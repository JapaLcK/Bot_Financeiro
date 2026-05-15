"""
db/recurring.py — Gastos Fixos / Recorrentes (Sprint 4).

Pro-only. Cobrança automática no dia `due_day` de cada mês via cron.
- `payment_type='account'`     → cria launch despesa (não interno).
- `payment_type='credit_card'` → cria credit_transaction na bill open atual.

Idempotência: `last_charged_ym` impede cobrar 2x no mesmo mês.
Reajuste: ao editar `amount`, guarda `last_amount` + timestamp pra UI mostrar a variação.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from .connection import get_conn
from .users import ensure_user


def list_recurring_expenses(user_id: int, include_inactive: bool = False) -> list[dict[str, Any]]:
    """Lista todos os gastos fixos do user."""
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select r.id, r.name, r.amount, r.category, r.due_day,
                       r.payment_type, r.card_id, c.name as card_name,
                       r.is_essential, r.is_active,
                       r.last_amount, r.last_amount_changed_at,
                       r.last_charged_ym, r.notes, r.created_at
                from recurring_expenses r
                left join credit_cards c on c.id = r.card_id
                where r.user_id = %s
                  and (%s::boolean = true or r.is_active = true)
                order by r.is_essential desc, r.due_day asc, lower(r.name) asc
                """,
                (user_id, include_inactive),
            )
            rows = cur.fetchall() or []
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append({
            "id": r["id"],
            "name": r["name"],
            "amount": float(r["amount"]),
            "category": r["category"],
            "due_day": int(r["due_day"]),
            "payment_type": r["payment_type"],
            "card_id": r["card_id"],
            "card_name": r["card_name"],
            "is_essential": bool(r["is_essential"]),
            "is_active": bool(r["is_active"]),
            "last_amount": float(r["last_amount"]) if r["last_amount"] is not None else None,
            "last_amount_changed_at": r["last_amount_changed_at"].isoformat() if r["last_amount_changed_at"] else None,
            "last_charged_ym": r["last_charged_ym"],
            "notes": r["notes"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        })
    return out


def get_recurring_expense(user_id: int, rec_id: int) -> dict[str, Any] | None:
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select r.id, r.name, r.amount, r.category, r.due_day,
                       r.payment_type, r.card_id, c.name as card_name,
                       r.is_essential, r.is_active,
                       r.last_amount, r.last_amount_changed_at,
                       r.last_charged_ym, r.notes, r.created_at
                from recurring_expenses r
                left join credit_cards c on c.id = r.card_id
                where r.user_id = %s and r.id = %s
                """,
                (user_id, int(rec_id)),
            )
            r = cur.fetchone()
            if not r:
                return None
            return {
                "id": r["id"], "name": r["name"], "amount": float(r["amount"]),
                "category": r["category"], "due_day": int(r["due_day"]),
                "payment_type": r["payment_type"], "card_id": r["card_id"],
                "card_name": r["card_name"],
                "is_essential": bool(r["is_essential"]), "is_active": bool(r["is_active"]),
                "last_amount": float(r["last_amount"]) if r["last_amount"] is not None else None,
                "last_amount_changed_at": r["last_amount_changed_at"].isoformat() if r["last_amount_changed_at"] else None,
                "last_charged_ym": r["last_charged_ym"],
                "notes": r["notes"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }


def count_active_recurring_expenses(user_id: int) -> int:
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select count(*) as n from recurring_expenses where user_id=%s and is_active=true",
                (user_id,),
            )
            return int(cur.fetchone()["n"] or 0)


def create_recurring_expense(
    user_id: int,
    name: str,
    amount: float,
    category: str,
    due_day: int,
    payment_type: str,
    card_id: int | None = None,
    is_essential: bool = False,
    notes: str | None = None,
) -> dict[str, Any]:
    """Cria gasto fixo. Levanta ValueError se input inválido."""
    ensure_user(user_id)
    name = (name or "").strip()
    if not name:
        raise ValueError("NOME_INVALIDO")
    if amount is None or float(amount) <= 0:
        raise ValueError("VALOR_INVALIDO")
    if due_day < 1 or due_day > 31:
        raise ValueError("DIA_INVALIDO")
    if payment_type not in ("account", "credit_card"):
        raise ValueError("FORMA_PAGAMENTO_INVALIDA")
    if payment_type == "credit_card" and not card_id:
        raise ValueError("CARTAO_OBRIGATORIO")
    if payment_type == "account":
        card_id = None  # ignora card_id quando não é cartão

    cat = (category or "").strip() or "outros"
    note = (notes or "").strip() or None

    with get_conn() as conn:
        with conn.cursor() as cur:
            if card_id:
                cur.execute(
                    "select id from credit_cards where id=%s and user_id=%s",
                    (card_id, user_id),
                )
                if not cur.fetchone():
                    raise ValueError("CARTAO_NAO_ENCONTRADO")

            cur.execute(
                """
                insert into recurring_expenses (
                    user_id, name, amount, category, due_day, payment_type,
                    card_id, is_essential, is_active, notes
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, true, %s)
                returning id
                """,
                (
                    user_id, name, Decimal(str(amount)), cat, int(due_day),
                    payment_type, card_id, bool(is_essential), note,
                ),
            )
            new_id = cur.fetchone()["id"]
        conn.commit()
    return get_recurring_expense(user_id, new_id)


def update_recurring_expense(
    user_id: int,
    rec_id: int,
    *,
    name: str | None = None,
    amount: float | None = None,
    category: str | None = None,
    due_day: int | None = None,
    payment_type: str | None = None,
    card_id: int | None = None,
    is_essential: bool | None = None,
    is_active: bool | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """PATCH. Quando amount muda, registra `last_amount` + timestamp (detector de reajuste)."""
    ensure_user(user_id)
    current = get_recurring_expense(user_id, rec_id)
    if not current:
        raise ValueError("RECORRENTE_NAO_ENCONTRADO")

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
        params.append((category or "").strip() or "outros")
    if due_day is not None:
        if int(due_day) < 1 or int(due_day) > 31:
            raise ValueError("DIA_INVALIDO")
        sets.append("due_day = %s")
        params.append(int(due_day))
    if payment_type is not None:
        if payment_type not in ("account", "credit_card"):
            raise ValueError("FORMA_PAGAMENTO_INVALIDA")
        sets.append("payment_type = %s")
        params.append(payment_type)
        if payment_type == "account":
            sets.append("card_id = NULL")
    if card_id is not None and (payment_type or current["payment_type"]) == "credit_card":
        sets.append("card_id = %s")
        params.append(int(card_id))
    if is_essential is not None:
        sets.append("is_essential = %s")
        params.append(bool(is_essential))
    if is_active is not None:
        sets.append("is_active = %s")
        params.append(bool(is_active))
    if notes is not None:
        sets.append("notes = %s")
        params.append((notes or "").strip() or None)

    if not sets:
        return current

    params.append(user_id)
    params.append(int(rec_id))
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"update recurring_expenses set {', '.join(sets)} where user_id=%s and id=%s",
                params,
            )
        conn.commit()
    return get_recurring_expense(user_id, rec_id)


def delete_recurring_expense(user_id: int, rec_id: int) -> None:
    ensure_user(user_id)
    current = get_recurring_expense(user_id, rec_id)
    if not current:
        raise ValueError("RECORRENTE_NAO_ENCONTRADO")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "delete from recurring_expenses where user_id=%s and id=%s",
                (user_id, int(rec_id)),
            )
        conn.commit()


def list_due_recurring_expenses(today: date | None = None) -> list[dict[str, Any]]:
    """Lista globais — todos os user — gastos fixos que VENCEM hoje e ainda
    não foram cobrados neste mês (`last_charged_ym != current_ym`).

    Usado pelo cron diário pra processar cobranças automáticas.
    """
    today = today or date.today()
    ym = today.strftime("%Y-%m")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select r.id, r.user_id, r.name, r.amount, r.category,
                       r.due_day, r.payment_type, r.card_id
                from recurring_expenses r
                where r.is_active = true
                  and r.due_day <= %s
                  and (r.last_charged_ym is null or r.last_charged_ym != %s)
                """,
                (today.day, ym),
            )
            rows = cur.fetchall() or []
    return [dict(r) for r in rows]


def mark_recurring_charged(user_id: int, rec_id: int, ym: str) -> None:
    """Marca como cobrado neste mês (idempotência)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update recurring_expenses set last_charged_ym=%s "
                "where user_id=%s and id=%s",
                (ym, user_id, int(rec_id)),
            )
        conn.commit()


__all__ = [
    "list_recurring_expenses",
    "get_recurring_expense",
    "count_active_recurring_expenses",
    "create_recurring_expense",
    "update_recurring_expense",
    "delete_recurring_expense",
    "list_due_recurring_expenses",
    "mark_recurring_charged",
]
