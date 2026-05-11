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
                "select id, name, balance, description from pockets where user_id=%s order by lower(name)",
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


def create_pocket(user_id: int, name: str, nota: str | None = None, description: str | None = None):
    """
    Cria caixinha. Retorna (launch_id, pocket_id, pocket_name).
    Se já existir, retorna (None, pocket_id, pocket_name).

    `nota` vira o texto do lançamento (audit log).
    `description` é a descrição visível da caixinha (mostrada no dashboard).
    """
    ensure_user(user_id)
    name = (name or "").strip()
    if not name:
        raise ValueError("EMPTY_NAME")

    # Plan gate: blinda todos os canais (HTTP, bot, IA). Levanta
    # PlanLimitExceeded com mensagem amigável; callers decidem se mostram em
    # texto (bot) ou em 403 (HTTP).
    from core.services.plan_service import check_can_create_pocket
    check_can_create_pocket(user_id)

    desc = (description or "").strip() or None
    criado_em = datetime.now(_tz())

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into pockets(user_id, name, balance, description) values (%s, %s, 0, %s) "
                "on conflict (user_id, name) do nothing returning id, name",
                (user_id, name, desc),
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
    """Exclui caixinha (so se saldo=0) e remove o historico relacionado.

    Apaga todos os launches que referenciam esta caixinha por nome
    (deposito_caixinha, saque_caixinha, criar_caixinha, delete_pocket
    de eventuais re-criacoes anteriores). Sem isso, recriar uma caixinha
    com o mesmo nome ressuscita o historico antigo (a query de historico
    filtra por `alvo`, nao por pocket_id).

    Saldo zero garante que os deposito/saque se cancelam — apagar nao
    afeta o saldo da conta principal. Retorna (None, canon_name) — o
    `launch_id` antigo de auditoria foi removido junto com o resto.
    """
    ensure_user(user_id)
    pocket_name = (pocket_name or "").strip()
    if not pocket_name:
        raise ValueError("EMPTY_NAME")

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

            cur.execute(
                """
                delete from launches
                 where user_id = %s
                   and lower(alvo) = lower(%s)
                   and tipo in ('deposito_caixinha', 'saque_caixinha',
                                'criar_caixinha', 'delete_pocket')
                """,
                (user_id, canon),
            )
            cur.execute("delete from pockets where id=%s", (pocket_id,))

        conn.commit()

    return None, canon
