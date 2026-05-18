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


_VALID_COLORS = {"purple", "coral", "gold", "green", "blue", "gray"}
_VALID_FLAGS  = {"Visa", "Mastercard", "Elo", "Amex", "Hipercard", "Outros"}


def _normalize_card_meta(color, flag, last4):
    """Normaliza color/flag/last4. Levanta ValueError se inválidos."""
    if color is not None:
        color = (color or "").strip().lower() or None
        if color and color not in _VALID_COLORS:
            raise ValueError(f"color_invalido:{color}")
    if flag is not None:
        flag = (flag or "").strip() or None
        if flag and flag not in _VALID_FLAGS:
            raise ValueError(f"flag_invalida:{flag}")
    if last4 is not None:
        last4 = (last4 or "").strip() or None
        if last4 and (len(last4) != 4 or not last4.isdigit()):
            raise ValueError(f"last4_invalido:{last4}")
    return color, flag, last4


def create_card(
    user_id: int,
    name: str,
    closing_day: int,
    due_day: int,
    color: str | None = None,
    flag: str | None = None,
    last4: str | None = None,
    credit_limit: float | None = None,
) -> int:
    ensure_user(user_id)
    name = (name or "").strip()
    if not name:
        raise ValueError("nome do cartão vazio")
    if card_name_exists(user_id, name):
        raise ValueError(f"nome_duplicado:{name}")

    color, flag, last4 = _normalize_card_meta(color, flag, last4)

    # Plan gate: blinda todos os canais. PlanLimitExceeded é capturado pelos
    # callers (bot tradicional, HTTP, IA conversacional).
    from core.services.plan_service import check_can_create_card
    check_can_create_card(user_id)

    limit_dec = Decimal(str(credit_limit)) if credit_limit is not None else None
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into credit_cards (user_id, name, closing_day, due_day, "
                "color, flag, last4, credit_limit) "
                "values (%s, %s, %s, %s, %s, %s, %s, %s) returning id",
                (user_id, name, int(closing_day), int(due_day), color, flag, last4, limit_dec),
            )
            card_id = cur.fetchone()["id"]
        conn.commit()
    return card_id


def update_card_meta(
    user_id: int,
    card_id: int,
    *,
    name: str | None = None,
    closing_day: int | None = None,
    due_day: int | None = None,
    color: str | None = None,
    flag: str | None = None,
    last4: str | None = None,
    credit_limit: float | None = None,
    clear_last4: bool = False,
    clear_limit: bool = False,
) -> bool:
    """Atualiza campos de um cartão. Só altera os passados (não-None).
    Use clear_last4=True / clear_limit=True pra limpar explicitamente."""
    sets: list[str] = []
    params: list = []

    if name is not None:
        n = (name or "").strip()
        if not n:
            raise ValueError("nome do cartão vazio")
        # Checa duplicado entre cartões DIFERENTES do mesmo user
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "select id from credit_cards where user_id=%s and name=%s and id<>%s",
                    (user_id, n, card_id),
                )
                if cur.fetchone():
                    raise ValueError(f"nome_duplicado:{n}")
        sets.append("name=%s"); params.append(n)

    if closing_day is not None:
        sets.append("closing_day=%s"); params.append(int(closing_day))
    if due_day is not None:
        sets.append("due_day=%s"); params.append(int(due_day))

    color_n, flag_n, last4_n = _normalize_card_meta(color, flag, last4)
    if color is not None:
        sets.append("color=%s"); params.append(color_n)
    if flag is not None:
        sets.append("flag=%s"); params.append(flag_n)
    if clear_last4:
        sets.append("last4=NULL")
    elif last4 is not None:
        sets.append("last4=%s"); params.append(last4_n)
    if clear_limit:
        sets.append("credit_limit=NULL")
    elif credit_limit is not None:
        sets.append("credit_limit=%s")
        params.append(Decimal(str(credit_limit)))

    if not sets:
        return False

    params.extend([user_id, card_id])
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"update credit_cards set {', '.join(sets)} where user_id=%s and id=%s",
                tuple(params),
            )
            updated = cur.rowcount > 0
        conn.commit()
    return updated


def get_card_delete_impact(user_id: int, card_id: int) -> dict:
    """Retorna o que será apagado se o cartão for excluído.
    {open_bill_total, future_installments_count, total_bills_count, total_transactions_count}"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id from credit_cards where id=%s and user_id=%s",
                (card_id, user_id),
            )
            if not cur.fetchone():
                return {}

            cur.execute(
                "select coalesce(sum(total - paid_amount), 0) as v "
                "from credit_bills where card_id=%s and user_id=%s and status<>'paid'",
                (card_id, user_id),
            )
            open_bill_total = float(cur.fetchone()["v"] or 0)

            cur.execute(
                "select count(*) as n from credit_transactions "
                "where card_id=%s and user_id=%s and purchased_at > current_date "
                "and is_refund=false and installment_no is not null",
                (card_id, user_id),
            )
            future_installments = int(cur.fetchone()["n"] or 0)

            cur.execute(
                "select count(*) as n from credit_bills where card_id=%s and user_id=%s",
                (card_id, user_id),
            )
            total_bills = int(cur.fetchone()["n"] or 0)

            cur.execute(
                "select count(*) as n from credit_transactions where card_id=%s and user_id=%s",
                (card_id, user_id),
            )
            total_tx = int(cur.fetchone()["n"] or 0)

    return {
        "open_bill_total": open_bill_total,
        "future_installments_count": future_installments,
        "total_bills_count": total_bills,
        "total_transactions_count": total_tx,
    }


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
                       c.credit_limit, c.color, c.flag, c.last4, c.display_order,
                       (u.default_card_id = c.id) as is_default
                from credit_cards c
                left join users u on u.id = c.user_id
                where c.user_id = %s
                order by c.display_order nulls last, c.name
                """,
                (user_id,),
            )
            return cur.fetchall()


