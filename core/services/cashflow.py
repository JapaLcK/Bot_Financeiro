"""
core/services/cashflow.py — projeção simples de caixa pra decisão de prazo.

Responde a pergunta do dono da farmácia: "se eu aceitar pagar até o dia X
(prazo do representante), eu fico tranquilo ou aperta?"

projetado(D) = saldo_atual
             + receitas fixas previstas em (hoje, D]
             − gastos fixos automáticos (mensal/anual) em (hoje, D]
             − boletos pendentes com vencimento até D
             − (opcional) um boleto novo que ele está considerando

`tranquilo` = projetado >= 0. É uma estimativa: não conta gastos avulsos futuros
nem recorrentes semanais/diários/únicos (raros); a ideia é dar visão de fôlego,
não fechamento contábil.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Any


def _as_date(v: Any) -> date | None:
    if v is None:
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except (ValueError, TypeError):
        return None


def _recurring_value_in_window(day: Any, freq: str, month: Any, start: date | None,
                               amount: float, after: date, until: date) -> float:
    """Soma o valor das ocorrências de um recorrente MENSAL/ANUAL em (after, until]."""
    if until <= after or amount <= 0:
        return 0.0
    try:
        day = int(day or 1)
    except (TypeError, ValueError):
        day = 1
    mnum = None
    if freq == "annual":
        try:
            mnum = int(month)
        except (TypeError, ValueError):
            return 0.0
    total = 0.0
    y, m = after.year, after.month
    while (y, m) <= (until.year, until.month):
        if not (freq == "annual" and mnum and m != mnum):
            dim = calendar.monthrange(y, m)[1]
            d = date(y, m, min(day, dim))
            if after < d <= until and (start is None or d >= start):
                total += amount
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return total


def _open_card_bills_due(user_id: int, until: date) -> float:
    """Saldo a pagar (total − pago) das faturas de cartão com vencimento até
    `until` — compromissos que o saldo em conta ainda não reflete (dívida de
    cartão não sai do saldo). Inclui 'open' (fatura corrente) e 'closed' com saldo
    (atrasada, ainda a pagar), mesmo critério de `list_bills_with_debt`: o
    atrasado também sai do caixa antes do alvo, então conta como saída."""
    from db.connection import get_conn
    from db.cards import card_bill_due_date

    total = 0.0
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select (b.total - coalesce(b.paid_amount, 0)) as remaining,
                       b.period_end, c.closing_day, c.due_day
                from credit_bills b
                join credit_cards c on c.id = b.card_id
                where b.user_id = %s and b.status in ('open', 'closed')
                  and b.total > coalesce(b.paid_amount, 0)
                """,
                (user_id,),
            )
            for row in cur.fetchall() or []:
                pe = _as_date(row["period_end"])
                if pe is None:
                    continue
                due = card_bill_due_date(pe, int(row["closing_day"] or 1), int(row["due_day"] or 1))
                if due <= until:
                    total += float(row["remaining"] or 0)
    return total


