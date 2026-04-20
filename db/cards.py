"""
db/cards.py — Cartões de crédito: faturas, parcelamentos e pagamentos.
"""
import calendar
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from utils_date import _tz, today_tz, billing_period_for_close_day

from .connection import get_conn
from .users import ensure_user
from .accounts import add_launch_and_update_balance


# ──────────────────────────────────────────────────────────────────────────────
# Helpers de data/período
# ──────────────────────────────────────────────────────────────────────────────

def _safe_date(y: int, m: int, d: int) -> date:
    last = calendar.monthrange(y, m)[1]
    return date(y, m, min(d, last))


def _prev_month(y: int, m: int) -> tuple[int, int]:
    return (y - 1, 12) if m == 1 else (y, m - 1)


def _bill_period_for_purchase(purchased_at: date, closing_day: int):
    y, m = purchased_at.year, purchased_at.month
    end_this = _safe_date(y, m, closing_day)
    if purchased_at > end_this:
        m = 1 if m == 12 else m + 1
        if m == 1:
            y += 1
    period_end = _safe_date(y, m, closing_day)
    py, pm = _prev_month(y, m)
    period_start = _safe_date(py, pm, closing_day) + timedelta(days=1)
    return period_start, period_end


def add_months(y: int, m: int, delta: int) -> tuple[int, int]:
    m2 = m + delta
    y2 = y + (m2 - 1) // 12
    m2 = (m2 - 1) % 12 + 1
    return y2, m2


def _last_day_of_month(y: int, m: int) -> int:
    return calendar.monthrange(y, m)[1]


def bill_period_for_month(year: int, month: int, closing_day: int) -> tuple[date, date]:
    """Período da fatura que FECHA no dia closing_day do mês (year, month)."""
    end_day = min(int(closing_day), _last_day_of_month(year, month))
    period_end = date(year, month, end_day)
    prev_y, prev_m = add_months(year, month, -1)
    start_day = min(int(closing_day) + 1, _last_day_of_month(prev_y, prev_m))
    period_start = date(prev_y, prev_m, start_day)
    return period_start, period_end


# ──────────────────────────────────────────────────────────────────────────────
# Cartões
# ──────────────────────────────────────────────────────────────────────────────

def card_name_exists(user_id: int, name: str) -> bool:
    name = (name or "").strip()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select 1 from credit_cards where user_id=%s and lower(name)=lower(%s) limit 1",
                (user_id, name),
            )
            return cur.fetchone() is not None


def create_card(user_id: int, name: str, closing_day: int, due_day: int) -> int:
    ensure_user(user_id)
    name = (name or "").strip()
    if not name:
        raise ValueError("nome do cartão vazio")
    if card_name_exists(user_id, name):
        raise ValueError(f"nome_duplicado:{name}")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into credit_cards (user_id, name, closing_day, due_day) "
                "values (%s, %s, %s, %s) returning id",
                (user_id, name, int(closing_day), int(due_day)),
            )
            card_id = cur.fetchone()["id"]
        conn.commit()
    return card_id


def delete_card(user_id: int, card_id: int) -> bool:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select id from credit_cards where id=%s and user_id=%s", (card_id, user_id))
            if not cur.fetchone():
                return False
            cur.execute(
                "update users set default_card_id=null where id=%s and default_card_id=%s",
                (user_id, card_id),
            )
            cur.execute("delete from credit_cards where id=%s and user_id=%s", (card_id, user_id))
        conn.commit()
    return True


def get_card_id_by_name(user_id: int, name: str) -> int | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id from credit_cards where user_id=%s and name=%s", (user_id, name)
            )
            row = cur.fetchone()
            return row["id"] if row else None


