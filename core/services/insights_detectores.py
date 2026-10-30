"""Detectores de insights do PL-06 que nasceram depois do A1 (`db/insights.py` não cresce; contrato em
`core/services/insights_contrato.py`). Os imports do pacote `db` são tardios: `db/insights.py` importa este
módulo dentro de `compute_active_insights`, e o import no topo fecharia o ciclo."""
from __future__ import annotations

from typing import Any

from core.services.insights_contrato import (
    RECURSO_POR_TIPO, dinheiro, montar_item, navegar, nome, periodo_dia, periodo_mes,
)
from utils_date import day_tz, now_tz
from utils_text import fmt_brl

JANELA = 7     # dias antes do vencimento em que a conta entra (info até D-4)
ALERTA = 3     # de D-3 a D-1: warning; D-0: critical
N_VENCIDA = 7  # P1 do dono (2026-10-28): a vencida aparece de D+1 a D+7, critical
# P6 da B3: a tela do botão ("Ver gastos fixos") calcula outra coisa; a ressalva nunca sai do texto por tamanho.
NOTA_GASTOS_FIXOS = "A Previsão mensal de Gastos fixos considera só recorrências e pode mostrar outro valor."


def _textos(nm: str, due, dias: int, valor, variavel: bool) -> tuple[str, str]:
    v = "valor a confirmar" if valor is None else f"cerca de {fmt_brl(valor)}" if variavel else fmt_brl(valor)
    quando = ("vence hoje" if dias == 0 else "vence amanhã" if dias == 1 else f"vence em {dias} dias" if dias > 0
              else "venceu ontem" if dias == -1 else f"venceu há {-dias} dias")
    dia = due.strftime("%d/%m")
    inicio = (f"Vencimento hoje, {dia}." if dias == 0 else f"Vencimento em {dia}." if dias > 0
              else f"Venceu em {dia} e continua em aberto.")
    return f"{nm} {quando} ({v})", f"{inicio} Se já pagou, marque como paga em Contas a pagar."


def detect_bill_due(user_id: int) -> list[dict[str, Any]]:
    """Conta a pagar pendente (`bill_instances`, pela leitura da agenda e da previsão, `db.bills.ler_instancias`,
    que filtra `user_id` e casa o recorrente do mesmo usuário): info de D-7 a D-4, warning de D-3 a D-1, critical
    em D-0 e vencida (D+1 a D+N_VENCIDA, uma por recorrente: a mais recente). Paga ou apagada sai da leitura.
    Chave `bill_due:id:vencimento` (vencida: `id:vencida`), então a dispensa volta só quando a faixa piora (D4).
    Fora: faturas de cartão, gastos fixos automáticos e o envio. O Carteiro avisa a mesma conta no feed de agentes:
    a duplicidade no app é aceita (Q2d). Sem plano de gastos fixos (`recurring_expenses`, T1) não há aviso: na geração
    e de novo na leitura do cache (`insights_contrato.RECURSO_POR_TIPO`), para quem perde o plano dentro do TTL."""
    from core.services.plan_service import plan_gate_ok
    from db.bills import ler_instancias
    from db.connection import get_conn
    if not plan_gate_ok(user_id, RECURSO_POR_TIPO["bill_due"]):  # poupa a consulta; a leitura do cache confere de novo
        return []
    hoje = day_tz(now_tz())
    with get_conn() as conn, conn.cursor() as cur:
        contas = ler_instancias(cur, user_id, include_paid=False)
    out: list[dict[str, Any]] = []
    vencidas: set[int] = set()
    # Prazo de horas primeiro (P3): as que ainda vencem, da mais próxima à mais longe; depois as vencidas, da mais
    # recente à mais antiga (que é também a que ganha no recorrente); `id` desempata. A gravidade segue no sort global.
    for c in sorted(contas, key=lambda c: (c["due_date"] < hoje, abs((c["due_date"] - hoje).days), c["id"])):
        due, dias = c["due_date"], (c["due_date"] - hoje).days
        if not -N_VENCIDA <= dias <= JANELA:
            continue
        vencida = dias < 0
        if vencida and c["recurring_id"] is not None:
            if c["recurring_id"] in vencidas:
                continue
            vencidas.add(c["recurring_id"])
        conta = nome(c["name"], reserva="Conta")  # sem nome, ou só marcação
        nm = conta[:1].upper() + conta[1:]  # `capitalize()` estragaria "CEMIG"
        valor = dinheiro(c["amount"]) if c["amount"] and c["amount"] > 0 else None  # variável sem estimativa = 0
        title, message = _textos(nm, due, dias, valor, bool(c["variable_amount"]))
        out.append(montar_item(
            "bill_due", f"{c['id']}:vencida" if vencida else c["id"], periodo_dia(due),
            severity="critical" if dias <= 0 else "warning" if dias <= ALERTA else "info",
            icon="⚠️" if vencida else "🧾", title=title, message=message,
            action_label="Ver contas a pagar", action_view="fixed",
            referencia={"estatistica": "vencimento", "conta": conta, "vencimento": due,
                        "valor": valor, "variavel": bool(c["variable_amount"])},
            atual={"dias_para_vencer": dias}, diferenca=None, amostra=None,
            acoes=[navegar("Ver contas a pagar", "fixed", **({"conta_valor": valor} if valor else {}))]))
    return out


