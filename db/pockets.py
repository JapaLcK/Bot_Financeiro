"""
db/pockets.py — Caixinhas (pockets): criar, depositar, sacar e excluir.
"""
from datetime import datetime
from decimal import Decimal

from psycopg.types.json import Jsonb

from utils_date import _tz

from .connection import get_conn
from .users import ensure_user


def list_pockets(user_id: int):
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id, name, balance from pockets where user_id=%s order by lower(name)",
                (user_id,),
            )
            return cur.fetchall()


def pocket_withdraw_to_account(
    user_id: int, pocket_name: str, amount: float, nota: str | None = None
):
    """Caixinha → Conta. Retorna (launch_id, new_account_balance, new_pocket_balance, canon_name)."""
    ensure_user(user_id)
    v = Decimal(str(amount))
    if v <= 0:
        raise ValueError("AMOUNT_INVALID")

    criado_em = datetime.now(_tz())

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id, name, balance from pockets "
                "where user_id=%s and lower(name)=lower(%s) for update",
                (user_id, pocket_name),
            )
            p = cur.fetchone()
            if not p:
                raise LookupError("POCKET_NOT_FOUND")

            pocket_id = p["id"]
            canon = p["name"]
            if Decimal(str(p["balance"])) < v:
                raise ValueError("INSUFFICIENT_POCKET")

            cur.execute(
                "update pockets set balance = balance - %s where id=%s returning balance",
                (v, pocket_id),
            )
            new_pocket = cur.fetchone()["balance"]

            cur.execute("select balance from accounts where user_id=%s for update", (user_id,))
            cur.execute(
                "update accounts set balance = balance + %s where user_id=%s returning balance",
                (v, user_id),
            )
            new_acc = cur.fetchone()["balance"]

            efeitos = {
                "delta_conta": float(+v),
                "delta_pocket": {"nome": canon, "delta": float(-v)},
                "delta_invest": None, "create_pocket": None, "create_investment": None,
            }
            cur.execute(
                "insert into launches(user_id, tipo, valor, alvo, nota, criado_em, efeitos) "
                "values (%s,%s,%s,%s,%s,%s,%s) returning id",
                (user_id, "saque_caixinha", v, canon, nota, criado_em, Jsonb(efeitos)),
            )
            launch_id = cur.fetchone()["id"]

        conn.commit()

    return launch_id, new_acc, new_pocket, canon


def create_pocket(user_id: int, name: str, nota: str | None = None):
    """
    Cria caixinha. Retorna (launch_id, pocket_id, pocket_name).
    Se já existir, retorna (None, pocket_id, pocket_name).
    """
    ensure_user(user_id)
    name = (name or "").strip()
    if not name:
        raise ValueError("EMPTY_NAME")

    criado_em = datetime.now(_tz())

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into pockets(user_id, name, balance) values (%s, %s, 0) "
                "on conflict (user_id, name) do nothing returning id, name",
                (user_id, name),
            )
            row = cur.fetchone()

            if row:
                pocket_id = row["id"]
                pocket_name = row["name"]
            else:
                cur.execute(
                    "select id, name from pockets where user_id=%s and lower(name)=lower(%s)",
                    (user_id, name),
                )
                r = cur.fetchone()
                if not r:
                    raise RuntimeError("POCKET_LOOKUP_FAILED")
                pocket_id, pocket_name = r["id"], r["name"]
                conn.commit()
                return None, pocket_id, pocket_name

            efeitos = {
                "delta_conta": 0.0, "delta_pocket": None, "delta_invest": None,
                "create_pocket": {"nome": pocket_name}, "create_investment": None,
            }
            cur.execute(
                "insert into launches(user_id, tipo, valor, alvo, nota, criado_em, efeitos) "
                "values (%s,%s,%s,%s,%s,%s,%s) returning id",
                (user_id, "criar_caixinha", Decimal("0"), pocket_name, nota, criado_em, Jsonb(efeitos)),
            )
            launch_id = cur.fetchone()["id"]

        conn.commit()

    return launch_id, pocket_id, pocket_name


def pocket_deposit_from_account(
    user_id: int, pocket_name: str, amount: float, nota: str | None = None
):
    """Conta → Caixinha. Retorna (launch_id, new_account_balance, new_pocket_balance, canon_name)."""
    ensure_user(user_id)
    v = Decimal(str(amount))
    if v <= 0:
        raise ValueError("AMOUNT_INVALID")

    criado_em = datetime.now(_tz())

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select balance from accounts where user_id=%s for update", (user_id,))
            acc = cur.fetchone()
            if not acc:
                raise RuntimeError("ACCOUNT_MISSING")
            if Decimal(str(acc["balance"])) < v:
                raise ValueError("INSUFFICIENT_ACCOUNT")

            cur.execute(
                "select id, name from pockets "
                "where user_id=%s and lower(name)=lower(%s) for update",
                (user_id, pocket_name),
            )
            p = cur.fetchone()
            if not p:
                raise LookupError("POCKET_NOT_FOUND")

            pocket_id, canon = p["id"], p["name"]

            cur.execute(
                "update accounts set balance = balance - %s where user_id=%s returning balance",
                (v, user_id),
            )
            new_acc = cur.fetchone()["balance"]

            cur.execute(
                "update pockets set balance = balance + %s where id=%s returning balance",
                (v, pocket_id),
            )
            new_pocket = cur.fetchone()["balance"]

            efeitos = {
                "delta_conta": float(-v),
                "delta_pocket": {"nome": canon, "delta": float(+v)},
                "delta_invest": None, "create_pocket": None, "create_investment": None,
            }
            cur.execute(
                "insert into launches(user_id, tipo, valor, alvo, nota, criado_em, efeitos) "
                "values (%s,%s,%s,%s,%s,%s,%s) returning id",
                (user_id, "deposito_caixinha", v, canon, nota, criado_em, Jsonb(efeitos)),
            )
            launch_id = cur.fetchone()["id"]

        conn.commit()

    return launch_id, new_acc, new_pocket, canon


def delete_pocket(user_id: int, pocket_name: str):
    """Exclui caixinha (só se saldo=0). Retorna (launch_id, canon_name)."""
    ensure_user(user_id)
    pocket_name = (pocket_name or "").strip()
    if not pocket_name:
        raise ValueError("EMPTY_NAME")

    criado_em = datetime.now(_tz())

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id, name, balance from pockets "
                "where user_id=%s and lower(name)=lower(%s) for update",
                (user_id, pocket_name),
            )
            p = cur.fetchone()
            if not p:
                raise LookupError("POCKET_NOT_FOUND")

            pocket_id, canon = p["id"], p["name"]
            if Decimal(str(p["balance"])) != Decimal("0"):
                raise ValueError("POCKET_NOT_ZERO")

            cur.execute("delete from pockets where id=%s", (pocket_id,))

            efeitos = {
                "delta_conta": 0.0, "delta_pocket": None, "delta_invest": None,
                "create_pocket": None, "create_investment": None,
                "delete_pocket": {"nome": canon, "balance": 0.0},
                "delete_investment": None,
            }
            cur.execute(
                "insert into launches(user_id, tipo, valor, alvo, nota, criado_em, efeitos) "
                "values (%s,%s,%s,%s,%s,%s,%s) returning id",
                (user_id, "delete_pocket", Decimal("0"), canon, None, criado_em, Jsonb(efeitos)),
            )
            launch_id = cur.fetchone()["id"]

        conn.commit()

    return launch_id, canon