def reorder_cards(user_id: int, ordered_ids: list[int]) -> int:
    """
    Atribui display_order sequencial (0..N-1) aos cartões na ordem recebida.
    Cartões do user que não estiverem na lista mantêm display_order atual
    (cliente sempre envia a lista completa, mas defensivo).
    Retorna quantidade de linhas afetadas.
    """
    if not ordered_ids:
        return 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            updated = 0
            for idx, card_id in enumerate(ordered_ids):
                cur.execute(
                    "update credit_cards set display_order = %s "
                    "where id = %s and user_id = %s",
                    (idx, int(card_id), user_id),
                )
                updated += cur.rowcount
            conn.commit()
            return updated


def get_card_by_id(user_id: int, card_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select c.id, c.name, c.closing_day, c.due_day,
                       c.reminders_enabled, c.reminders_days_before, c.reminder_last_sent_on,
                       c.credit_limit, c.color, c.flag, c.last4,
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
                "select id, status, coalesce(paid_amount, 0) as paid_amount from credit_bills "
                "where user_id=%s and card_id=%s and period_start=%s and period_end=%s",
                (user_id, card_id, period_start, period_end),
            )
            row = cur.fetchone()
            if row:
                bid = int(row["id"])
                status = (row.get("status") or "").lower()
                paid = Decimal(str(row.get("paid_amount") or 0))
                # Bill existe mas tá fechada sem pagamento real (zumbi do
                # undo de uma compra anterior) — reabre pra receber a nova.
                if status in ("paid", "closed") and paid == 0:
                    cur.execute(
                        "update credit_bills set status='open', paid_at=null "
                        "where id=%s and user_id=%s",
                        (bid, user_id),
                    )
                    conn.commit()
                return bid

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

                # Bill que estava 'paid/closed' (paga total ou parcial) e agora
                # ganhou uma parcela nova precisa voltar a aparecer em
                # `list_open_bills`. Sem isso, a fatura sumia da listagem mesmo
                # tendo saldo devedor — bug visto em prod (fatura de julho com
                # parcela nova sumindo).
                cur.execute(
                    "update credit_bills set status='open', paid_at=null "
                    "where id=%s and status in ('paid','closed') "
                    "and total > coalesce(paid_amount, 0)",
                    (bill_id,),
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


def update_credit_transaction_fields(
    user_id: int,
    ct_id: int,
    *,
    categoria: str | None = None,
    nota: str | None = None,
) -> bool:
    """Atualiza categoria e/ou nota de uma compra no crédito.

    Não mexe em valor, cartão ou data — esses ficariam fora de scope (mudariam
    o saldo da fatura ou a janela de fechamento).

    Comportamento de parcelamento: se a transação pertence a um grupo
    (group_id != NULL), a alteração propaga pra TODAS as parcelas do grupo.
    Justificativa: parcelas do mesmo grupo são da mesma compra lógica;
    editar categoria de uma e não das outras gera inconsistência (relatórios
    por categoria viram errados).

    Retorna True se algo foi alterado, False se não encontrou.
    """
    sets: list[str] = []
    params: list = []
    if categoria is not None:
        sets.append("categoria = %s")
        params.append(categoria)
    if nota is not None:
        sets.append("nota = %s")
        params.append(nota)
    if not sets:
        return False

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select group_id from credit_transactions where user_id = %s and id = %s",
                (user_id, ct_id),
            )
            row = cur.fetchone()
            if not row:
                return False
            group_id = row.get("group_id")

            if group_id:
                # Parcelado — propaga pra todas do grupo
                cur.execute(
                    f"update credit_transactions set {', '.join(sets)} "
                    f"where user_id = %s and group_id = %s::uuid",
                    [*params, user_id, group_id],
                )
            else:
                cur.execute(
                    f"update credit_transactions set {', '.join(sets)} "
                    f"where user_id = %s and id = %s",
                    [*params, user_id, ct_id],
                )
        conn.commit()
    return True


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
                # Só fecha como 'paid' se houve pagamento DE FATO (paid > 0).
                # Sem isso, esvaziar uma fatura (total=0, paid=0) deixava a bill
                # zumbi como 'paid' — `list_open_bills` filtra por status='open'
                # e a fatura sumia do dashboard. Pior: a próxima compra no mesmo
                # período reabria o id antigo mas continuava 'paid'.
                if paid > 0 and paid >= total:
                    cur.execute(
                        "update credit_bills set status='paid', paid_at=now() "
                        "where id=%s and user_id=%s",
                        (bill_id, user_id),
                    )

            conn.commit()

    return {"mode": "single", "ct_id": ct_id, "removed_total": float(v), "removed_count": 1}