def set_default_card(user_id: int, card_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("update users set default_card_id=%s where id=%s", (card_id, user_id))
        conn.commit()


def get_default_card_id(user_id: int) -> int | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select default_card_id from users where id=%s", (user_id,))
            row = cur.fetchone()
            return row["default_card_id"] if row else None


def list_cards(user_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select c.id, c.name, c.closing_day, c.due_day,
                       c.reminders_enabled, c.reminders_days_before, c.reminder_last_sent_on,
                       c.credit_limit,
                       (u.default_card_id = c.id) as is_default
                from credit_cards c
                left join users u on u.id = c.user_id
                where c.user_id = %s
                order by c.name
                """,
                (user_id,),
            )
            return cur.fetchall()


def get_card_by_id(user_id: int, card_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select c.id, c.name, c.closing_day, c.due_day,
                       c.reminders_enabled, c.reminders_days_before, c.reminder_last_sent_on,
                       c.credit_limit,
                       (u.default_card_id = c.id) as is_default
                from credit_cards c
                left join users u on u.id = c.user_id
                where c.user_id = %s and c.id = %s
                limit 1
                """,
                (user_id, card_id),
            )
            return cur.fetchone()


def get_card_credit_usage(user_id: int, card_id: int) -> Decimal:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select coalesce(sum(greatest(total - coalesce(paid_amount, 0), 0)), 0) as used
                from credit_bills
                where user_id = %s and card_id = %s and status in ('open', 'closed')
                """,
                (user_id, card_id),
            )
            row = cur.fetchone()
    return Decimal(str(row["used"] or 0))


def set_card_limit(user_id: int, card_id: int, limit_amount: float | None) -> bool:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update credit_cards set credit_limit = %s where user_id = %s and id = %s",
                (Decimal(str(limit_amount)) if limit_amount is not None else None, user_id, card_id),
            )
            updated = cur.rowcount > 0
        conn.commit()
    return updated


def update_card_reminder_settings(user_id: int, card_id: int, enabled: bool, days_before: int | None = None):
    with get_conn() as conn:
        with conn.cursor() as cur:
            if days_before is None:
                cur.execute(
                    "update credit_cards set reminders_enabled=%s where user_id=%s and id=%s",
                    (bool(enabled), user_id, card_id),
                )
            else:
                cur.execute(
                    "update credit_cards set reminders_enabled=%s, reminders_days_before=%s "
                    "where user_id=%s and id=%s",
                    (bool(enabled), int(days_before), user_id, card_id),
                )
        conn.commit()


def mark_card_reminder_sent(user_id: int, card_id: int, sent_on: date):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update credit_cards set reminder_last_sent_on=%s where user_id=%s and id=%s",
                (sent_on, user_id, card_id),
            )
        conn.commit()


# ──────────────────────────────────────────────────────────────────────────────
# Faturas (credit_bills)
# ──────────────────────────────────────────────────────────────────────────────

def get_or_create_open_bill(user_id: int, card_id: int, ref_date: date) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select closing_day, user_id from credit_cards where id=%s limit 1",
                (card_id,),
            )
            card = cur.fetchone()
            if not card:
                raise ValueError("Cartão não encontrado.")
            if int(card["user_id"]) != int(user_id):
                raise ValueError("Cartão não pertence a este usuário.")

            closing_day = int(card["closing_day"])
            period_start, period_end = billing_period_for_close_day(ref_date, closing_day)

            cur.execute(
                """
                insert into credit_bills (user_id, card_id, period_start, period_end, total, status)
                values (%s, %s, %s, %s, 0, 'open')
                on conflict (card_id, period_start, period_end)
                do update set user_id = excluded.user_id
                returning id, status
                """,
                (user_id, card_id, period_start, period_end),
            )
            row = cur.fetchone()
            bill_id = int(row["id"])
            status = (row.get("status") or "").lower()

            if status in ("paid", "closed"):
                cur.execute("update credit_bills set status='open' where id=%s", (bill_id,))

        conn.commit()

    return bill_id


def get_or_create_bill_by_period(user_id: int, card_id: int, period_start: date, period_end: date) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id from credit_bills "
                "where user_id=%s and card_id=%s and period_start=%s and period_end=%s",
                (user_id, card_id, period_start, period_end),
            )
            row = cur.fetchone()
            if row:
                return int(row["id"])

            cur.execute(
                "insert into credit_bills (user_id, card_id, period_start, period_end, status, total, paid_amount) "
                "values (%s, %s, %s, %s, 'open', 0, 0) returning id",
                (user_id, card_id, period_start, period_end),
            )
            bid = int(cur.fetchone()["id"])
        conn.commit()
    return bid


def get_current_open_bill_id(user_id: int, card_id: int, as_of: date):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select closing_day from credit_cards where id=%s and user_id=%s limit 1",
                (card_id, user_id),
            )
            row = cur.fetchone()
            if not row:
                return None
            closing_day = int(row["closing_day"])
            ps, pe = billing_period_for_close_day(as_of, closing_day)
            cur.execute(
                "select id from credit_bills "
                "where user_id=%s and card_id=%s and status='open' and period_start=%s and period_end=%s limit 1",
                (user_id, card_id, ps, pe),
            )
            b = cur.fetchone()
            return int(b["id"]) if b else None


# ──────────────────────────────────────────────────────────────────────────────
# Transações de crédito
# ──────────────────────────────────────────────────────────────────────────────

def add_credit_purchase(
    user_id: int,
    card_id: int,
    valor: float,
    categoria: str | None,
    nota: str | None,
    purchased_at: date,
):
    ensure_user(user_id)
    bill_id = get_or_create_open_bill(user_id, card_id, purchased_at)
    v = Decimal(str(valor))

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into credit_transactions (bill_id, user_id, card_id, valor, categoria, nota, purchased_at) "
                "values (%s, %s, %s, %s, %s, %s, %s) returning id",
                (bill_id, user_id, card_id, v, categoria, nota, purchased_at),
            )
            tx_id = cur.fetchone()["id"]

            cur.execute(
                "update credit_bills set total = total + %s where id=%s and user_id=%s returning total",
                (v, bill_id, user_id),
            )
            bill_total = Decimal(str(cur.fetchone()["total"]))

            cur.execute(
                "select coalesce(paid_amount, 0) as paid_amount from credit_bills where id=%s and user_id=%s",
                (bill_id, user_id),
            )
            bill_paid = Decimal(str(cur.fetchone()["paid_amount"]))
            bill_due = bill_total - bill_paid

        conn.commit()

    return tx_id, float(bill_due), bill_id


def add_credit_purchase_installments(
    user_id: int,
    card_id: int,
    valor_total: float,
    categoria: str | None,
    nota: str | None,
    purchased_at: date,
    installments: int,
):
    """Registra compra parcelada: uma transação por fatura futura."""
    ensure_user(user_id)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select closing_day from credit_cards where id=%s and user_id=%s",
                (card_id, user_id),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError("Cartão não encontrado ou não pertence a este usuário.")
            closing_day = row["closing_day"]

    group_id = uuid4()
    vtotal = Decimal(str(valor_total))
    n = max(1, int(installments))
    vparc = (vtotal / Decimal(n)).quantize(Decimal("0.01"))
    parcelas = [vparc] * n
    parcelas[-1] = (parcelas[-1] + vtotal - sum(parcelas)).quantize(Decimal("0.01"))

    ps0, pe0 = _bill_period_for_purchase(purchased_at, closing_day)
    base_y, base_m = pe0.year, pe0.month

    tx_ids = []
    with get_conn() as conn:
        with conn.cursor() as cur:
            for i in range(n):
                y2, m2 = add_months(base_y, base_m, i)
                ps, pe = bill_period_for_month(y2, m2, closing_day)
                bill_id = get_or_create_bill_by_period(user_id, card_id, ps, pe)

                cur.execute(
                    "insert into credit_transactions "
                    "(bill_id, user_id, card_id, valor, categoria, nota, purchased_at, "
                    "group_id, installment_no, installments_total, is_refund) "
                    "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,false) returning id",
                    (bill_id, user_id, card_id, parcelas[i], categoria, nota,
                     purchased_at, group_id, i + 1, n),
                )
                tx_ids.append(cur.fetchone()["id"])

                cur.execute(
                    "update credit_bills set total = total + %s where id=%s",
                    (parcelas[i], bill_id),
                )
        conn.commit()

    return {"group_id": str(group_id), "tx_ids": tx_ids}, float(vtotal)


def add_credit_refund(
    user_id: int,
    card_id: int,
    valor: float,
    categoria: str | None,
    nota: str | None,
    purchased_at: date,
):
    ensure_user(user_id)
    bill_id = get_or_create_open_bill(user_id, card_id, purchased_at)
    v = Decimal(str(valor))
    if v <= 0:
        raise ValueError("valor do estorno deve ser > 0")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into credit_transactions "
                "(bill_id, user_id, card_id, tipo, valor, categoria, nota, purchased_at, is_refund) "
                "values (%s,%s,%s,'estorno',%s,%s,%s,%s,true) returning id",
                (bill_id, user_id, card_id, -v, categoria, nota, purchased_at),
            )
            tx_id = cur.fetchone()["id"]
            cur.execute(
                "update credit_bills set total = total + %s where id=%s returning total",
                (-v, bill_id),
            )
            total = cur.fetchone()["total"]
        conn.commit()

    return tx_id, total


def undo_credit_transaction(user_id: int, ct_id: int):
    """
    Desfaz um crédito CT#.
    Se pertence a parcelamento (group_id), desfaz o grupo inteiro.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id, bill_id, valor, group_id, installment_no, installments_total "
                "from credit_transactions where user_id=%s and id=%s",
                (user_id, ct_id),
            )
            tx = cur.fetchone()
            if not tx:
                return None

            group_id = tx.get("group_id")
            installments_total = int(tx.get("installments_total") or 0)

    if group_id and installments_total > 1:
        return undo_installment_group(user_id, group_id)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id, bill_id, valor from credit_transactions "
                "where user_id=%s and id=%s for update",
                (user_id, ct_id),
            )
            tx2 = cur.fetchone()
            if not tx2:
                return None

            bill_id = tx2["bill_id"]
            v = Decimal(str(tx2["valor"]))

            cur.execute(
                "delete from credit_transactions where user_id=%s and id=%s",
                (user_id, ct_id),
            )
            cur.execute(
                "update credit_bills set total = greatest(0, total - %s) "
                "where id=%s and user_id=%s "
                "returning total, coalesce(paid_amount, 0) as paid_amount",
                (float(v), bill_id, user_id),
            )
            b = cur.fetchone()
            if b:
                total = Decimal(str(b["total"]))
                paid = Decimal(str(b["paid_amount"]))
                if paid >= total:
                    cur.execute(
                        "update credit_bills set status='paid', paid_at=now() "
                        "where id=%s and user_id=%s",
                        (bill_id, user_id),
                    )

            conn.commit()

    return {"mode": "single", "ct_id": ct_id, "removed_total": float(v), "removed_count": 1}


