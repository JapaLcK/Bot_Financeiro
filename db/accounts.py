"""
db/accounts.py — Saldo, lançamentos e importação OFX.
"""
import json
from datetime import datetime, date, timedelta
from decimal import Decimal

from psycopg.types.json import Json, Jsonb

import db_support as _db_support
from utils_date import _tz

from .connection import get_conn
from .users import ensure_user, ensure_user_tx


# ──────────────────────────────────────────────────────────────────────────────
# Saldo
# ──────────────────────────────────────────────────────────────────────────────

def get_balance(user_id: int) -> Decimal:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select balance from accounts where user_id=%s", (user_id,))
            row = cur.fetchone()
            return row["balance"] if row else Decimal("0")


def set_balance(user_id: int, new_balance: Decimal) -> Decimal:
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE accounts SET balance=%s WHERE user_id=%s RETURNING balance",
                (new_balance, user_id),
            )
            bal = cur.fetchone()["balance"]
        conn.commit()
    return bal


# ──────────────────────────────────────────────────────────────────────────────
# Lançamentos
# ──────────────────────────────────────────────────────────────────────────────

def add_launch_and_update_balance(
    user_id: int,
    tipo: str,
    valor: float,
    alvo: str | None,
    nota: str | None,
    categoria: str | None = None,
    criado_em: datetime | None = None,
    is_internal_movement: bool = False,
):
    """
    Lança em launches e atualiza saldo em accounts na mesma transação.
    Regra: despesa → saldo -= valor; receita → saldo += valor.
    """
    ensure_user(user_id)

    v = Decimal(str(valor))
    if tipo == "despesa":
        delta = -v
    elif tipo == "receita":
        delta = +v
    else:
        raise ValueError(f"tipo inválido: {tipo}")

    if criado_em is None:
        criado_em = datetime.now(_tz())

    cat = (categoria or "").strip() or "outros"

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update accounts set balance = balance + %s where user_id=%s returning balance",
                (delta, user_id),
            )
            new_bal = cur.fetchone()["balance"]

            cur.execute(
                """
                insert into launches(user_id, tipo, valor, alvo, nota, categoria, criado_em, efeitos, is_internal_movement)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                returning id
                """,
                (user_id, tipo, v, alvo, nota, cat, criado_em,
                 Json({"delta_conta": float(delta)}), is_internal_movement),
            )
            launch_id = cur.fetchone()["id"]

        conn.commit()

    return launch_id, new_bal


def list_launches(user_id: int, limit: int = 10):
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, tipo, valor, alvo, nota, categoria, source, criado_em
                from launches
                where user_id=%s
                order by criado_em desc, id desc
                limit %s
                """,
                (user_id, limit),
            )
            return cur.fetchall()


def update_launch_category(user_id: int, launch_id: int, categoria: str | None) -> bool:
    ensure_user(user_id)
    cat = (categoria or "").strip() or None
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update launches set categoria=%s where user_id=%s and id=%s",
                (cat, user_id, launch_id),
            )
            changed = (cur.rowcount or 0) == 1
        conn.commit()
    return changed


def update_launch_categories_bulk(user_id: int, items: list[tuple[int, str]]) -> int:
    ensure_user(user_id)
    if not items:
        return 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                "update launches set categoria=%s where user_id=%s and id=%s",
                [(cat, user_id, lid) for (lid, cat) in items],
            )
            n = cur.rowcount or 0
        conn.commit()
    return n


def export_launches(user_id: int, start_date: date | None = None, end_date: date | None = None):
    ensure_user(user_id)

    params = [user_id]
    where = ["user_id=%s"]

    if start_date:
        where.append("criado_em >= %s")
        params.append(datetime.combine(start_date, datetime.min.time()))
    if end_date:
        where.append("criado_em < %s")
        params.append(datetime.combine(end_date + timedelta(days=1), datetime.min.time()))

    sql = f"""
        select id, tipo, valor, alvo, nota, criado_em, efeitos
        from launches
        where {' and '.join(where)}
        order by criado_em asc, id asc
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            return cur.fetchall()


def get_launches_by_period(user_id: int, start_date: date, end_date: date):
    return _db_support.get_launches_by_period_impl(get_conn, ensure_user, user_id, start_date, end_date)