def undo_installment_group(user_id: int, group_id: str):
    """Apaga um parcelamento. Comportamento Option B (decidido 2026-05-14):

    - Tx em faturas ABERTAS: DELETADAS. Open bill total cai.
      Saldo do mês "volta" naturalmente porque despesa daquela parcela some.
    - Tx em faturas PAGAS/FECHADAS: MANTIDAS como órfãs (group_id=null),
      nota ganha sufixo "[Parcelamento removido em DD/MM]". Faturas pagas
      ficam intactas — dinheiro já saiu da conta, não volta.
    - NUNCA cria refund launch. Se user precisa corrigir cagada do passado,
      cria lançamento de ajuste manual.

    Edge case raro: open bill com paid_amount > new_total (user pagou
    parcial e parcelamento ocupava boa parte da fatura). Clampa paid=total,
    bill vira 'paid'. Diferença ignorada — sem refund. User pode criar
    ajuste manual se sentir falta.

    Mudança de comportamento (era refundar paid_amount via launch).
    """
    today = date.today()
    note_suffix = f" [Parcelamento removido em {today.strftime('%d/%m/%Y')}]"
    card_name: str | None = None

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select t.id, t.bill_id, t.valor, t.card_id, t.nota,
                       b.status as bill_status
                from credit_transactions t
                join credit_bills b on b.id = t.bill_id
                where t.user_id = %s and t.group_id = %s::uuid and t.is_refund = false
                """,
                (user_id, group_id),
            )
            rows = cur.fetchall()
            if not rows:
                return None

            tx_to_delete: list[dict] = []
            tx_to_orphan: list[dict] = []
            for r in rows:
                if r["bill_status"] == "open":
                    tx_to_delete.append(r)
                else:
                    tx_to_orphan.append(r)

            cur.execute(
                "select name from credit_cards where id = %s",
                (rows[0]["card_id"],),
            )
            row_card = cur.fetchone()
            card_name = row_card["name"] if row_card else "cartão"

            removed_total = Decimal("0")
            orphaned_total = Decimal("0")
            by_bill_delete: dict[int, Decimal] = {}
            for r in tx_to_delete:
                v = Decimal(str(r["valor"]))
                removed_total += v
                by_bill_delete[r["bill_id"]] = by_bill_delete.get(r["bill_id"], Decimal("0")) + v
            for r in tx_to_orphan:
                orphaned_total += Decimal(str(r["valor"]))

            if tx_to_delete:
                ids = [r["id"] for r in tx_to_delete]
                cur.execute(
                    "delete from credit_transactions where id = any(%s)",
                    (ids,),
                )

            for r in tx_to_orphan:
                old_nota = r["nota"] or ""
                new_nota = (old_nota + note_suffix).strip()
                cur.execute(
                    "update credit_transactions set group_id = null, nota = %s where id = %s",
                    (new_nota, r["id"]),
                )

            for bill_id, bill_sum in by_bill_delete.items():
                cur.execute(
                    "select total, coalesce(paid_amount, 0) as paid_amount "
                    "from credit_bills where id = %s and user_id = %s for update",
                    (bill_id, user_id),
                )
                row = cur.fetchone()
                if not row:
                    continue
                old_total = Decimal(str(row["total"]))
                old_paid = Decimal(str(row["paid_amount"]))
                new_total = max(Decimal("0"), old_total - bill_sum)

                if new_total > 0 and old_paid >= new_total:
                    cur.execute(
                        "update credit_bills set total = %s, paid_amount = %s, "
                        "status='paid', paid_at=now() "
                        "where id = %s and user_id = %s",
                        (new_total, new_total, bill_id, user_id),
                    )
                elif new_total > old_paid:
                    cur.execute(
                        "update credit_bills set total = %s "
                        "where id = %s and user_id = %s",
                        (new_total, bill_id, user_id),
                    )
                else:
                    new_paid = min(old_paid, new_total)
                    cur.execute(
                        "update credit_bills set total = %s, paid_amount = %s "
                        "where id = %s and user_id = %s",
                        (new_total, new_paid, bill_id, user_id),
                    )

            conn.commit()

    return {
        "group_id": group_id,
        "removed_count": len(tx_to_delete),
        "orphaned_count": len(tx_to_orphan),
        "removed_total": float(removed_total),
        "orphaned_total": float(orphaned_total),
        "refunded": 0.0,
        "refund_launch_id": None,
        "card_name": card_name,
    }


def get_installment_group_delete_impact(user_id: int, group_id: str):
    """Retorna o impacto de deletar o parcelamento (sem deletar).

    Frontend usa pra escolher entre 2 mensagens de confirmação:
    - sem parcelas pagas: explica que fatura aberta diminui, sem impacto na conta
    - com parcelas pagas: avisa que R$ Y já pagos NÃO voltam, sugere lançamento manual
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select
                    count(*) filter (where b.status = 'open') as future_count,
                    coalesce(sum(t.valor) filter (where b.status = 'open'), 0) as future_total,
                    count(*) filter (where b.status != 'open') as paid_count,
                    coalesce(sum(t.valor) filter (where b.status != 'open'), 0) as paid_total,
                    coalesce(sum(t.valor), 0) as full_total,
                    max(t.installments_total) as installments_total,
                    min(t.nota) as nota,
                    max(c.name) as card_name
                from credit_transactions t
                join credit_bills b on b.id = t.bill_id
                join credit_cards c on c.id = t.card_id
                where t.user_id = %s and t.group_id = %s::uuid and t.is_refund = false
                """,
                (user_id, group_id),
            )
            row = cur.fetchone()
            if not row or int(row["future_count"] or 0) + int(row["paid_count"] or 0) == 0:
                return None

    return {
        "group_id": group_id,
        "future_count": int(row["future_count"] or 0),
        "future_total": float(row["future_total"] or 0),
        "paid_count": int(row["paid_count"] or 0),
        "paid_total": float(row["paid_total"] or 0),
        "full_total": float(row["full_total"] or 0),
        "installments_total": int(row["installments_total"] or 0),
        "nota": row["nota"],
        "card_name": row["card_name"],
    }


def anticipate_installment(user_id: int, group_id: str):
    """Antecipa a próxima parcela pendente do parcelamento.

    Option A (decidido 2026-05-14): "antecipar" = paguei à vista.
    - Identifica a próxima parcela pendente (menor period_end + menor installment_no)
    - DELETA a tx do parcelamento
    - REDUZ total da fatura aberta correspondente
    - CRIA launch (despesa) na conta corrente com mesmo valor/categoria
      e nota explicitando que foi antecipação. is_internal_movement=False
      pra contar nas analytics do user (preserva categoria original).

    Retorna None se não há parcela pendente.
    """
    today = date.today()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select t.id, t.bill_id, t.valor, t.categoria, t.nota,
                       t.installment_no, t.installments_total, t.card_id,
                       c.name as card_name
                from credit_transactions t
                join credit_bills b on b.id = t.bill_id
                join credit_cards c on c.id = t.card_id
                where t.user_id = %s and t.group_id = %s::uuid
                  and t.is_refund = false and b.status = 'open'
                order by b.period_end asc, t.installment_no asc
                limit 1
                for update of t
                """,
                (user_id, group_id),
            )
            tx = cur.fetchone()
            if not tx:
                return None

            valor = Decimal(str(tx["valor"]))
            bill_id = tx["bill_id"]

            cur.execute(
                "delete from credit_transactions where id = %s",
                (tx["id"],),
            )

            cur.execute(
                "select total, coalesce(paid_amount, 0) as paid_amount "
                "from credit_bills where id = %s and user_id = %s for update",
                (bill_id, user_id),
            )
            bill = cur.fetchone()
            old_total = Decimal(str(bill["total"]))
            old_paid = Decimal(str(bill["paid_amount"]))
            new_total = max(Decimal("0"), old_total - valor)

            if new_total > 0 and old_paid >= new_total:
                cur.execute(
                    "update credit_bills set total = %s, paid_amount = %s, "
                    "status='paid', paid_at=now() where id = %s and user_id = %s",
                    (new_total, new_total, bill_id, user_id),
                )
            else:
                cur.execute(
                    "update credit_bills set total = %s where id = %s and user_id = %s",
                    (new_total, bill_id, user_id),
                )

            conn.commit()

    inst_no = tx.get("installment_no") or 0
    inst_total = tx.get("installments_total") or 0
    original_nota = (tx.get("nota") or "").strip()
    nota_str = f"Antecipou parcela {inst_no}/{inst_total}"
    if original_nota:
        nota_str += f" — {original_nota}"
    nota_str += f" ({today.strftime('%d/%m/%Y')})"

    launch_id, _seq, _bal = add_launch_and_update_balance(
        user_id=user_id,
        tipo="despesa",
        valor=float(valor),
        alvo=f"antecipacao:{tx['card_name']}",
        nota=nota_str,
        categoria=tx.get("categoria") or "outros",
    )

    return {
        "group_id": group_id,
        "anticipated_installment_no": inst_no,
        "installments_total": inst_total,
        "valor": float(valor),
        "launch_id": launch_id,
        "card_name": tx["card_name"],
        "nota": nota_str,
    }