def undo_installment_group(user_id: int, group_id: str):
    """Desfaz parcelamento inteiro removendo todas as transactions do grupo."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id, bill_id, valor from credit_transactions "
                "where user_id = %s and group_id = %s::uuid and is_refund = false for update",
                (user_id, group_id),
            )
            rows = cur.fetchall()
            if not rows:
                return None

            total_removed = Decimal("0")
            by_bill: dict[int, Decimal] = {}
            for r in rows:
                v = Decimal(str(r["valor"]))
                total_removed += v
                by_bill[r["bill_id"]] = by_bill.get(r["bill_id"], Decimal("0")) + v

            removed_count = len(rows)

            cur.execute(
                "delete from credit_transactions "
                "where user_id = %s and group_id = %s::uuid and is_refund = false",
                (user_id, group_id),
            )
            for bill_id, bill_sum in by_bill.items():
                cur.execute(
                    "update credit_bills set total = greatest(0, total - %s) "
                    "where id = %s and user_id = %s",
                    (float(bill_sum), bill_id, user_id),
                )

            conn.commit()

    return {
        "group_id": group_id,
        "removed_count": removed_count,
        "removed_total": float(total_removed),
    }


def resolve_installment_group_id(user_id: int, identifier: str) -> str | None:
    ident = (identifier or "").strip().lower()
    if not ident:
        return None
    if ident.startswith("par-"):
        ident = ident[4:]
    if ident.startswith("pc") and len(ident) > 2:
        ident = ident[2:]

    with get_conn() as conn:
        with conn.cursor() as cur:
            if len(ident) == 36 and "-" in ident:
                cur.execute(
                    "select distinct group_id::text as group_id from credit_transactions "
                    "where user_id = %s and group_id = %s::uuid limit 1",
                    (user_id, ident),
                )
                row = cur.fetchone()
                return row["group_id"] if row else None

            cur.execute(
                "select distinct group_id::text as group_id from credit_transactions "
                "where user_id = %s and group_id is not null "
                "and replace(group_id::text, '-', '') like %s "
                "order by group_id::text limit 2",
                (user_id, f"{ident}%"),
            )
            rows = cur.fetchall()

    return rows[0]["group_id"] if len(rows) == 1 else None


# ──────────────────────────────────────────────────────────────────────────────
# Resumos e listagens
# ──────────────────────────────────────────────────────────────────────────────

def get_open_bill_summary(user_id: int, card_id: int, as_of: date | None = None):
    if as_of is None:
        as_of = today_tz()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select closing_day from credit_cards where id=%s and user_id=%s limit 1",
                (card_id, user_id),
            )
            row = cur.fetchone()
            if not row:
                return None

            closing_day = int(row["closing_day"])
            period_start, period_end = billing_period_for_close_day(as_of, closing_day)
            bill_id = get_or_create_open_bill(user_id, card_id, as_of)

            cur.execute(
                "select id, period_start, period_end, total, "
                "coalesce(paid_amount, 0) as paid_amount, status "
                "from credit_bills "
                "where user_id=%s and card_id=%s and period_start=%s and period_end=%s limit 1",
                (user_id, card_id, period_start, period_end),
            )
            bill = cur.fetchone()
            if not bill:
                return None

            cur.execute(
                "select id, valor, categoria, nota, purchased_at, "
                "installment_no, installments_total, group_id, is_refund "
                "from credit_transactions "
                "where user_id=%s and bill_id=%s "
                "order by purchased_at desc, id desc limit 50",
                (user_id, bill["id"]),
            )
            items = cur.fetchall()

    return bill, items


def pay_bill_amount(
    user_id: int,
    card_id: int,
    card_name: str,
    amount: float | None,
    bill_id: int | None = None,
):
    with get_conn() as conn:
        with conn.cursor() as cur:
            if bill_id is not None:
                cur.execute(
                    "select id, total, coalesce(paid_amount, 0) as paid_amount, status "
                    "from credit_bills where id=%s and user_id=%s and card_id=%s limit 1 for update",
                    (bill_id, user_id, card_id),
                )
            else:
                cur.execute(
                    "select id, total, coalesce(paid_amount, 0) as paid_amount, status "
                    "from credit_bills where user_id=%s and card_id=%s and status in ('open','closed') "
                    "order by period_start desc limit 1 for update",
                    (user_id, card_id),
                )

            bill = cur.fetchone()
            if not bill:
                return None

            total = Decimal(str(bill["total"]))
            paid = Decimal(str(bill["paid_amount"]))
            due = total - paid

            if due <= 0:
                cur.execute(
                    "update credit_bills set status='paid', paid_at=now() where id=%s",
                    (bill["id"],),
                )
                conn.commit()
                return None

            if amount is not None:
                pay = Decimal(str(amount))
                if pay <= 0:
                    return {"error": "invalid_amount"}
                if pay > due:
                    return {
                        "error": "amount_too_high",
                        "due": float(due),
                        "total": float(total),
                        "paid_amount": float(paid),
                    }
            else:
                pay = due

        # Pagamento de fatura é movimentação interna
        launch_id, new_balance = add_launch_and_update_balance(
            user_id=user_id,
            tipo="despesa",
            valor=float(pay),
            alvo=f"fatura:{card_name}",
            nota=f"Pagamento de fatura ({card_name})",
            categoria="pagamento_fatura",
            is_internal_movement=True,
        )

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                update credit_bills
                set paid_amount = coalesce(paid_amount, 0) + %s,
                    paid_at = now(),
                    status = case
                        when coalesce(paid_amount, 0) + %s >= total then 'paid'
                        else status
                    end
                where id=%s
                """,
                (pay, pay, bill["id"]),
            )
            conn.commit()

    return {"paid": float(pay), "launch_id": launch_id, "new_balance": new_balance}