def get_summary_by_period(user_id: int, start_date: date, end_date: date):
    return _db_support.get_summary_by_period_impl(get_conn, ensure_user, user_id, start_date, end_date)


# ──────────────────────────────────────────────────────────────────────────────
# Desfazer lançamento
# ──────────────────────────────────────────────────────────────────────────────

def delete_launch_and_rollback(user_id: int, launch_id: int):
    """
    Deleta um lançamento e reverte seus efeitos no banco atomicamente.
    Usa o campo efeitos (jsonb) para saber o que reverter.
    """
    ensure_user(user_id)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id, tipo, valor, alvo, efeitos from launches where id=%s and user_id=%s",
                (launch_id, user_id),
            )
            row = cur.fetchone()
            if not row:
                raise LookupError("NOT_FOUND")

            efeitos = row.get("efeitos")
            if efeitos is None:
                raise ValueError("lançamento sem 'efeitos' (não dá pra desfazer com segurança).")

            if isinstance(efeitos, str):
                efeitos = json.loads(efeitos)

            delta_conta = Decimal(str(efeitos.get("delta_conta", 0)))
            delta_pocket = efeitos.get("delta_pocket")
            delta_invest = efeitos.get("delta_invest")
            create_pocket = efeitos.get("create_pocket")
            create_invest = efeitos.get("create_investment")
            delete_pocket = efeitos.get("delete_pocket")
            delete_investment = efeitos.get("delete_investment")

            # desfazer criação de investimento (zera e deleta)
            if create_invest:
                nome = create_invest.get("nome")
                if nome:
                    cur.execute(
                        "delete from investments where user_id=%s and lower(name)=lower(%s) and balance=0",
                        (user_id, nome),
                    )

            # desfazer deleção de investimento (recria)
            if delete_investment:
                nome = delete_investment.get("nome")
                bal0 = Decimal(str(delete_investment.get("balance", 0)))
                rate = Decimal(str(delete_investment.get("rate", 0)))
                period = delete_investment.get("period", "monthly")
                last_date_str = delete_investment.get("last_date")
                asset_type = delete_investment.get("asset_type") or "CDB"
                indexer = delete_investment.get("indexer")
                issuer = delete_investment.get("issuer")
                purchase_date = delete_investment.get("purchase_date")
                maturity_date = delete_investment.get("maturity_date")
                interest_payment_frequency = delete_investment.get("interest_payment_frequency") or "maturity"
                tax_profile = delete_investment.get("tax_profile") or "regressive_ir_iof"
                if nome:
                    from datetime import date as _date
                    ld = _date.fromisoformat(last_date_str) if last_date_str else datetime.now(_tz()).date()
                    cur.execute(
                        """
                        insert into investments(
                            user_id, name, balance, rate, period, last_date,
                            asset_type, indexer, issuer, purchase_date, maturity_date,
                            interest_payment_frequency, tax_profile
                        )
                        values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        on conflict (user_id, name) do nothing
                        """,
                        (
                            user_id, nome, bal0, rate, period, ld,
                            asset_type, indexer, issuer, purchase_date, maturity_date,
                            interest_payment_frequency, tax_profile,
                        ),
                    )

            # desfazer deleção de caixinha (recria)
            if delete_pocket:
                nome = delete_pocket.get("nome")
                bal0 = Decimal(str(delete_pocket.get("balance", 0)))
                if nome:
                    cur.execute(
                        "insert into pockets(user_id, name, balance) values (%s,%s,%s) "
                        "on conflict (user_id, name) do nothing",
                        (user_id, nome, bal0),
                    )

            # reverte conta
            if delta_conta != 0:
                cur.execute(
                    "update accounts set balance = balance - %s where user_id=%s",
                    (delta_conta, user_id),
                )

            # reverte caixinha
            if delta_pocket:
                nome = delta_pocket.get("nome")
                dp = Decimal(str(delta_pocket.get("delta", 0)))
                if not nome:
                    raise ValueError("delta_pocket inválido (sem nome).")
                cur.execute(
                    "update pockets set balance = balance - %s where user_id=%s and lower(name)=lower(%s)",
                    (dp, user_id, nome),
                )

            # reverte investimento
            if delta_invest:
                nome = delta_invest.get("nome")
                di = Decimal(str(delta_invest.get("delta", 0)))
                if not nome:
                    raise ValueError("delta_invest inválido (sem nome).")
                cur.execute(
                    "update investments set balance = balance - %s where user_id=%s and lower(name)=lower(%s)",
                    (di, user_id, nome),
                )

            # desfazer criação de caixinha (deleta)
            if create_pocket:
                nome = create_pocket.get("nome")
                if nome:
                    cur.execute(
                        "delete from pockets where user_id=%s and lower(name)=lower(%s)",
                        (user_id, nome),
                    )

            # apaga o lançamento
            cur.execute("delete from launches where id=%s and user_id=%s", (launch_id, user_id))

        conn.commit()


