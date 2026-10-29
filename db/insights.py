"""
db/insights.py — Insights proativos do Piggy (Sprint 7).

Detecção de padrões/anomalias relevantes pro user, calculada ON-DEMAND
(sem cron). O frontend chama o endpoint e recebe a lista fresca a cada refresh.
O "dispensado"/"útil" persistido mora em `insight_estado` (`db/insight_estado.py`);
o X do painel antigo segue no localStorage (TTL 24h) até o PR C do PL-06.

Cada detector devolve itens do contrato v1 (`core/services/insights_contrato.py`):
os campos legados (`key`, `title`, `message`, `severity`, `action_*`, `icon`) e a
`explicacao` com período, fonte, referência e números calculados aqui.

Tom: amigo, sem julgar, anti-fricção. Mascote: Piggy 🐷.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from core.services.insights_contrato import (
    GRAVIDADE, dinheiro, diferenca, montar_item, navegar, nome, periodo_dia, periodo_mes, sujeito,
)
from utils_date import day_tz, now_tz
from utils_text import fmt_brl, merchant_key

from .budgets import get_budgets_status_for_month
from .connection import CAT_VAZIA_LABEL, TIPO_DESPESA_SQL, TIPO_RECEITA_SQL, cat_key, get_conn
from .recurring import list_recurring_expenses
from .users import ensure_user

logger = logging.getLogger(__name__)

# category_spike: anterior abaixo disso não é base (nem "+567%" sobre centavos); o atual, com +30%, já passa de 65.
PISO_ANTERIOR = Decimal(50)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de mês
# ─────────────────────────────────────────────────────────────────────────────

def _days_in_month(d: date) -> int:
    if d.month == 12:
        nxt = date(d.year + 1, 1, 1)
    else:
        nxt = date(d.year, d.month + 1, 1)
    return (nxt - date(d.year, d.month, 1)).days


def _month_progress_pct(today: date | None = None) -> float:
    """Quanto do mês corrente já passou (0–100)."""
    today = today or date.today()
    total = _days_in_month(today)
    return (today.day / total) * 100.0


def _prev_n_months(today: date, n: int) -> list[tuple[int, int]]:
    """Retorna lista de (year, month) dos N meses anteriores (não inclui o atual).

    Ex: today=2026-05-17, n=3 → [(2026,4), (2026,3), (2026,2)]
    """
    out: list[tuple[int, int]] = []
    y, m = today.year, today.month
    for _ in range(n):
        m -= 1
        if m == 0:
            m = 12
            y -= 1
        out.append((y, m))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Detectores individuais
# ─────────────────────────────────────────────────────────────────────────────

def _detect_budget_warnings(user_id: int) -> list[dict[str, Any]]:
    """Categorias com orçamento em zona amarela (80-99%) ou vermelha (>=100%)."""
    out: list[dict[str, Any]] = []
    today = date.today()
    days_left = max(0, _days_in_month(today) - today.day)

    status = get_budgets_status_for_month(user_id)

    for b in status.get("budgets", []):
        st = b.get("status")
        if st == "verde":
            continue
        pct = b.get("pct") or 0.0
        cat = b.get("categoria") or "categoria"
        emoji = b.get("emoji") or "🏷️"

        if st == "vermelho":
            severity = "critical"
            title = f"{cat.capitalize()} estourou o orçamento"
            msg = (
                f"{pct:.0f}% do limite consumido — "
                f"{'já passou' if pct >= 100 else 'tá no limite'}. "
                f"Faltam {days_left} dias no mês."
            )
        else:  # amarelo
            severity = "warning"
            title = f"{cat.capitalize()} tá apertando"
            msg = f"{pct:.0f}% do orçamento de {cat.lower()} e faltam {days_left} dias."

        gasto, limite = dinheiro(b.get("spent") or 0), dinheiro(b.get("budget") or 0)
        out.append(montar_item(
            "budget_warning", sujeito(cat, cat_key(cat)), periodo_mes(today), title=title, message=msg, severity=severity,
            icon=emoji, action_label="Ajustar", action_view="budgets",
            referencia={"estatistica": "orcamento_do_mes", "categoria": nome(cat), "valor": limite},
            atual={"valor": gasto, "percentual": pct}, diferenca=diferenca(gasto, limite),
            impacto={"tipo": "excesso" if gasto > limite else "restante", "valor": abs(limite - gasto)},
            acoes=[navegar(f"Ajustar orçamento de {nome(cat)}", "budgets", orcamento_valor=limite,
                           gasto_valor=gasto)]))
    return out


def _detect_category_spike(user_id: int) -> list[dict[str, Any]]:
    """Categoria cujo mês corrente INTEIRO (o que já está lançado, como o Resumo do mês) passou o anterior inteiro
    em 30% (`warning`) ou 60% (`critical`), o anterior >= R$ 50: a regra P1-B (`comparacao_periodos.escolher_janelas`
    e `resumo_mes.categorias`, cartão pela fatura). Sem o portão de 40% do mês: o parcial que já passou o anterior
    só cresce. "Sem categoria" fica fora; o plano que não enxerga o anterior inteiro não vê (corte do histórico).
    Fica de fora o `motivos` do Resumo ("número pode estar incompleto")."""
    from core.reports.weekly import _pct
    from core.services.comparacao_periodos import escolher_janelas
    from core.services.plan_service import history_earliest_date
    from .resumo_mes import categorias
    agora = now_tz()
    hoje = day_tz(agora)
    j = escolher_janelas(hoje, hoje.year, hoje.month)
    a, p = j["atual"], j["anterior"]
    corte = history_earliest_date(user_id, agora)
    if corte and corte > p["inicio"]:
        return []
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("set transaction isolation level repeatable read, read only")
        atuais = categorias(cur, user_id, a["inicio"], a["fim"] + timedelta(days=1))
        anteriores = {r["chave"]: r["total"] for r in categorias(cur, user_id, p["inicio"], p["fim"] + timedelta(days=1))}
        emojis: dict[str, str] = {}
        try:  # a última consulta da conexão (só leitura): falha aqui não estraga nada lido antes
            cur.execute("select name, emoji from user_categories where user_id=%s", (user_id,))
            emojis = {r["name"]: r["emoji"] for r in cur.fetchall() if r["emoji"]}
        except Exception:
            pass

    rot_ant = periodo_mes(p["inicio"])["rotulo"]
    out: list[dict[str, Any]] = []
    for r in atuais:
        chave, label = r["chave"], r["categoria"]
        atual, ant = dinheiro(r["total"]), dinheiro(anteriores.get(chave, 0))
        if chave == cat_key(CAT_VAZIA_LABEL) or ant < PISO_ANTERIOR or atual * 10 < ant * 13:
            continue
        out.append(montar_item(
            # A chave vem do SQL (`cat_key_sql`, a mesma da main): recalcular do rótulo sanitizado mudaria a chave.
            "category_spike", sujeito(label, chave), periodo_mes(hoje),
            severity="critical" if atual * 10 >= ant * 16 else "warning",
            title=f"{label.capitalize()} subiu {_pct(atual - ant, ant, 0)}% no mês",
            message=f"{fmt_brl(atual)} até agora contra {fmt_brl(ant)} em {rot_ant} inteiro. Vale conferir.",
            action_label="Ver análises", action_view="analytics", icon=emojis.get(label.lower(), "📈"),
            referencia={"estatistica": "mes_anterior_inteiro", "categoria": nome(label), "valor": ant,
                        "periodo": {**periodo_mes(p["inicio"]), "parcial": False}},
            atual={"valor": atual}, diferenca=diferenca(atual, ant), amostra=None))
    return out


def _detect_recurring_increase(user_id: int) -> list[dict[str, Any]]:
    """Recorrentes cujo `amount` foi reajustado nos últimos 60 dias."""
    today = date.today()
    cutoff = today - timedelta(days=60)
    out: list[dict[str, Any]] = []
    recs = list_recurring_expenses(user_id, include_inactive=False)

    for r in recs:
        if not r.get("last_amount") or not r.get("last_amount_changed_at"):
            continue
        try:
            changed_at = date.fromisoformat(r["last_amount_changed_at"][:10])
        except (TypeError, ValueError):
            continue
        if changed_at < cutoff:
            continue
        old = float(r["last_amount"])
        new = float(r["amount"])
        if old <= 0 or abs(new - old) < 0.01:
            continue
        delta = new - old
        delta_pct = (delta / old) * 100.0
        if abs(delta_pct) < 1.0:
            continue
        signal = "subiu" if delta > 0 else "caiu"
        freq = r.get("frequency") or "monthly"
        # Impacto e ação (Pro) só onde a conta por ano é exata: mensal x12, anual x1.
        vezes = {"monthly": 12, "annual": 1}.get(freq)
        out.append(montar_item(
            "recurring_increase", r["id"], periodo_dia(changed_at),
            severity="warning" if delta > 0 and abs(delta_pct) >= 10 else "info",
            title=f"{r['name']} {signal} {abs(delta_pct):.0f}%",
            message=(
                f"De {fmt_brl(old)} pra {fmt_brl(new)} "
                f"(Δ {'+' if delta >= 0 else '-'}{fmt_brl(abs(delta))}). "
                f"Reajuste em {changed_at.strftime('%d/%m')}."
            ),
            action_label="Ver gastos fixos", action_view="fixed", icon="📈" if delta > 0 else "📉",
            referencia={"estatistica": "valor_anterior", "recorrente": nome(r["name"]),
                        "valor": dinheiro(old), "frequencia": freq},
            atual={"valor": dinheiro(new), "data": changed_at}, diferenca=diferenca(new, old),
            impacto=vezes and {"por_ano_valor": (dinheiro(new) - dinheiro(old)) * vezes},
            acoes=vezes and [navegar(f"Revisar {nome(r['name'])}", "fixed",
                                     custo_por_ano_valor=dinheiro(new) * vezes)]))
    return out


def _detect_salary_burn_fast(user_id: int) -> list[dict[str, Any]]:
    """Despesas do mês > % do mês decorrido + 15pp vs receita esperada.

    Usa a média de receita dos meses anteriores presentes na janela de 4 meses da
    query como proxy de "salário esperado" (não dá pra confiar só na receita do
    mês corrente — pode ainda não ter caído). Ver a nota na query abaixo: o
    `_prev_n_months(today, 3)` não recorta essa janela.

    Ex: dia 10 (33% do mês), gastou 50% da receita esperada → alerta.
    """
    today = date.today()
    progress = _month_progress_pct(today)
    if progress < 25.0:  # cedo demais, sem signal
        return []
    if progress > 90.0:  # tarde demais, alerta perde utilidade
        return []

    prev_months = _prev_n_months(today, 3)
    if len(prev_months) < 2:
        return []

    # Receita média mensal dos meses anteriores presentes na janela: a query pega
    # 4 meses (`- interval '4 months'` até o 1º do mês corrente), não 3. O
    # `_prev_n_months(today, 3)` acima só alimenta o guard `len < 2`, que com n=3
    # nunca dispara — divergência PREEXISTENTE, não mexida neste PR (mudar a
    # janela mudaria número em produção).
    # A forma legada 'entrada' conta: sem ela o `expected_income` sai menor (ou os
    # meses reconhecidos caem abaixo de 2 e o alerta some), enquanto o endpoint de
    # patterns reporta a renda certa — o mesmo painel, dois números.
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                select date_part('year', criado_em)::int as y,
                       date_part('month', criado_em)::int as m,
                       sum(valor)::float as total
                from launches
                where user_id=%s
                  and {TIPO_RECEITA_SQL}
                  and is_internal_movement = false
                  and criado_em >= make_date(%s, %s, 1) - interval '4 months'
                  and criado_em <  make_date(%s, %s, 1)
                group by 1, 2
                """,
                (
                    user_id,
                    today.year, today.month,
                    today.year, today.month,
                ),
            )
            rows = cur.fetchall() or []

    incomes = [float(r["total"] or 0) for r in rows if float(r["total"] or 0) > 0]
    if len(incomes) < 2:
        return []
    expected_income = sum(incomes) / len(incomes)
    if expected_income < 500:  # user com pouca receita: skip alert (ruidoso)
        return []

    # Despesas no mês corrente (launches + credit_transactions)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                select
                  coalesce((
                    select sum(valor) from launches
                    where user_id=%s
                      and {TIPO_DESPESA_SQL}
                      and is_internal_movement = false
                      and date_part('year',  criado_em) = %s
                      and date_part('month', criado_em) = %s
                  ), 0) +
                  coalesce((
                    select sum(ct.valor) from credit_transactions ct
                    join credit_bills b on b.id = ct.bill_id and b.user_id = ct.user_id
                    where ct.user_id=%s
                      and ct.is_refund = false
                      and date_part('year',  b.period_end) = %s
                      and date_part('month', b.period_end) = %s
                  ), 0) as total
                """,
                (
                    user_id, today.year, today.month,
                    user_id, today.year, today.month,
                ),
            )
            row = cur.fetchone()
            current_expense = float(row["total"] or 0)

    if current_expense <= 0:
        return []

    expense_pct = (current_expense / expected_income) * 100.0
    # Gap em pontos percentuais entre o que gastou e quanto do mês passou
    gap = expense_pct - progress
    if gap < 15.0:
        return []

    if gap >= 35.0:
        severity = "critical"
        title = "Seu mês tá queimando rápido 🔥"
    else:
        severity = "warning"
        title = "Tá gastando mais rápido que o normal"

    msg = (
        f"Já consumiu {expense_pct:.0f}% da sua receita média mensal "
        f"e só {progress:.0f}% do mês passou. Vale dar uma freada."
    )
    return [montar_item(
        "salary_burn_fast", "receita", periodo_mes(today), title=title, message=msg, severity=severity,
        icon="🔥", action_label="Ver análises", action_view="analytics",
        referencia={"estatistica": "receita_media_mensal", "valor": dinheiro(expected_income)},
        atual={"despesa_valor": dinheiro(current_expense), "percentual_da_receita": round(expense_pct, 1),
               "percentual_do_mes": round(progress, 1)},
        amostra={"suficiente": True, "meses_com_receita": len(incomes), "minimo_meses": 2})]


def _detect_goals_behind(user_id: int) -> list[dict[str, Any]]:
    """Metas com `indicator='behind'` (ritmo atual não atinge o prazo).

    Reusa a lógica do endpoint /goals/{uid}/status. Pra evitar duplicar
    código aqui, replica só o essencial: pega caixinhas com target_amount
    + target_date e calcula ritmo dos últimos 90d.
    """
    from .pockets import list_pockets

    pockets = list_pockets(user_id)

    out: list[dict[str, Any]] = []
    today = date.today()
    for p in pockets:
        ta = p.get("target_amount")
        td = p.get("target_date")
        if ta is None or td is None:
            continue
        saved = float(p.get("balance") or 0)
        tgt = float(ta)
        if tgt <= 0:
            continue
        pct = (saved / tgt * 100.0)
        if pct >= 100:
            continue
        days_left = (td - today).days
        if days_left <= 0:
            # Prazo passou, meta não cumprida
            out.append(montar_item(
                "goal_behind", f"{p['id']}:vencida", periodo_mes(today), severity="critical",
                title=f"Meta '{p['name']}' venceu",
                message=(f"Prazo era {td.strftime('%d/%m/%Y')} e ainda faltam "
                         f"{fmt_brl(tgt - saved)}. Vale rever o prazo."),
                action_label="Ver metas", action_view="goals", icon=p.get("emoji") or "🎯",
                referencia={"estatistica": "meta_com_prazo", "caixinha": nome(p["name"]),
                            "meta_valor": dinheiro(tgt), "prazo": td},
                atual={"saldo_valor": dinheiro(saved), "falta_valor": dinheiro(tgt - saved)}))
            continue

        remaining = tgt - saved
        months_left = max(1, days_left / 30.0)
        needed = remaining / months_left

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select
                      coalesce(sum(case when tipo = 'deposito_caixinha' then valor else 0 end), 0) -
                      coalesce(sum(case when tipo = 'saque_caixinha' then valor else 0 end), 0)
                      as net
                    from launches
                    where user_id=%s and alvo=%s
                      and criado_em >= now() - interval '90 days'
                    """,
                    (user_id, p["name"]),
                )
                r = cur.fetchone()
                current_pace = float(r["net"] or 0) / 3.0

        # behind = ritmo < 50% do necessário (consistente com /goals/status)
        if needed > 0 and current_pace < needed * 0.5:
            shortfall_pct = (1.0 - (current_pace / needed)) * 100.0 if needed > 0 else 100.0
            out.append(montar_item(
                "goal_behind", f"{p['id']}:atrasada", periodo_mes(today),
                severity="warning" if shortfall_pct < 80 else "critical",
                title=f"Meta '{p['name']}' atrasada",
                message=(f"Pra bater o prazo precisaria de {fmt_brl(needed)}/mês, "
                         f"mas o ritmo atual tá em {fmt_brl(max(0, current_pace))}/mês."),
                action_label="Ver metas", action_view="goals", icon=p.get("emoji") or "🎯",
                referencia={"estatistica": "ritmo_mensal_necessario", "caixinha": nome(p["name"]),
                            "valor": dinheiro(needed), "meta_valor": dinheiro(tgt), "prazo": td},
                atual={"valor": dinheiro(max(0, current_pace)), "saldo_valor": dinheiro(saved),
                       "janela": {"dias": 90}},
                diferenca=diferenca(max(0, current_pace), needed)))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Função pública