def project(user_id: int, target_date: date, extra_amount: float = 0.0) -> dict[str, Any]:
    """Projeção de caixa até `target_date`, opcionalmente considerando um boleto
    novo de `extra_amount`. Ver docstring do módulo."""
    from db.accounts import get_balance
    from db.recurring import list_recurring_expenses
    from db.recurring_income import list_recurring_incomes
    from db.bills import list_bills

    today = date.today()

    # Saldo de partida: consolidado (carteira + bancos autorizados no Open Finance)
    # quando o usuário tem banco conectado e o consolidado está liberado — senão a
    # projeção de quem tem OF partiria só da carteira manual e ficaria errada. Mesmo
    # critério do dashboard/relatórios (get_consolidated_balance + gate beta).
    saldo = float(get_balance(user_id))
    balance_source = "manual"  # carteira manual; vira "consolidated" se somar OF
    of_bank_count = 0
    try:
        from db import get_consolidated_balance
        from core.services.plan_service import consolidated_balance_enabled
        cb = get_consolidated_balance(user_id)
        of_bank_count = int(cb.get("of_bank_count") or 0)
        if of_bank_count > 0 and consolidated_balance_enabled(user_id):
            saldo = float(cb.get("consolidated") or 0)
            balance_source = "consolidated"
    except Exception:
        pass  # sem OF/consolidado → segue com a carteira manual

    # Bancos conectados que NÃO entraram no saldo de partida (gate consolidado
    # desligado): a projeção parte só da carteira e subestima o caixa → o
    # dashboard mostra um aviso quando isso acontece.
    banks_excluded = of_bank_count > 0 and balance_source == "manual"

    receitas = 0.0
    for inc in list_recurring_incomes(user_id):
        if not inc.get("is_active"):
            continue
        receitas += _recurring_value_in_window(
            inc.get("pay_day"), inc.get("frequency") or "monthly", inc.get("pay_month"),
            _as_date(inc.get("start_date")), float(inc.get("amount") or 0), today, target_date,
        )

    gastos_fixos = 0.0
    for e in list_recurring_expenses(user_id):
        if not e.get("is_active"):
            continue
        if (e.get("payment_mode") or "autopay") != "autopay":
            continue  # 'manual' = boleto; já entra em boletos_ate
        if (e.get("frequency") or "monthly") not in ("monthly", "annual"):
            continue  # weekly/daily/once ficam de fora do v1 da projeção
        gastos_fixos += _recurring_value_in_window(
            e.get("due_day"), e.get("frequency") or "monthly", e.get("due_month"),
            _as_date(e.get("start_date")), float(e.get("amount") or 0), today, target_date,
        )

    boletos = 0.0
    n_boletos = 0
    for b in list_bills(user_id, include_paid=False, limit=1000):
        if b.get("status") != "pending":
            continue
        d = _as_date(b.get("due_date"))
        if d and d <= target_date:
            boletos += float(b.get("amount") or 0)
            n_boletos += 1

    faturas_cartao = _open_card_bills_due(user_id, target_date)

    extra = float(extra_amount or 0)
    projetado = saldo + receitas - gastos_fixos - boletos - faturas_cartao - extra
    return {
        "today": today.isoformat(),
        "target": target_date.isoformat(),
        "saldo_atual": round(saldo, 2),
        "balance_source": balance_source,
        "of_bank_count": of_bank_count,
        "banks_excluded": banks_excluded,
        "receitas_previstas": round(receitas, 2),
        "gastos_fixos_previstos": round(gastos_fixos, 2),
        "boletos_ate": round(boletos, 2),
        "n_boletos": n_boletos,
        "faturas_cartao": round(faturas_cartao, 2),
        "boleto_novo": round(extra, 2),
        "projetado": round(projetado, 2),
        "tranquilo": projetado >= 0,
    }


def forecast_horizons(user_id: int, horizons: tuple[int, ...] = (30, 60, 90)) -> dict[str, Any]:
    """Previsão de saldo em vários horizontes (default 30/60/90 dias).

    Feature paga (Pro+): reusa `project()` em hoje+N pra cada N e devolve
    ``{"today": ..., "horizons": {"30": <projeção>, "60": ..., "90": ...}}`` —
    formato pensado pro card do dashboard e pra tool de IA. Cada projeção mantém
    a mesma semântica de `project` (saldo + receitas fixas − gastos fixos −
    boletos até a data); ver docstring do módulo."""
    today = date.today()
    hz = {str(n): project(user_id, today + timedelta(days=n)) for n in horizons}
    # A origem do saldo é a mesma em todos os horizontes; sobe pro topo pra o
    # dashboard renderizar o aviso sem precisar abrir cada projeção.
    any_h = next(iter(hz.values()), {})
    return {
        "today": today.isoformat(),
        "balance_source": any_h.get("balance_source", "manual"),
        "of_bank_count": any_h.get("of_bank_count", 0),
        "banks_excluded": any_h.get("banks_excluded", False),
        "horizons": hz,
    }


__all__ = ["project", "forecast_horizons"]
