"""
core/services/recurring_charger.py — Cobra gastos fixos automaticamente.

Roda como background task no startup. Verifica a cada hora se há
`recurring_expenses` com `due_day <= today` e não cobrados neste mês.
Cria launch (account) ou credit_transaction (cartão) + registra em
`recurring_charges` (idempotência via UNIQUE(recurring_id, ym)).

Cobrança em cartão de crédito:
- Acha a `credit_bills` open atual do cartão (status='open', period contendo today).
- Se não existir, dispara `ensure_open_bill_for_today` (deixa o fluxo padrão criar).
- Adiciona tx + atualiza bill.total.
"""
from __future__ import annotations

import asyncio
import sys
import traceback
from datetime import date, datetime
from decimal import Decimal

from db.connection import get_conn


async def run_recurring_charger_loop():
    """Loop infinito: verifica e cobra a cada hora."""
    while True:
        try:
            await asyncio.sleep(5)  # delay inicial pra não pegar startup
            await asyncio.to_thread(charge_due_recurring_expenses_once)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[recurring_charger] erro: {exc}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
        await asyncio.sleep(60 * 60)  # 1 hora entre verificações


def charge_due_recurring_expenses_once(today: date | None = None) -> list[dict]:
    """Roda uma passada. Retorna lista de cobranças efetuadas (pra logging).
    Pulável e idempotente — pode rodar várias vezes no mesmo dia sem duplicar.
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
                  and (r.last_charged_ym is null or r.last_charged_ym <> %s)
                """,
                (today.day, ym),
            )
            due = cur.fetchall() or []

    results: list[dict] = []
    for r in due:
        try:
            result = _charge_one(dict(r), today, ym)
            results.append(result)
        except Exception as exc:
            print(
                f"[recurring_charger] falhou cobrar rec={r['id']} user={r['user_id']}: {exc}",
                file=sys.stderr,
            )
            traceback.print_exc(file=sys.stderr)

    if results:
        print(f"[recurring_charger] {len(results)} cobranças efetuadas em {today}.", flush=True)
    return results


def _charge_one(rec: dict, today: date, ym: str) -> dict:
    """Cobra UM gasto fixo. Retorna dict com info da cobrança."""
    user_id = int(rec["user_id"])
    rec_id = int(rec["id"])
    amount = float(rec["amount"])
    name = rec["name"]
    category = rec["category"] or "outros"
    payment_type = rec["payment_type"]

    nota = f"Cobrança automática · {name}"

    launch_id = None
    credit_tx_id = None

    if payment_type == "account":
        from db.accounts import add_launch_and_update_balance
        launch_id, _seq, _bal = add_launch_and_update_balance(
            user_id, "despesa", amount, alvo=f"recorrente:{name}", nota=nota,
            categoria=category, is_internal_movement=False,
        )
    else:  # credit_card
        card_id = rec.get("card_id")
        if not card_id:
            raise ValueError(f"recorrente {rec_id}: payment_type=credit_card sem card_id")
        credit_tx_id = _charge_on_credit_card(user_id, int(card_id), amount, category, nota, today)

    # Insere recurring_charges + marca last_charged_ym (UNIQUE(recurring_id, ym))
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into recurring_charges (recurring_id, user_id, launch_id, credit_tx_id, amount, ym)
                values (%s, %s, %s, %s, %s, %s)
                on conflict (recurring_id, ym) do nothing
                returning id
                """,
                (rec_id, user_id, launch_id, credit_tx_id, Decimal(str(amount)), ym),
            )
            row = cur.fetchone()
            charge_id = row["id"] if row else None
            cur.execute(
                "update recurring_expenses set last_charged_ym=%s where id=%s and user_id=%s",
                (ym, rec_id, user_id),
            )
        conn.commit()

    return {
        "recurring_id": rec_id, "user_id": user_id, "name": name,
        "amount": amount, "payment_type": payment_type,
        "launch_id": launch_id, "credit_tx_id": credit_tx_id,
        "charge_id": charge_id, "ym": ym,
    }


def _charge_on_credit_card(
    user_id: int, card_id: int, amount: float, category: str, nota: str, today: date
) -> int:
    """Cria credit_transaction na bill open atual do cartão.
    Se não existir bill open, cria uma. Atualiza bill.total."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Tenta achar bill open que cobre `today`
            cur.execute(
                """
                select id, total from credit_bills
                where user_id=%s and card_id=%s and status='open'
                  and period_start <= %s and period_end >= %s
                limit 1
                """,
                (user_id, card_id, today, today),
            )
            bill = cur.fetchone()
            if not bill:
                # Procura qualquer bill open desse cartão (mais permissivo)
                cur.execute(
                    """
                    select id, total from credit_bills
                    where user_id=%s and card_id=%s and status='open'
                    order by period_end asc limit 1
                    """,
                    (user_id, card_id),
                )
                bill = cur.fetchone()
            if not bill:
                raise ValueError(
                    f"Sem bill open pro cartão {card_id} — cron não consegue cobrar."
                )

            bill_id = bill["id"]
            cur.execute(
                """
                insert into credit_transactions (
                    bill_id, user_id, card_id, tipo, valor, categoria, nota,
                    purchased_at, is_refund, source
                ) values (%s, %s, %s, 'credito', %s, %s, %s, %s, false, 'recurring')
                returning id
                """,
                (bill_id, user_id, card_id, Decimal(str(amount)), category, nota, today),
            )
            tx_id = cur.fetchone()["id"]
            cur.execute(
                "update credit_bills set total = total + %s where id = %s",
                (Decimal(str(amount)), bill_id),
            )
        conn.commit()
    return tx_id


__all__ = [
    "run_recurring_charger_loop",
    "charge_due_recurring_expenses_once",
]
