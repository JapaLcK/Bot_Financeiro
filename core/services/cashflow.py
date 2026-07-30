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
from datetime import date
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


def project(user_id: int, target_date: date, extra_amount: float = 0.0) -> dict[str, Any]:
    """Projeção de caixa até `target_date`, opcionalmente considerando um boleto
    novo de `extra_amount`. Ver docstring do módulo."""
    from db.accounts import get_balance
    from db.recurring import list_recurring_expenses
    from db.recurring_income import list_recurring_incomes
    from db.bills import list_bills

    today = date.today()
    saldo = float(get_balance(user_id))

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

    extra = float(extra_amount or 0)
    projetado = saldo + receitas - gastos_fixos - boletos - extra
    return {
        "today": today.isoformat(),
        "target": target_date.isoformat(),
        "saldo_atual": round(saldo, 2),
        "receitas_previstas": round(receitas, 2),
        "gastos_fixos_previstos": round(gastos_fixos, 2),
        "boletos_ate": round(boletos, 2),
        "n_boletos": n_boletos,
        "boleto_novo": round(extra, 2),
        "projetado": round(projetado, 2),
        "tranquilo": projetado >= 0,
    }


__all__ = ["project"]
