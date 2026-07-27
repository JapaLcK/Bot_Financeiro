"""
db/recurring_income.py — Receitas Recorrentes.

Espelho de `db/recurring.py` do lado da entrada. Pro-only (mesma flag
`recurring_expenses_enabled`). Crédito automático no dia `pay_day` de cada
mês via o mesmo loop de `core/services/recurring_charger.py`.

Sempre cria launch `receita` na conta — receita não tem cartão de crédito,
então não existe `payment_type`/`card_id` aqui.

Idempotência: `last_credited_ym` impede creditar 2x no mesmo mês.
Reajuste: ao editar `amount`, guarda `last_amount` + timestamp pra UI mostrar
a variação (aumento de salário aparece igual reajuste de assinatura).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from .connection import get_conn
from .recurring import _parse_start_date
from .users import ensure_user


def list_recurring_incomes(user_id: int, include_inactive: bool = False) -> list[dict[str, Any]]:
    """Lista todas as receitas recorrentes do user."""
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select r.id, r.name, r.amount, r.category, r.pay_day,
                       r.is_primary, r.is_active,
                       r.last_amount, r.last_amount_changed_at,
                       r.last_credited_ym, r.notes, r.created_at, r.start_date
                from recurring_incomes r
                where r.user_id = %s
                  and (%s::boolean = true or r.is_active = true)
                order by r.is_primary desc, r.pay_day asc, lower(r.name) asc
                """,
                (user_id, include_inactive),
            )
            rows = cur.fetchall() or []
    return [_row_to_dict(r) for r in rows]


def get_recurring_income(user_id: int, inc_id: int) -> dict[str, Any] | None:
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select r.id, r.name, r.amount, r.category, r.pay_day,
                       r.is_primary, r.is_active,
                       r.last_amount, r.last_amount_changed_at,
                       r.last_credited_ym, r.notes, r.created_at, r.start_date
                from recurring_incomes r
                where r.user_id = %s and r.id = %s
                """,
                (user_id, int(inc_id)),
            )
            r = cur.fetchone()
            return _row_to_dict(r) if r else None


def _row_to_dict(r: Any) -> dict[str, Any]:
    return {
        "id": r["id"],
        "name": r["name"],
        "amount": float(r["amount"]),
        "category": r["category"],
        "pay_day": int(r["pay_day"]),
        "is_primary": bool(r["is_primary"]),
        "is_active": bool(r["is_active"]),
        "last_amount": float(r["last_amount"]) if r["last_amount"] is not None else None,
        "last_amount_changed_at": r["last_amount_changed_at"].isoformat() if r["last_amount_changed_at"] else None,
        "last_credited_ym": r["last_credited_ym"],
        "notes": r["notes"],
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        "start_date": r["start_date"].isoformat() if r["start_date"] else None,
    }


def count_active_recurring_incomes(user_id: int) -> int:
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select count(*) as n from recurring_incomes where user_id=%s and is_active=true",
                (user_id,),
            )
            return int(cur.fetchone()["n"] or 0)


def create_recurring_income(
    user_id: int,
    name: str,
    amount: float,
    category: str,
    pay_day: int,
    is_primary: bool = False,
    notes: str | None = None,
    start_date: date | str | None = None,
) -> dict[str, Any]:
    """Cria receita recorrente. Levanta ValueError se input inválido.

    `start_date` = a partir de quando a recorrência vale (default: hoje).
    """
    ensure_user(user_id)
    name = (name or "").strip()
    if not name:
        raise ValueError("NOME_INVALIDO")
    if amount is None or float(amount) <= 0:
        raise ValueError("VALOR_INVALIDO")
    if pay_day < 1 or pay_day > 31:
        raise ValueError("DIA_INVALIDO")
    start = _parse_start_date(start_date, default=date.today())

    cat = (category or "").strip() or "salário"
    note = (notes or "").strip() or None

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into recurring_incomes (
                    user_id, name, amount, category, pay_day,
                    is_primary, is_active, notes, start_date
                ) values (%s, %s, %s, %s, %s, %s, true, %s, %s)
                returning id
                """,
                (
                    user_id, name, Decimal(str(amount)), cat, int(pay_day),
                    bool(is_primary), note, start,
                ),
            )
            new_id = cur.fetchone()["id"]
        conn.commit()
    return get_recurring_income(user_id, new_id)