# ──────────────────────────────────────────────────────────────────────────────
# OFX import (idempotente)
# ──────────────────────────────────────────────────────────────────────────────

def get_ofx_import_by_hash(user_id: int, file_hash: str):
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select file_hash, dt_start, dt_end, total_transactions,
                       inserted_count, duplicate_count, imported_at
                from ofx_imports
                where user_id=%s and file_hash=%s
                """,
                (user_id, file_hash),
            )
            return cur.fetchone()


def import_ofx_launches_bulk(
    user_id: int,
    launches_rows: list[dict],
    *,
    file_hash: str,
    bank_id: str | None,
    acct_id: str | None,
    acct_type: str | None,
    dt_start: date | None,
    dt_end: date | None,
):
    """
    Importa transações OFX de forma IDEMPOTENTE (ON CONFLICT DO NOTHING).
    Saldo só é ajustado pelas transações efetivamente inseridas.
    """
    ensure_user(user_id)
    total = len(launches_rows)

    prev = get_ofx_import_by_hash(user_id, file_hash)
    if prev:
        bal = get_balance(user_id)
        return {
            "skipped_same_file": True,
            "total": prev["total_transactions"],
            "inserted": prev["inserted_count"],
            "duplicates": prev["duplicate_count"],
            "dt_start": prev["dt_start"],
            "dt_end": prev["dt_end"],
            "new_balance": bal,
            "imported_at": prev["imported_at"],
        }

    inserted = 0
    duplicates = 0
    delta_total = Decimal("0")

    with get_conn() as conn:
        with conn.cursor() as cur:
            for r in launches_rows:
                cur.execute(
                    """
                    insert into launches(
                        user_id, tipo, valor, categoria, alvo, nota, criado_em, efeitos,
                        source, external_id, posted_at, currency, imported_at, is_internal_movement
                    )
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now(),%s)
                    on conflict (user_id, source, external_id) do nothing
                    """,
                    (
                        user_id, r["tipo"], r["valor"], r.get("categoria"), r.get("alvo"), r.get("nota"),
                        r["criado_em"],
                        Json({"delta_conta": float(r["delta"]), "ofx": r.get("ofx_meta", {})}),
                        "ofx", r["external_id"], r.get("posted_at"), r.get("currency", "BRL"),
                        r.get("is_internal_movement", False),
                    ),
                )
                if (cur.rowcount or 0) == 1:
                    inserted += 1
                    delta_total += r["delta"]
                else:
                    duplicates += 1

            if inserted:
                cur.execute(
                    "update accounts set balance = balance + %s where user_id=%s returning balance",
                    (delta_total, user_id),
                )
                new_bal = cur.fetchone()["balance"]
            else:
                cur.execute("select balance from accounts where user_id=%s", (user_id,))
                new_bal = cur.fetchone()["balance"]

            cur.execute(
                """
                insert into ofx_imports(
                    user_id, file_hash, bank_id, acct_id, acct_type,
                    dt_start, dt_end, total_transactions, inserted_count, duplicate_count
                )
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) on conflict do nothing
                """,
                (user_id, file_hash, bank_id, acct_id, acct_type,
                 dt_start, dt_end, total, inserted, duplicates),
            )

        conn.commit()

    return {
        "skipped_same_file": False,
        "total": total,
        "inserted": inserted,
        "duplicates": duplicates,
        "dt_start": dt_start,
        "dt_end": dt_end,
        "new_balance": new_bal,
    }