# ─────────────────────────────────────────────────────────────────────────────

def _detect_of_bill_increase(user_id: int) -> list[dict[str, Any]]:
    """Contas que aumentaram, detectadas via Open Finance (Fase 6) — pega até conta que o
    usuário nem cadastrou como recorrente. Complementa `_detect_recurring_increase`."""
    from db import detect_open_finance_bill_increase
    flags = detect_open_finance_bill_increase(user_id)
    out: list[dict[str, Any]] = []
    for f in flags:
        old = float(f["old"])
        new = float(f["new"])
        pct = float(f["pct"])
        # Chave pelo mês da cobrança: outro valor no mesmo mês é piora (ou melhora) do mesmo fato.
        out.append(montar_item(
            "of_bill_increase", sujeito(f["merchant"], merchant_key(f["merchant"])), periodo_dia(f["date"]),
            chave_periodo=f"{f['date']:%Y-%m}",
            severity="warning" if pct >= 15 else "info", title=f"{f['merchant']} subiu {pct:.0f}%",
            message=f"De {fmt_brl(old)} pra {fmt_brl(new)} — detectado pelo Open Finance.",
            # Sem botão, como em produção (o sanitizador antigo anulava `history`); voltar com ele é do dono.
            action_label=None, action_view=None, icon="📈",
            referencia={"estatistica": "media_meses_anteriores", "estabelecimento": nome(f["merchant"]),
                        "valor": dinheiro(old), "meses": f["meses_anteriores"]},
            atual={"valor": dinheiro(new), "data": f["date"]}, diferenca=diferenca(f["new"], f["old"])))
    return out