def update_recurring_income(
    user_id: int,
    inc_id: int,
    *,
    name: str | None = None,
    amount: float | None = None,
    category: str | None = None,
    pay_day: int | None = None,
    is_primary: bool | None = None,
    is_active: bool | None = None,
    notes: str | None = None,
    start_date: date | str | None = None,
) -> dict[str, Any]:
    """PATCH. Quando amount muda, registra `last_amount` + timestamp (detector de reajuste)."""
    ensure_user(user_id)
    current = get_recurring_income(user_id, inc_id)
    if not current:
        raise ValueError("RECEITA_NAO_ENCONTRADA")

    sets: list[str] = []
    params: list[Any] = []

    if name is not None:
        v = (name or "").strip()
        if not v:
            raise ValueError("NOME_INVALIDO")
        sets.append("name = %s")
        params.append(v)
    if amount is not None:
        if float(amount) <= 0:
            raise ValueError("VALOR_INVALIDO")
        if abs(float(amount) - float(current["amount"])) > 0.005:
            # Reajuste detectado: guarda valor anterior + timestamp
            sets.append("last_amount = %s")
            params.append(Decimal(str(current["amount"])))
            sets.append("last_amount_changed_at = now()")
        sets.append("amount = %s")
        params.append(Decimal(str(amount)))
    if category is not None:
        sets.append("category = %s")
        params.append((category or "").strip() or "salário")
    if pay_day is not None:
        if int(pay_day) < 1 or int(pay_day) > 31:
            raise ValueError("DIA_INVALIDO")
        sets.append("pay_day = %s")
        params.append(int(pay_day))
    if is_primary is not None:
        sets.append("is_primary = %s")
        params.append(bool(is_primary))
    if is_active is not None:
        sets.append("is_active = %s")
        params.append(bool(is_active))
    if notes is not None:
        sets.append("notes = %s")
        params.append((notes or "").strip() or None)
    if start_date is not None:
        sets.append("start_date = %s")
        params.append(_parse_start_date(start_date, default=date.today()))

    if not sets:
        return current

    params.append(user_id)
    params.append(int(inc_id))
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"update recurring_incomes set {', '.join(sets)} where user_id=%s and id=%s",
                params,
            )
        conn.commit()
    return get_recurring_income(user_id, inc_id)


def delete_recurring_income(user_id: int, inc_id: int) -> None:
    ensure_user(user_id)
    current = get_recurring_income(user_id, inc_id)
    if not current:
        raise ValueError("RECEITA_NAO_ENCONTRADA")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "delete from recurring_incomes where user_id=%s and id=%s",
                (user_id, int(inc_id)),
            )
        conn.commit()


def list_due_recurring_incomes(today: date | None = None) -> list[dict[str, Any]]:
    """Lista global — todos os user — receitas que caem hoje (ou já passaram no mês)
    e ainda não foram creditadas neste mês (`last_credited_ym != current_ym`).

    Usado pelo cron diário pra processar os créditos automáticos.
    """
    today = today or date.today()
    ym = today.strftime("%Y-%m")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select r.id, r.user_id, r.name, r.amount, r.category, r.pay_day
                from recurring_incomes r
                where r.is_active = true
                  and r.pay_day <= %s
                  and (r.last_credited_ym is null or r.last_credited_ym != %s)
                  -- Não retroagir: começa em start_date (ver charger).
                  and (
                      to_char(coalesce(r.start_date, r.created_at::date), 'YYYY-MM') < %s
                      or (
                          to_char(coalesce(r.start_date, r.created_at::date), 'YYYY-MM') = %s
                          and r.pay_day >= extract(day from coalesce(r.start_date, r.created_at::date))
                      )
                  )
                """,
                (today.day, ym, ym, ym),
            )
            rows = cur.fetchall() or []
    return [dict(r) for r in rows]


def mark_recurring_income_credited(user_id: int, inc_id: int, ym: str) -> None:
    """Marca como creditada neste mês (idempotência)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update recurring_incomes set last_credited_ym=%s "
                "where user_id=%s and id=%s",
                (ym, user_id, int(inc_id)),
            )
        conn.commit()


__all__ = [
    "list_recurring_incomes",
    "get_recurring_income",
    "count_active_recurring_incomes",
    "create_recurring_income",
    "update_recurring_income",
    "delete_recurring_income",
    "list_due_recurring_incomes",
    "mark_recurring_income_credited",
]
