"""Resumo semanal (Plus/Pro): payload tipado, adaptador de texto e texto livre.

Mora fora de `reports_daily` pelo teto de 350 linhas; `reports_daily` reexporta os
três nomes públicos. `_saldo_atual` é importado na função: `reports_daily` importa
este módulo no topo, então um import de módulo aqui fecharia o ciclo.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from core.observability import get_logger
from db import get_launches_by_period, get_summary_by_period, get_top_expense_categories, list_cards
from utils_date import _tz, now_tz

from .formatting import _fmt_brl, _fmt_pct, _fmt_sinal, finish_report

logger = get_logger(__name__)


def _semana_equivalente(today: date, closed: bool) -> tuple[tuple[date, date], tuple[date, date]]:
    """(período, anterior) da semana, ambos inclusivos e com o mesmo número de dias.

    closed=True: seg→dom da semana passada contra seg→dom da retrasada.
    closed=False: segunda desta semana até hoje contra a segunda da semana
    passada até o MESMO dia da semana (n dias contra n dias).
    """
    monday = today - timedelta(days=today.weekday())
    inicio, fim = (monday - timedelta(days=7), monday - timedelta(days=1)) if closed else (monday, today)
    ant_inicio = inicio - timedelta(days=7)
    return (inicio, fim), (ant_inicio, ant_inicio + (fim - inicio))


def _dec(v) -> Decimal:
    """float de um `sum(numeric)` → Decimal de 2 casas (convertido uma vez, na borda)."""
    return Decimal(str(round(float(v or 0), 2))).quantize(Decimal("0.01"))


def _pct(parte: Decimal, base: Decimal, casas: int) -> Decimal | None:
    """`parte/base` em %, calculado do valor cheio e arredondado UMA vez, direto à casa pedida
    (arredondar 11,46 → 11,5 → 12 é o erro que isto evita). Base zero: None."""
    if base == 0:
        return None
    return (parte / base * 100).quantize(Decimal(1).scaleb(-casas), ROUND_HALF_UP)


def _variacao(atual: Decimal, anterior: Decimal, com_pct: bool = True) -> dict:
    # Base zero: pct=None (sem divisão por zero e sem "+100%" inventado).
    return {"anterior": anterior, "delta": atual - anterior,
            **({"pct": _pct(atual - anterior, anterior, 1)} if com_pct else {})}


def _categoria_em_uma_linha(nome: str, limite: int = 40) -> str:
    """Só para apresentação: o nome é texto do usuário e vai dentro de uma variável de uma linha
    (sem quebra, sem marcação do WhatsApp, com teto)."""
    # Cf/Cc (ZWSP, BOM, RLO, ZWJ…) saem, menos o espaço em branco, que vira espaço no split.
    # ponytail: tirar o ZWJ quebra emoji composto (cosmético).
    sem_ctrl = "".join(c for c in (nome or "") if c.isspace() or unicodedata.category(c) not in ("Cf", "Cc"))
    t = " ".join(re.sub(r"[*_~`]", "", sem_ctrl).split()) or "outros"
    return t if len(t) <= limite else t[: limite - 1].rstrip() + "…"


def _totais_semana(user_id: int, inicio: date, fim: date) -> dict:
    from db.accounts import _TIPO_ALIASES

    resumo = get_summary_by_period(user_id, inicio, fim)
    tipos = _TIPO_ALIASES["despesa"] + _TIPO_ALIASES["receita"]
    # Conta o que entra nos totais (despesa/receita não interna), não toda linha da janela.
    n = sum(1 for r in get_launches_by_period(user_id, inicio, fim) or []
            if r["tipo"] in tipos and not r["is_internal_movement"])
    receita, despesa = _dec(resumo.get("receita")), _dec(resumo.get("despesa"))
    return {"receita": receita, "despesa": despesa, "resultado": receita - despesa, "n": n}


def _bancos(user_id: int, agora: datetime) -> tuple[str, datetime | None, int]:
    """(estado, menor `sincronizado_em` das conexões vivas, quantas CONEXÕES nunca sincronizaram), a
    partir do bloco de contas oficial. A contagem (e a presença de banco sem sync) vem das conexões e
    não das contas: uma conexão com 3 contas é 1 banco, e a nova sem conta ainda existe."""
    from db import contas_hoje
    from db.connection import get_conn
    from db.open_finance_state import _TERMINAL
    from db.patrimonio import ler_conexoes

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("set transaction isolation level repeatable read, read only")
        contas = contas_hoje.listar(cur, user_id, agora=agora)["contas"]
        nunca = sum(1 for c in ler_conexoes(cur, user_id)[0]
                    if c["last_sync_at"] is None and (c["status"] or "").upper() not in _TERMINAL)
        conn.rollback()
    vivas = [c for c in contas if "conexao_pausada" not in c["motivos"]]
    syncs = [c["sincronizado_em"] for c in vivas if c["sincronizado_em"]]
    if not syncs:
        # Conexão nova ainda sem linhas em open_finance_accounts não aparece em `contas`: vale a conexão.
        return ("nunca_sincronizado", None, nunca) if nunca else ("sem_banco", None, 0)
    velho = nunca or any("banco_desatualizado" in c["motivos"] for c in vivas)  # pendente = desatualizado
    return ("desatualizado" if velho else "atualizado"), min(syncs), nunca


def build_weekly_report_data(user_id: int, closed: bool = False) -> dict:
    """Payload tipado do resumo semanal (Decimal/date; nada aqui é texto).

    Despesa/receita seguem `get_summary_by_period` (sem cartão: divergência
    conhecida do Q18, `cartao_incluido=False`). `comparacao` só vem para quem tem
    `financial_comparison` (Plus+); senão `comparacao_motivo="plano"`.
    """
    from core.services.plan_service import history_earliest_date, plan_gate_ok

    from .reports_daily import _saldo_atual

    gerado_em = now_tz()
    (inicio, fim), (ant_inicio, ant_fim) = _semana_equivalente(gerado_em.date(), closed)
    atual = _totais_semana(user_id, inicio, fim)

    maior = None
    try:  # extra: o template não usa a maior categoria
        top = get_top_expense_categories(user_id, inicio, fim, limit=1, include_card=False)
        if top and atual["despesa"] > 0:
            total = _dec(top[0]["total"])
            maior = {"categoria": top[0]["categoria"], "total": total,
                     "pct_da_despesa": _pct(total, atual["despesa"], 1)}
    except Exception as exc:
        logger.warning("resumo semanal: maior categoria indisponível (%s)", type(exc).__name__)

    comparacao = motivo = None
    if not plan_gate_ok(user_id, "financial_comparison"):
        motivo = "plano"
    else:
        # Extra: se falhar, o resumo sai sem a linha (os totais do template não dependem disto).
        try:
            corte = history_earliest_date(user_id, gerado_em)
            if corte and corte > ant_inicio:
                motivo = "inicio_do_historico"
            else:
                ant = _totais_semana(user_id, ant_inicio, ant_fim)
                if ant["n"] == 0:
                    motivo = "sem_base"  # "gastei zero" e "ainda não usava o app" não se distinguem
                else:
                    comparacao = {"receita": _variacao(atual["receita"], ant["receita"]),
                                  "despesa": _variacao(atual["despesa"], ant["despesa"]),
                                  "resultado": _variacao(atual["resultado"], ant["resultado"], com_pct=False)}
        except Exception as exc:
            logger.warning("resumo semanal: comparação indisponível (%s)", type(exc).__name__)
            comparacao, motivo = None, "indisponivel"

    try:
        bancos, atualizado_em, sem_sync = _bancos(user_id, gerado_em)
    except Exception as exc:  # extra: a linha dos bancos some, o envio não cai
        logger.warning("resumo semanal: bancos indisponíveis (%s)", type(exc).__name__)
        bancos, atualizado_em, sem_sync = None, None, 0
    try:  # extra: só decide o aviso do cartão
        tem_cartao = bool(list_cards(user_id))
    except Exception as exc:
        logger.warning("resumo semanal: cartões indisponíveis (%s)", type(exc).__name__)
        tem_cartao = False
    return {
        "periodo": {"inicio": inicio, "fim": fim, "parcial": not closed},
        "anterior": {"inicio": ant_inicio, "fim": ant_fim},
        "gerado_em": gerado_em,
        "atualizado_em": atualizado_em.astimezone(_tz()) if atualizado_em else None,
        "bancos": bancos, "bancos_sem_sync": sem_sync,
        "receita": atual["receita"], "despesa": atual["despesa"], "resultado": atual["resultado"],
        "n_lancamentos": atual["n"],
        "maior_categoria": maior,
        "comparacao": comparacao, "comparacao_motivo": motivo,
        "saldo": _dec(_saldo_atual(user_id)),
        "cartao_incluido": False,
        "tem_cartao": tem_cartao,
    }


def _fmt_variacao(d: dict) -> str:
    """Uma linha só (variável nomeada da Meta não aceita quebra de linha)."""
    c = d["comparacao"]
    if c is None:
        return {"sem_base": "Sem lançamentos na semana anterior para comparar.",
                "inicio_do_historico": "Comparação indisponível: a semana anterior está fora do histórico do seu plano.",
                "indisponivel": "Comparação com a semana anterior indisponível no momento.",
                }.get(d["comparacao_motivo"], "")

    def parte(nome, atual, v):
        pct = _pct(atual - v["anterior"], v["anterior"], 0)   # do valor cheio, não do pct de 1 casa
        if pct is not None:
            return f"{nome} {_fmt_pct(pct)}"
        return f"{nome} novas ({_fmt_brl(atual)})" if atual > 0 else f"{nome} sem variação"

    a = d["anterior"]
    return (f"Contra {a['inicio']:%d/%m} a {a['fim']:%d/%m}: "
            f"{parte('despesas', d['despesa'], c['despesa'])}, {parte('receitas', d['receita'], c['receita'])}, "
            f"resultado {_fmt_sinal(c['resultado']['delta'])}")


def build_weekly_report_summary(user_id: int, closed: bool = False) -> dict[str, str]:
    """Resumo semanal em texto, em cima de `build_weekly_report_data`.

    closed=False (sob demanda): semana atual, de segunda até hoje.
    closed=True  (agendado na segunda): semana anterior completa (seg → dom).

    As 6 chaves legadas (`start, end, saldo, gastos, receita, lancamentos`) são o
    contrato do template homologado da Meta e não mudam; as demais só alimentam o
    texto livre. Todo valor é de uma linha.
    """
    d = build_weekly_report_data(user_id, closed=closed)
    p, m = d["periodo"], d["maior_categoria"]
    atualizado = d["atualizado_em"]
    quando = f"{atualizado:%d/%m} às {atualizado:%H:%M}" if atualizado else ""
    sem_sync = d["bancos_sem_sync"]
    return {
        "start": p["inicio"].strftime("%d/%m/%Y"),
        "end": p["fim"].strftime("%d/%m/%Y"),
        "saldo": _fmt_brl(d["saldo"]),
        "gastos": _fmt_brl(d["despesa"]),
        "receita": _fmt_brl(d["receita"]),
        "lancamentos": str(d["n_lancamentos"]),
        "periodo": (f"Segunda-feira, {p['fim']:%d/%m/%Y} (parcial)" if p["parcial"] and p["inicio"] == p["fim"]
                    else f"Semana em andamento: {p['inicio']:%d/%m} a {p['fim']:%d/%m/%Y}" if p["parcial"]
                    else f"Semana de {p['inicio']:%d/%m} a {p['fim']:%d/%m/%Y} (seg a dom)"),
        "atualizado_em": {
            "atualizado": f"Bancos atualizados em {quando}",
            "desatualizado": f"Bancos desatualizados: última atualização em {quando}"
                             + (f" e {sem_sync} banco(s) ainda não sincronizado(s)" if sem_sync else ""),
            "nunca_sincronizado": "Bancos conectados ainda não sincronizaram",
        }.get(d["bancos"], ""),
        "resultado": _fmt_sinal(d["resultado"]),
        "maior_categoria": (f"{_categoria_em_uma_linha(m['categoria'])}, {_fmt_brl(m['total'])} "
                            f"({_fmt_pct(_pct(m['total'], d['despesa'], 0), sinal=False)} das despesas)" if m else ""),
        "variacao": _fmt_variacao(d),
        "aviso_cartao": "Compras no cartão ficam na fatura e não entram neste resumo." if d["tem_cartao"] else "",
    }


def build_weekly_report_text(user_id: int, closed: bool = False) -> str:
    s = build_weekly_report_summary(user_id, closed=closed)

    lines = ["📊 *Resumo semanal do Bot Financeiro*", f"📅 {s['periodo']}"]
    if s["atualizado_em"]:
        lines.append(f"🔄 {s['atualizado_em']}")
    lines += [
        "",
        f"📈 Receitas: {s['receita']}",
        f"📉 Despesas: {s['gastos']}",
        f"✅ Resultado da semana: {s['resultado']} (receitas - despesas)",
    ]
    if s["maior_categoria"]:
        lines.append(f"🏷️ Maior categoria: {s['maior_categoria']}")
    if s["variacao"]:
        lines.append(f"↔️ {s['variacao']}")
    lines += [
        f"🧾 Lançamentos: {s['lancamentos']}",
        "",
        f"🏦 Saldo atual nas contas: {s['saldo']} (é o que você tem hoje, não o resultado da semana)",
    ]
    if s["aviso_cartao"]:
        lines.append(f"ℹ️ {s['aviso_cartao']}")

    return finish_report(user_id, lines)