def close_bill(user_id: int, card_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update credit_bills set status='closed', closed_at=now() "
                "where id = (select id from credit_bills where card_id=%s and status='open' "
                "order by period_start desc limit 1) returning id",
                (card_id,),
            )
            row = cur.fetchone()
        conn.commit()
    return row["id"] if row else None


def get_next_bill_summary(user_id: int, card_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select closing_day from credit_cards where id=%s", (card_id,)
            )
            closing_day = cur.fetchone()["closing_day"]

            cur.execute(
                "select period_start from credit_bills where card_id=%s "
                "order by period_start desc limit 1",
                (card_id,),
            )
            last = cur.fetchone()
            if last:
                y, m = last["period_start"].year, last["period_start"].month
                y2, m2 = add_months(y, m, 1)
            else:
                from datetime import date as _date
                today = _date.today()
                y2, m2 = today.year, today.month

            ps, pe = bill_period_for_month(y2, m2, closing_day)
            bill_id = get_or_create_bill_by_period(user_id, card_id, ps, pe)

            cur.execute(
                "select id, period_start, period_end, total, paid_amount, status "
                "from credit_bills where id=%s",
                (bill_id,),
            )
            bill = cur.fetchone()
    return bill


def list_open_bills(user_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select b.id, c.name as card_name, b.period_start, b.period_end,
                       b.total, coalesce(b.paid_amount, 0) as paid_amount, b.status
                from credit_bills b
                join credit_cards c on c.id = b.card_id
                where b.user_id=%s and b.status='open'
                order by b.period_end asc, c.name asc
                """,
                (user_id,),
            )
            return cur.fetchall()


def list_credit_card_due_reminders(user_id: int, today: date):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select c.id as card_id, c.name as card_name,
                       c.closing_day, c.due_day, c.reminders_enabled,
                       c.reminders_days_before, c.reminder_last_sent_on,
                       b.id as bill_id, b.period_start, b.period_end,
                       b.total, coalesce(b.paid_amount, 0) as paid_amount
                from credit_cards c
                join credit_bills b on b.card_id = c.id and b.user_id = c.user_id
                where c.user_id = %s and c.reminders_enabled = true
                  and b.status in ('open', 'closed')
                order by b.period_end asc, c.name asc
                """,
                (user_id,),
            )
            return cur.fetchall()


