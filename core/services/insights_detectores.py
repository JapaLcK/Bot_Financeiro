"""Detectores de insights do PL-06 que nasceram depois do A1 (`db/insights.py` não cresce; contrato em
`core/services/insights_contrato.py`). Os imports do pacote `db` são tardios: `db/insights.py` importa este
módulo dentro de `compute_active_insights`, e o import no topo fecharia o ciclo."""
from __future__ import annotations

from typing import Any

from core.services.insights_contrato import dinheiro, montar_item, navegar, nome, periodo_dia
from utils_date import day_tz, now_tz
from utils_text import fmt_brl

JANELA = 7     # dias antes do vencimento em que a conta entra (info até D-4)
ALERTA = 3     # de D-3 a D-1: warning; D-0: critical
N_VENCIDA = 7  # P1 do dono (2026-10-28): a vencida aparece de D+1 a D+7, critical


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
    a duplicidade no app é aceita (Q2d). Sem plano de gastos fixos (`recurring_expenses`, T1) não há aviso."""
    from core.services.plan_service import plan_gate_ok
    from db.bills import ler_instancias
    from db.connection import get_conn
    if not plan_gate_ok(user_id, "recurring_expenses"):
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