def compute_active_insights(user_id: int) -> tuple[list[dict[str, Any]], list[str]]:
    """(insights do user ordenados por severidade, nomes dos detectores que falharam).

    Detector que quebra não derruba os outros: os itens deles saem, e o nome dele vai em `falhas`.
    """
    from core.services.insights_detectores import detect_bill_due
    ensure_user(user_id)
    out: list[dict[str, Any]] = []
    falhas: list[str] = []
    for detector in (
        detect_bill_due,  # primeiro: no empate de gravidade a conta (prazo de horas) vem antes (P3)
        _detect_budget_warnings,
        _detect_recurring_increase,
        _detect_of_bill_increase,
        _detect_category_spike,
        _detect_salary_burn_fast,
        _detect_goals_behind,
    ):
        try:
            out.extend(detector(user_id))
        except Exception as exc:
            # Detector quebrado não impede o resto, mas entra em `falhas`: quem chama não pode ler a lista
            # incompleta como "nada a apontar". O log leva só o nome e a classe do erro (sem dado pessoal).
            logger.warning("insights: %s falhou (%s)", detector.__name__, type(exc).__name__)
            falhas.append(detector.__name__)

    out.sort(key=lambda i: -GRAVIDADE.get(i.get("severity"), -99))  # critical > warning > info; desconhecida no fim
    return out, falhas


def motivo_sem_insights(user_id: int) -> str:
    """Por que a lista saiu vazia: `sem_dados` (nada financeiro) ou `sem_destaques` (há dados, nada a apontar)."""
    from .users import _tem_dados_financeiros
    with get_conn() as conn, conn.cursor() as cur:
        return "sem_destaques" if _tem_dados_financeiros(cur, user_id) else "sem_dados"


__all__ = ["compute_active_insights", "motivo_sem_insights"]