def list_installment_groups(user_id: int, limit: int = 15):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select t.group_id, c.name as card_name,
                       max(t.installments_total) as n_total,
                       count(*) as n_registered,
                       sum(t.valor) as total,
                       sum(case when b.status = 'open' then t.valor else 0 end) as total_pending,
                       count(case when b.status = 'open' then 1 end) as n_pending,
                       max(t.purchased_at) as last_purchase,
                       min(t.nota) as nota
                from credit_transactions t
                join credit_cards c on c.id = t.card_id
                join credit_bills b on b.id = t.bill_id
                where t.user_id=%s and t.group_id is not null and t.is_refund=false
                group by t.group_id, c.name
                order by max(t.purchased_at) desc
                limit %s
                """,
                (user_id, limit),
            )
            return cur.fetchall()


def import_credit_ofx_bulk(
    user_id: int,
    card_id: int,
    tx_rows: list[dict],
    file_hash: str,
    dt_start: "date | None",
    dt_end: "date | None",
    acct_id: "str | None" = None,
    credit_limit: "Decimal | None" = None,
    ledger_balance: "Decimal | None" = None,
) -> dict:
    """
    Importa transações de fatura OFX em credit_transactions de forma idempotente.

    Cada tx_row deve ter:
      external_id, posted_at, valor, tipo ('despesa'|'estorno'),
      categoria, nota, installment_no (opcional), installments_total (opcional),
      memo_base (opcional — nome sem sufixo de parcela, para linking de grupo)

    Deduplicação: (user_id, card_id, external_id) WHERE source='ofx'.
    Parcelamentos: agrupa por group_id linkando parcelas anteriores pelo memo_base.
    Atualiza credit_limit no cartão se fornecido.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Verifica se arquivo já foi importado
            cur.execute(
                "select id from ofx_imports where user_id=%s and file_hash=%s",
                (user_id, file_hash),
            )
            if cur.fetchone():
                return {
                    "inserted": 0,
                    "duplicates": len(tx_rows),
                    "total": len(tx_rows),
                    "dt_start": dt_start,
                    "dt_end": dt_end,
                    "skipped_same_file": True,
                }

    # Criar/buscar fatura do período
    if dt_start and dt_end:
        bill_id = get_or_create_bill_by_period(user_id, card_id, dt_start, dt_end)
    else:
        from utils_date import today_tz
        bill_id = get_or_create_open_bill(user_id, card_id, today_tz())

    inserted = 0
    duplicates = 0

    with get_conn() as conn:
        with conn.cursor() as cur:
            for row in tx_rows:
                ext_id = row.get("external_id")
                posted_at = row["posted_at"]
                valor_row = Decimal(str(row["valor"]))

                # ── Deduplicação 1: pelo FITID (source=ofx) ──────────────────
                # Evita reimportar o mesmo arquivo duas vezes.
                if ext_id:
                    cur.execute(
                        "select id from credit_transactions "
                        "where user_id=%s and card_id=%s and source='ofx' and external_id=%s",
                        (user_id, card_id, ext_id),
                    )
                    if cur.fetchone():
                        duplicates += 1
                        continue

                # ── Deduplicação 2: por valor + data + parcela (anti-duplicata manual) ──
                # Cobre o caso em que o usuário cadastrou o parcelamento manualmente
                # e depois importa a fatura OFX. Sem isso, a parcela apareceria em dobro.
                inst_no = row.get("installment_no")
                inst_total = row.get("installments_total")
                if inst_no and inst_total:
                    cur.execute(
                        """
                        select id from credit_transactions
                        where user_id=%s and card_id=%s
                          and installment_no=%s
                          and installments_total=%s
                          and valor=%s
                          and purchased_at=%s
                        limit 1
                        """,
                        (user_id, card_id, inst_no, inst_total, valor_row, posted_at),
                    )
                    if cur.fetchone():
                        duplicates += 1
                        continue

                # ── Detectar/recuperar group_id para parcelamentos ────────────
                group_id = None
                memo_base = row.get("memo_base") or ""

                if inst_no and inst_total and inst_total > 1:
                    # Busca QUALQUER parcela já existente do mesmo grupo
                    # (mesmo memo base + mesmo total de parcelas).
                    # Funciona para OFX importado fora de ordem E para ligar
                    # parcelas do OFX a parcelamentos manuais (se o memo bater).
                    if memo_base:
                        cur.execute(
                            """
                            select group_id from credit_transactions
                            where user_id=%s and card_id=%s
                              and installments_total=%s
                              and group_id is not null
                              and lower(nota) like %s
                            order by purchased_at desc limit 1
                            """,
                            (
                                user_id, card_id,
                                inst_total,
                                f"%{memo_base[:25].lower()}%",
                            ),
                        )
                        existing = cur.fetchone()
                        if existing and existing["group_id"]:
                            group_id = existing["group_id"]

                    if group_id is None:
                        group_id = uuid4()

                is_refund = row.get("tipo") == "estorno"

                cur.execute(
                    """
                    insert into credit_transactions
                      (bill_id, user_id, card_id, tipo, valor, categoria, nota,
                       purchased_at, group_id, installment_no, installments_total,
                       is_refund, source, external_id)
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'ofx',%s)
                    """,
                    (
                        bill_id, user_id, card_id,
                        row.get("tipo", "despesa"), valor_row,
                        row.get("categoria"), row.get("nota"),
                        posted_at,
                        group_id, inst_no, inst_total,
                        is_refund, ext_id,
                    ),
                )
                inserted += 1

            # Recalcula total da fatura com base nas transações reais
            cur.execute(
                """
                update credit_bills
                set total = (
                    select coalesce(sum(valor), 0)
                    from credit_transactions
                    where bill_id=%s and is_refund=false
                )
                where id=%s
                """,
                (bill_id, bill_id),
            )

            # Atualiza limite de crédito do cartão se veio no OFX
            if credit_limit is not None and credit_limit > 0:
                cur.execute(
                    "update credit_cards set credit_limit=%s where id=%s and user_id=%s",
                    (credit_limit, card_id, user_id),
                )

            # Registra a importação no log
            cur.execute(
                """
                insert into ofx_imports
                  (user_id, file_hash, acct_id, acct_type, dt_start, dt_end,
                   total_transactions, inserted_count, duplicate_count)
                values (%s,%s,%s,'CREDITLINE',%s,%s,%s,%s,%s)
                on conflict (user_id, file_hash) do nothing
                """,
                (
                    user_id, file_hash, acct_id, dt_start, dt_end,
                    len(tx_rows), inserted, duplicates,
                ),
            )

        conn.commit()

    return {
        "inserted": inserted,
        "duplicates": duplicates,
        "total": len(tx_rows),
        "dt_start": dt_start,
        "dt_end": dt_end,
        "bill_id": bill_id,
        "skipped_same_file": False,
    }