def list_installment_groups_detailed(user_id: int, sort: str = "urgency"):
    """Lista parcelamentos ativos do user com detalhe por parcela.

    Cada grupo inclui lista 'parcelas' ordenada por installment_no. Cada
    parcela: tx_id, installment_no, valor, is_paid, is_next, due_date.

    sort:
      - 'urgency' (default): pendentes primeiro, próxima parcela ASC,
        tiebreaker compra recente DESC. Parcelamentos 100% pagos vão pro fim.
      - 'recent': compra mais recente DESC.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select
                    t.id as tx_id,
                    t.group_id::text as group_id, t.bill_id, t.valor,
                    t.categoria, t.nota,
                    t.installment_no, t.installments_total, t.purchased_at,
                    t.card_id, c.name as card_name, c.color as card_color,
                    c.flag as card_flag, c.last4 as card_last4,
                    c.closing_day, c.due_day,
                    b.period_end, b.status as bill_status
                from credit_transactions t
                join credit_cards c on c.id = t.card_id
                join credit_bills b on b.id = t.bill_id
                where t.user_id = %s and t.group_id is not null and t.is_refund = false
                order by t.group_id, t.installment_no asc nulls last, b.period_end asc
                """,
                (user_id,),
            )
            rows = cur.fetchall()

    def _due_date(period_end: date, closing_day: int, due_day: int) -> date:
        if due_day >= closing_day:
            return _safe_date(period_end.year, period_end.month, due_day)
        m = period_end.month + 1
        y = period_end.year + (m - 1) // 12
        m = (m - 1) % 12 + 1
        return _safe_date(y, m, due_day)

    groups: dict[str, dict] = {}
    for r in rows:
        gid = r["group_id"]
        if gid not in groups:
            groups[gid] = {
                "group_id": gid,
                "name": (r["nota"] or "Parcelamento").strip(),
                "categoria": r["categoria"],
                "card_id": int(r["card_id"]),
                "card_name": r["card_name"],
                "card_color": r["card_color"],
                "card_flag": r["card_flag"],
                "card_last4": r["card_last4"],
                "installments_total": int(r["installments_total"] or 0),
                "purchased_at": r["purchased_at"].isoformat() if r["purchased_at"] else None,
                "parcelas": [],
            }
        is_paid = r["bill_status"] != "open"
        due = _due_date(r["period_end"], int(r["closing_day"]), int(r["due_day"]))
        groups[gid]["parcelas"].append({
            "tx_id": int(r["tx_id"]),
            "installment_no": int(r["installment_no"] or 0),
            "valor": float(r["valor"]),
            "is_paid": is_paid,
            "is_next": False,
            "due_date": due.isoformat(),
            "period_end": r["period_end"].isoformat(),
            "bill_id": int(r["bill_id"]),
        })

    out = []
    for gid, g in groups.items():
        parcelas = g["parcelas"]
        parcelas.sort(key=lambda p: (p["installment_no"], p["due_date"]))

        total = sum(p["valor"] for p in parcelas)
        paid_total = sum(p["valor"] for p in parcelas if p["is_paid"])
        n_paid = sum(1 for p in parcelas if p["is_paid"])

        next_due = None
        for p in parcelas:
            if not p["is_paid"]:
                p["is_next"] = True
                next_due = p["due_date"]
                break

        g["total"] = total
        g["paid_amount"] = paid_total
        g["remaining_amount"] = total - paid_total
        g["n_paid"] = n_paid
        g["n_pending"] = len(parcelas) - n_paid
        g["next_due_date"] = next_due
        g["valor_parcela"] = parcelas[0]["valor"] if parcelas else 0
        out.append(g)

    if sort == "urgency":
        # Sort estável: 1º por compra DESC (tiebreaker), depois por urgência.
        out.sort(key=lambda g: g["purchased_at"] or "0000-01-01", reverse=True)
        out.sort(key=lambda g: (
            0 if g["n_pending"] > 0 else 1,
            g["next_due_date"] or "9999-12-31",
        ))
    else:
        out.sort(key=lambda g: g["purchased_at"] or "0000-01-01", reverse=True)

    return out


