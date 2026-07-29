"""db/bills.py — Contas a pagar (boletos).

Instâncias por ciclo dos recorrentes com `payment_mode='manual'`. Diferente do
gasto fixo (autopay), a conta a pagar NÃO debita sozinha: a Piggy lembra e o
lançamento só é criado quando o usuário confirma o pagamento (`mark_bill_paid`).

Status: 'pending' | 'paid'. "Vencida" é derivado (pending + due_date < hoje).
Idempotência de instância: UNIQUE(recurring_id, due_date).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from .connection import get_conn


def _row(r: Any) -> dict[str, Any]:
    return {
        "id": r["id"],
        "recurring_id": r["recurring_id"],
        "name": r.get("name"),
        "category": r.get("category"),
        "due_date": r["due_date"].isoformat() if r["due_date"] else None,
        "amount": float(r["amount"]) if r["amount"] is not None else None,
        "status": r["status"],
        "paid_at": r["paid_at"].isoformat() if r.get("paid_at") else None,
        "paid_amount": float(r["paid_amount"]) if r.get("paid_amount") is not None else None,
        "launch_id": r.get("launch_id"),
    }


def list_bills(user_id: int, include_paid: bool = False, limit: int = 120) -> list[dict[str, Any]]:
    """Contas a pagar do usuário (com nome/categoria do recorrente). Pendentes
    primeiro, por vencimento; pagas recentes no fim se include_paid."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select b.id, b.recurring_id, r.name, r.category, b.due_date,
                       b.amount, b.status, b.paid_at, b.paid_amount, b.launch_id
                from bill_instances b
                join recurring_expenses r on r.id = b.recurring_id
                where b.user_id = %s
                  and (%s::boolean = true or b.status = 'pending')
                order by (b.status = 'pending') desc, b.due_date asc
                limit %s
                """,
                (user_id, include_paid, int(limit)),
            )
            rows = cur.fetchall() or []
    return [_row(r) for r in rows]


def get_bill(user_id: int, bill_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select b.id, b.recurring_id, r.name, r.category, b.due_date,
                       b.amount, b.status, b.paid_at, b.paid_amount, b.launch_id
                from bill_instances b
                join recurring_expenses r on r.id = b.recurring_id
                where b.user_id = %s and b.id = %s
                """,
                (user_id, int(bill_id)),
            )
            r = cur.fetchone()
            return _row(r) if r else None


def ensure_bill_instance(recurring_id: int, user_id: int, due_date: date, amount: float) -> None:
    """Cria a instância (pending) do ciclo se ainda não existir. Idempotente."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into bill_instances (recurring_id, user_id, due_date, amount, status)
                values (%s, %s, %s, %s, 'pending')
                on conflict (recurring_id, due_date) do nothing
                """,
                (int(recurring_id), int(user_id), due_date, Decimal(str(amount))),
            )
        conn.commit()


def mark_bill_paid(user_id: int, bill_id: int, amount: float | None = None) -> dict[str, Any] | None:
    """Confirma o pagamento: cria o lançamento de despesa (debita + categoriza)
    e marca a conta como paga. `amount` opcional sobrescreve o valor (boleto
    variável). Retorna a conta atualizada, ou None se não achar / já paga.
    """
    from db.accounts import add_launch_and_update_balance

    bill = get_bill(user_id, bill_id)
    if not bill or bill["status"] == "paid":
        return None

    valor = float(amount) if (amount is not None and float(amount) > 0) else float(bill["amount"] or 0)
    if valor <= 0:
        raise ValueError("VALOR_INVALIDO")

    name = bill["name"] or "Conta"
    categoria = bill["category"] or "outros"
    launch_id, _seq, _bal = add_launch_and_update_balance(
        user_id, "despesa", valor, alvo=f"conta:{name}", nota=f"Pagamento · {name}",
        categoria=categoria, is_internal_movement=False,
    )
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                update bill_instances
                   set status='paid', paid_at=now(), paid_amount=%s, launch_id=%s
                 where id=%s and user_id=%s and status='pending'
                """,
                (Decimal(str(valor)), launch_id, int(bill_id), int(user_id)),
            )
        conn.commit()
    return get_bill(user_id, bill_id)


def mark_bill_reminder_sent(bill_id: int, sent_on: date) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update bill_instances set reminder_last_sent_on=%s where id=%s",
                (sent_on, int(bill_id)),
            )
        conn.commit()


def list_active_manual_recurrings() -> list[dict[str, Any]]:
    """Global (todos os users) — recorrentes 'manual' ativos, pro loop gerar as
    instâncias do ciclo. Traz o que o cálculo de vencimento precisa."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, user_id, name, amount, category, due_day,
                       frequency, due_month, start_date
                from recurring_expenses
                where is_active = true and payment_mode = 'manual'
                """
            )
            return [dict(r) for r in (cur.fetchall() or [])]


def list_users_with_pending_bills() -> list[int]:
    """Distintos user_ids com pelo menos uma conta a pagar pendente. Usado pelo
    loop de lembretes pra saber a quem perguntar por vencimentos."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select distinct user_id from bill_instances where status = 'pending'")
            return [int(r["user_id"]) for r in (cur.fetchall() or [])]


def list_due_bill_reminders(user_id: int, today: date, days_before: int = 3) -> list[dict[str, Any]]:
    """Contas pendentes que merecem lembrete HOJE: faltam `days_before` dias, ou
    vence hoje, ou venceu ontem — e ainda não lembramos hoje."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select b.id, b.recurring_id, r.name, r.category, b.due_date,
                       b.amount, b.status, b.reminder_last_sent_on
                from bill_instances b
                join recurring_expenses r on r.id = b.recurring_id
                where b.user_id = %s
                  and b.status = 'pending'
                  and (b.reminder_last_sent_on is null or b.reminder_last_sent_on <> %s)
                  and (b.due_date - %s) in (%s, 0, -1)
                order by b.due_date asc
                """,
                (user_id, today, today, int(days_before)),
            )
            return [dict(r) for r in (cur.fetchall() or [])]


__all__ = [
    "list_bills", "get_bill", "ensure_bill_instance", "mark_bill_paid",
    "mark_bill_reminder_sent", "list_active_manual_recurrings",
    "list_users_with_pending_bills", "list_due_bill_reminders",
]