def get_installment_group_summaries(user_id: int, group_ids: list) -> dict:
    """
    Dado uma lista de group_ids, retorna para cada grupo:
      - parcelas_total: total de parcelas da compra
      - parcelas_restantes: quantas faturas ainda estão abertas (status='open')
      - valor_restante: soma das parcelas ainda em aberto

    Retorna dict: str(group_id) → dict com as chaves acima.
    Faz UMA única query para todos os grupos (sem N+1).
    """
    if not group_ids:
        return {}

    str_ids = [str(g) for g in group_ids if g]
    if not str_ids:
        return {}

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select
                    t.group_id::text,
                    max(t.installments_total) as parcelas_total,
                    count(*) filter (where b.status = 'open')  as parcelas_restantes,
                    coalesce(
                        sum(t.valor) filter (where b.status = 'open'), 0
                    ) as valor_restante
                from credit_transactions t
                join credit_bills b on b.id = t.bill_id
                where t.user_id = %s
                  and t.group_id::text = any(%s)
                  and t.is_refund = false
                group by t.group_id
                """,
                (user_id, str_ids),
            )
            rows = cur.fetchall()

    return {
        str(r["group_id"]): {
            "parcelas_total": int(r["parcelas_total"] or 0),
            "parcelas_restantes": int(r["parcelas_restantes"] or 0),
            "valor_restante": float(r["valor_restante"] or 0),
        }
        for r in rows
    }


def monthly_summary_credit_debit(user_id: int, start: date, end: date):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select coalesce(sum(valor),0) as total_debito from launches "
                "where user_id=%s and tipo='despesa' and criado_em::date between %s and %s",
                (user_id, start, end),
            )
            deb = cur.fetchone()["total_debito"]

            cur.execute(
                "select coalesce(sum(valor),0) as total_credito from credit_transactions "
                "where user_id=%s and purchased_at between %s and %s",
                (user_id, start, end),
            )
            cred = cur.fetchone()["total_credito"]

            cur.execute(
                "select c.name, coalesce(sum(t.valor),0) as total "
                "from credit_transactions t join credit_cards c on c.id=t.card_id "
                "where t.user_id=%s and t.purchased_at between %s and %s "
                "group by c.name order by total desc",
                (user_id, start, end),
            )
            by_card = cur.fetchall()

    return {"debito": deb, "credito": cred, "por_cartao": by_card}