def update_installment_group_meta(user_id: int, group_id: str,
                                  nome: str | None = None,
                                  categoria: str | None = None) -> bool:
    """Edita nome (nota) e/ou categoria em TODAS as tx do parcelamento.

    Retorna True se algo foi alterado.
    """
    if nome is None and categoria is None:
        return False

    sets = []
    params: list = []
    if nome is not None:
        sets.append("nota = %s")
        params.append((nome or "").strip() or None)
    if categoria is not None:
        sets.append("categoria = %s")
        params.append((categoria or "").strip() or None)
    params.extend([user_id, group_id])

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"update credit_transactions set {', '.join(sets)} "
                f"where user_id = %s and group_id = %s::uuid and is_refund = false",
                tuple(params),
            )
            n = cur.rowcount
            conn.commit()

    return n > 0


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

        # Pagamento de fatura é movimentação interna. `extra_efeitos`
        # carrega `bill_id` + `paid_amount_added` pra `delete_launch_and_
        # rollback` reverter o `paid_amount` da bill se o user apagar o
        # lançamento de pagamento do histórico.
        launch_id, _user_seq, new_balance = add_launch_and_update_balance(
            user_id=user_id,
            tipo="despesa",
            valor=float(pay),
            alvo=f"fatura:{card_name}",
            nota=f"Pagamento de fatura ({card_name})",
            categoria="pagamento_fatura",
            is_internal_movement=True,
            extra_efeitos={
                "bill_id": int(bill["id"]),
                "paid_amount_added": float(pay),
            },
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


def rebuild_bill_totals(
    user_id: int,
    *,
    refund_overpayments: bool = False,
) -> dict[str, int | float]:
    """Reconciliação FORTE: recalcula `total` de cada bill do user a partir
    de `sum(credit_transactions.valor)` e reabre bills que voltam a ter
    saldo devedor.

    Diferente da reconciliação preguiçosa em `list_open_bills` (que confia
    no `total` armazenado), esse helper recalcula do zero. Cobre casos
    onde o `total` ficou inconsistente por bug passado, edição manual no
    DB, ou rollback que esqueceu de decrementar.

    `refund_overpayments` controla o que fazer com bills onde
    `paid_amount > total` (overpayment fantasma, geralmente legado de
    `undo_installment_group` antigo que não revertia paid_amount):
      - False (default): clampa `paid_amount = total` silenciosamente.
        Perde a memória do pagamento extra, mas estado fica consistente.
      - True: clampa E cria UM launch de receita (per cartão) com o
        excesso somado, devolvendo o dinheiro pra conta corrente. Use
        quando o DB do user tem lixo herdado pra normalizar de vez.

    Uso pelo CLI:
        from db import rebuild_bill_totals
        print(rebuild_bill_totals(<user_id>, refund_overpayments=True))

    Retorna `{totals_updated, reopened, paid_clamped, refunded}`.
    """
    overpayments_by_card: dict[int, tuple[Decimal, str]] = {}

    with get_conn() as conn:
        with conn.cursor() as cur:
            # Passo 1: recalcular total = SUM(credit_transactions.valor)
            # pra cada bill do user. Retorna IDs onde houve mudança.
            cur.execute(
                """
                with computed as (
                    select b.id as bill_id,
                           coalesce(sum(t.valor), 0) as new_total
                      from credit_bills b
                      left join credit_transactions t on t.bill_id = b.id
                     where b.user_id = %s
                     group by b.id
                )
                update credit_bills b
                   set total = c.new_total
                  from computed c
                 where b.id = c.bill_id
                   and b.total <> c.new_total
                returning b.id
                """,
                (user_id,),
            )
            totals_updated = len(cur.fetchall())

            # Passo 2: detectar e clampar overpayments (paid_amount > total).
            # Antes do clamp, captura o excesso por cartão pra opcionalmente
            # estornar na conta corrente.
            cur.execute(
                """
                select b.id, b.card_id, c.name as card_name,
                       coalesce(b.paid_amount, 0) - b.total as overpaid
                  from credit_bills b
                  join credit_cards c on c.id = b.card_id
                 where b.user_id = %s
                   and coalesce(b.paid_amount, 0) > b.total
                """,
                (user_id,),
            )
            paid_clamped = 0
            for row in cur.fetchall():
                paid_clamped += 1
                if refund_overpayments:
                    over = Decimal(str(row["overpaid"]))
                    cid = int(row["card_id"])
                    if cid in overpayments_by_card:
                        acc, _ = overpayments_by_card[cid]
                        overpayments_by_card[cid] = (acc + over, row["card_name"])
                    else:
                        overpayments_by_card[cid] = (over, row["card_name"])

            cur.execute(
                """
                update credit_bills
                   set paid_amount = total
                 where user_id = %s
                   and coalesce(paid_amount, 0) > total
                """,
                (user_id,),
            )

            # Passo 3: reabrir bills paid/closed que (após clamp) ainda tem saldo.
            cur.execute(
                """
                update credit_bills
                   set status='open', paid_at=null
                 where user_id=%s
                   and status in ('paid','closed')
                   and total > coalesce(paid_amount, 0)
                returning id
                """,
                (user_id,),
            )
            reopened = len(cur.fetchall())
        conn.commit()

    # Passo 4: estorno opcional. Fora da transação anterior pra evitar
    # bloqueios cruzados com `add_launch_and_update_balance`.
    refunded_total = Decimal("0")
    if refund_overpayments:
        for cid, (amount, card_name) in overpayments_by_card.items():
            if amount <= 0:
                continue
            add_launch_and_update_balance(
                user_id=user_id,
                tipo="receita",
                valor=float(amount),
                alvo=f"estorno_fatura:{card_name}",
                nota=f"Estorno de pagamento ({card_name}) — reconciliação retroativa",
                categoria="estorno_pagamento_fatura",
                is_internal_movement=True,
            )
            refunded_total += amount

    return {
        "totals_updated": totals_updated,
        "reopened": reopened,
        "paid_clamped": paid_clamped,
        "refunded": float(refunded_total),
    }


def list_open_bills(user_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Reconciliação preguiçosa: bill `paid` que voltou a ter saldo
            # (estorno, parcelamento que caiu no período de uma bill paga,
            # edição) volta pra `open`. Cobre casos onde o caller esqueceu
            # de reabrir após mexer no `total`.
            #
            # `closed` NÃO é reaberta aqui: fatura que já fechou (period_end
            # passou) com saldo residual é débito atrasado, não fatura
            # corrente. Pra consumir essas, use `list_bills_with_debt`.
            cur.execute(
                """
                update credit_bills
                   set status='open', paid_at=null
                 where user_id=%s
                   and status = 'paid'
                   and total > coalesce(paid_amount, 0)
                """,
                (user_id,),
            )

            cur.execute(
                """
                select b.id, b.card_id, c.name as card_name, b.period_start, b.period_end,
                       b.total, coalesce(b.paid_amount, 0) as paid_amount, b.status
                from credit_bills b
                join credit_cards c on c.id = b.card_id
                where b.user_id=%s and b.status='open'
                order by b.period_end asc, c.name asc
                """,
                (user_id,),
            )
            rows = cur.fetchall()
        conn.commit()
    return rows


def list_bills_with_debt(user_id: int):
    """Bills com saldo a pagar: faturas atuais (`open`) e atrasadas
    (`closed` com `total > paid_amount`).

    Usada pelo `get_total_debt` da IA pra agregar TUDO que o user deve no
    cartão, separando o que ainda tá em curso do que já passou e segue
    em aberto. Cada row tem `is_overdue=True` quando status='closed'.

    Não inclui `paid` (mesmo com inconsistência) — pra reabrir paid com
    saldo, chame `list_open_bills` antes (faz reconciliação preguiçosa).
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select b.id, b.card_id, c.name as card_name,
                       b.period_start, b.period_end,
                       b.total, coalesce(b.paid_amount, 0) as paid_amount,
                       b.status,
                       (b.status = 'closed') as is_overdue
                from credit_bills b
                join credit_cards c on c.id = b.card_id
                where b.user_id=%s
                  and b.status in ('open', 'closed')
                  and b.total > coalesce(b.paid_amount, 0)
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
    """Grupos de parcelamento do user, agregando bills pendentes.

    `upcoming_due_dates` é a lista (ordenada) das datas de vencimento das
    parcelas pendentes — calculada por cartão: se `due_day >= closing_day`,
    vence no mesmo mês do period_end; senão vence no mês seguinte. Usada
    no display de "meus parcelamentos" pra mostrar quando cada parcela cai.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select t.group_id, c.name as card_name,
                       c.closing_day, c.due_day,
                       max(t.installments_total) as n_total,
                       count(*) as n_registered,
                       sum(t.valor) as total,
                       sum(case when b.status = 'open' then t.valor else 0 end) as total_pending,
                       count(case when b.status = 'open' then 1 end) as n_pending,
                       max(t.purchased_at) as last_purchase,
                       min(t.nota) as nota,
                       array_remove(
                         array_agg(
                           case when b.status = 'open' then b.period_end end
                           order by b.period_end asc
                         ),
                         null
                       ) as pending_period_ends
                from credit_transactions t
                join credit_cards c on c.id = t.card_id
                join credit_bills b on b.id = t.bill_id
                where t.user_id=%s and t.group_id is not null and t.is_refund=false
                group by t.group_id, c.name, c.closing_day, c.due_day
                order by max(t.purchased_at) desc
                limit %s
                """,
                (user_id, limit),
            )
            rows = cur.fetchall()

    def _due_date(period_end: date, closing_day: int, due_day: int) -> date:
        if due_day >= closing_day:
            return date(period_end.year, period_end.month, due_day)
        m = period_end.month + 1
        y = period_end.year + (m - 1) // 12
        m = (m - 1) % 12 + 1
        return date(y, m, due_day)

    for r in rows:
        closing_day = int(r["closing_day"])
        due_day = int(r["due_day"])
        period_ends = r.get("pending_period_ends") or []
        r["upcoming_due_dates"] = [
            _due_date(pe, closing_day, due_day) for pe in period_ends
        ]
    return rows


def consolidate_duplicate_bills(user_id: int, card_id: int, closing_day: int) -> int:
    """
    Mescla credit_bills duplicadas do mesmo cartão que caem no mesmo mês de fechamento.

    Isso pode acontecer quando o OFX foi importado antes do fix de period, gerando
    uma bill com datas brutas do arquivo e outra calculada pelo closing_day.

    Estratégia:
      - Agrupa as bills abertas por (ano, mês) do period_end
      - Se mais de uma bill no mesmo mês: mantém a com mais transações, move
        todas as transações das outras para ela, deleta as duplicadas
      - Recalcula total da bill vencedora

    Retorna o número de merges realizados.
    """
    from collections import defaultdict

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, period_start, period_end, total
                from credit_bills
                where user_id=%s and card_id=%s and status='open'
                order by period_end
                """,
                (user_id, card_id),
            )
            bills = cur.fetchall()

        if not bills:
            return 0

        # Agrupa por (ano, mês) do period_end
        by_month: dict[tuple, list] = defaultdict(list)
        for b in bills:
            key = (b["period_end"].year, b["period_end"].month)
            by_month[key].append(b)

        merges = 0
        with conn.cursor() as cur:
            for (y, m), group in by_month.items():
                if len(group) <= 1:
                    continue

                # Bill canônica: prefere a cujo period_end coincide com o closing_day
                import calendar as _cal
                last = _cal.monthrange(y, m)[1]
                canonical_end = date(y, m, min(closing_day, last))
                canonical = next((b for b in group if b["period_end"] == canonical_end), None)

                # Se nenhuma bate exatamente, pega a com mais transações
                if canonical is None:
                    cur.execute(
                        "select bill_id, count(*) as cnt from credit_transactions "
                        "where bill_id = any(%s) group by bill_id order by cnt desc limit 1",
                        ([b["id"] for b in group],),
                    )
                    row = cur.fetchone()
                    winner_id = row["bill_id"] if row else group[0]["id"]
                    canonical = next(b for b in group if b["id"] == winner_id)

                others = [b for b in group if b["id"] != canonical["id"]]

                for other in others:
                    # Move transações para a bill canônica
                    cur.execute(
                        "update credit_transactions set bill_id=%s where bill_id=%s",
                        (canonical["id"], other["id"]),
                    )
                    # Remove a bill duplicada
                    cur.execute(
                        "delete from credit_bills where id=%s",
                        (other["id"],),
                    )
                    merges += 1

                # Recalcula total da bill canônica
                cur.execute(
                    """
                    update credit_bills
                    set total = (
                        select coalesce(sum(case when is_refund=false then valor else -abs(valor) end), 0)
                        from credit_transactions
                        where bill_id=%s
                    )
                    where id=%s
                    """,
                    (canonical["id"], canonical["id"]),
                )

        conn.commit()

    return merges


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
    O limite vindo do OFX é só informativo no relatório de importação. Não
    gravamos automaticamente no cartão porque bancos podem exportar campos de
    limite/disponível que não refletem o limite real contratado pelo usuário.
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

    # Busca o closing_day do cartão para calcular o período correto de cada transação.
    # NÃO usamos dt_start/dt_end do OFX diretamente — esses valores raramente
    # coincidem com o período calculado pelo closing_day, o que criaria bills
    # duplicadas para o mesmo mês quando o usuário já tem transações manuais.
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select closing_day from credit_cards where id=%s and user_id=%s limit 1",
                (card_id, user_id),
            )
            card_row = cur.fetchone()
    if not card_row:
        raise ValueError("Cartão não encontrado.")
    closing_day = int(card_row["closing_day"])

    # Cache de bill_ids por (period_start, period_end) para evitar queries repetidas
    _bill_cache: dict[tuple, int] = {}

    def _get_bill_for_date(ref_date: date) -> int:
        ps, pe = billing_period_for_close_day(ref_date, closing_day)
        key = (ps, pe)
        if key not in _bill_cache:
            _bill_cache[key] = get_or_create_bill_by_period(user_id, card_id, ps, pe)
        return _bill_cache[key]

    inserted = 0
    duplicates = 0
    affected_bill_ids: set[int] = set()

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

                tx_bill_id = _get_bill_for_date(posted_at)
                affected_bill_ids.add(tx_bill_id)

                cur.execute(
                    """
                    insert into credit_transactions
                      (bill_id, user_id, card_id, tipo, valor, categoria, nota,
                       purchased_at, group_id, installment_no, installments_total,
                       is_refund, source, external_id)
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'ofx',%s)
                    """,
                    (
                        tx_bill_id, user_id, card_id,
                        row.get("tipo", "despesa"), valor_row,
                        row.get("categoria"), row.get("nota"),
                        posted_at,
                        group_id, inst_no, inst_total,
                        is_refund, ext_id,
                    ),
                )
                inserted += 1

            # Recalcula total de cada fatura que recebeu transações novas
            for bid in affected_bill_ids:
                cur.execute(
                    """
                    update credit_bills
                    set total = (
                        select coalesce(sum(case when is_refund=false then valor else -abs(valor) end), 0)
                        from credit_transactions
                        where bill_id=%s
                    )
                    where id=%s
                    """,
                    (bid, bid),
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

    # bill_id principal = primeira fatura afetada (compatibilidade com chamadores)
    main_bill_id = next(iter(affected_bill_ids), None)

    # Consolida bills duplicadas que possam ter sido criadas em importações anteriores
    # (antes do fix de closing_day). Seguro de chamar sempre — sem-op se não há dups.
    consolidate_duplicate_bills(user_id, card_id, closing_day)

    # Após consolidação, o main_bill_id pode ter mudado — busca a bill canônica do mês
    if dt_end:
        ps, pe = billing_period_for_close_day(dt_end, closing_day)
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "select id from credit_bills where user_id=%s and card_id=%s "
                    "and period_end=%s limit 1",
                    (user_id, card_id, pe),
                )
                row = cur.fetchone()
                if row:
                    main_bill_id = row["id"]

    return {
        "inserted": inserted,
        "duplicates": duplicates,
        "total": len(tx_rows),
        "dt_start": dt_start,
        "dt_end": dt_end,
        "bill_id": main_bill_id,
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