def _textos_mes(per: dict, fecha, saldo) -> tuple[str, str, str]:
    """(severidade, título, corpo) do `month_projection`: P3 (saldo de hoje < 0 = critical) e P6 da B3."""
    prev = (f"Previsão para {per['fim']:%d/%m}: {fmt_brl(-fecha)} no negativo, sem contar os gastos do dia a dia. "
            f"{NOTA_GASTOS_FIXOS}")
    if saldo < 0:
        return "critical", "Seu saldo está no vermelho", f"Seu saldo já está {fmt_brl(-saldo)} no negativo. {prev}"
    mes = per["rotulo"].split(" de ")[0]
    return "warning", f"{mes[:1].upper()}{mes[1:]} pode fechar no vermelho", prev


def detect_month_projection(user_id: int) -> list[dict[str, Any]]:
    """O mês corrente fecha negativo no pior caso do SIMULADOR (`veredito.py`), lido no último dia do mês e nunca no
    pior dia: a mesma snapshot, a mesma janela de conferência (receita fixa não conferida dos últimos
    `JANELA_CONFERENCIA_DIAS` dias conta a favor; antes dela, "já aconteceu"), sem a estimativa do dia a dia nem as
    saídas duvidosas (`cashflow.fechamento_pior_caso`).
    warning com o saldo de hoje >= 0, critical com ele < 0; fechamento >= 0 não avisa. Calado onde o simulador não
    conclui: banco conectado, fixo sem data, valor estimado, pedido pendente no chat. A Previsão mensal de Gastos fixos
    soma só recorrências e pode discordar: o texto avisa (`NOTA_GASTOS_FIXOS`). O chat (`check_cashflow`, sem a janela)
    pode se abster no mesmo caso (P7, issue #1326). Gate `forecast` (Plus) aqui e na leitura (`RECURSO_POR_TIPO`).
    Só para quem já informou o saldo (`carteira_qualidade.saldo_informado`, emenda 1): a Carteira nasce em 0, e o
    primeiro gasto de quem nunca a informou não é "saldo no vermelho"."""
    from core.services.cashflow import fechamento_pior_caso, premissa_da_janela
    from core.services.cashflow_snapshot import carregar
    from core.services.plan_service import plan_gate_ok
    from db.carteira_qualidade import saldo_informado
    from db.connection import get_conn
    if not plan_gate_ok(user_id, RECURSO_POR_TIPO["month_projection"]):  # poupa a snapshot; a leitura confere de novo
        return []
    with get_conn() as conn, conn.cursor() as cur:
        if not saldo_informado(cur, user_id):  # antes do `carregar`, que é a parte cara
            return []
    hoje = day_tz(now_tz())
    per = periodo_mes(hoje)
    snap = carregar(user_id, hoje, per["fim"])
    fecha = fechamento_pior_caso(snap, per["fim"])
    if fecha is None or fecha >= 0:
        return []
    saldo = dinheiro(snap.base["saldo"])
    severity, title, message = _textos_mes(per, fecha, saldo)
    return [montar_item(
        "month_projection", "saldo", per, severity=severity, icon="📉", title=title, message=message,
        action_label="Ver gastos fixos", action_view="fixed",
        referencia={"estatistica": "previsao_pior_caso", "data_alvo": per["fim"], "saldo_hoje_valor": saldo,
                    "premissa": premissa_da_janela(snap)},
        atual={"valor": fecha}, diferenca=None, amostra=None, impacto={"falta_valor": -fecha})]
