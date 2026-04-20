"""
db/users.py — Gerenciamento de usuários, identidades e link de contas.
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt

from .connection import get_conn


# ──────────────────────────────────────────────────────────────────────────────
# Helpers internos de usuário
# ──────────────────────────────────────────────────────────────────────────────

def ensure_user_tx(cur, user_id: int):
    cur.execute("insert into users(id) values (%s) on conflict do nothing", (user_id,))
    cur.execute(
        "insert into accounts(user_id, balance) values (%s, 0) on conflict do nothing",
        (user_id,),
    )


def ensure_user(user_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            ensure_user_tx(cur, user_id)
        conn.commit()


# ──────────────────────────────────────────────────────────────────────────────
# Merge de usuários (vinculação Discord ↔ WhatsApp)
# ──────────────────────────────────────────────────────────────────────────────

def merge_users(from_user_id: int, to_user_id: int) -> None:
    """
    Move TODOS os dados de from_user_id → to_user_id.

    Antes de mover launches, remove duplicatas que colidem na unique
    uq_launches_user_source_external (user_id, source, external_id).
    """
    if from_user_id == to_user_id:
        return

    with get_conn() as conn:
        with conn.cursor() as cur:
            ensure_user_tx(cur, to_user_id)
            ensure_user_tx(cur, from_user_id)

            # 1) dedupe de launches
            cur.execute(
                """
                delete from launches lf
                using launches lt
                where lf.user_id = %s
                  and lt.user_id = %s
                  and lf.external_id is not null
                  and lt.external_id is not null
                  and lf.source = lt.source
                  and lf.external_id = lt.external_id
                """,
                (from_user_id, to_user_id),
            )

            # 2) move launches restantes
            cur.execute(
                "update launches set user_id=%s where user_id=%s",
                (to_user_id, from_user_id),
            )

            # 3) soma saldos
            cur.execute("select balance from accounts where user_id=%s", (to_user_id,))
            row_to = cur.fetchone()
            bal_to = float(row_to["balance"]) if row_to else 0.0

            cur.execute("select balance from accounts where user_id=%s", (from_user_id,))
            row_from = cur.fetchone()
            bal_from = float(row_from["balance"]) if row_from else 0.0

            new_bal = bal_to + bal_from
            cur.execute(
                "update accounts set balance=%s where user_id=%s",
                (new_bal, to_user_id),
            )
            cur.execute("delete from accounts where user_id=%s", (from_user_id,))

            # 4) identidades / link_codes
            cur.execute(
                "update user_identities set user_id=%s where user_id=%s",
                (to_user_id, from_user_id),
            )
            cur.execute(
                "update link_codes set user_id=%s where user_id=%s",
                (to_user_id, from_user_id),
            )

            # 5) outras tabelas com user_id
            for table in ("user_category_rules", "pending_actions", "pockets", "investments",
                          "credit_transactions", "ofx_imports"):
                cur.execute(
                    f"update {table} set user_id=%s where user_id=%s",
                    (to_user_id, from_user_id),
                )

            # 6) credit_cards: merge seguro por nome
            cur.execute("select id, name from credit_cards where user_id=%s", (from_user_id,))
            from_cards = cur.fetchall()

            for from_card in from_cards:
                from_card_id = from_card["id"]
                from_card_name = from_card["name"]

                cur.execute(
                    "select id from credit_cards where user_id=%s and name=%s",
                    (to_user_id, from_card_name),
                )
                to_card_row = cur.fetchone()

                if to_card_row:
                    to_card_id = to_card_row["id"]
                    cur.execute(
                        """
                        delete from credit_bills fb
                        using credit_bills tb
                        where fb.card_id = %s and tb.card_id = %s
                          and fb.period_start = tb.period_start and fb.period_end = tb.period_end
                        """,
                        (from_card_id, to_card_id),
                    )
                    cur.execute(
                        "update credit_bills set card_id=%s where card_id=%s",
                        (to_card_id, from_card_id),
                    )
                    cur.execute(
                        "update credit_transactions set card_id=%s where card_id=%s",
                        (to_card_id, from_card_id),
                    )
                    cur.execute("delete from credit_cards where id=%s", (from_card_id,))
                else:
                    cur.execute(
                        "update credit_cards set user_id=%s where id=%s",
                        (to_user_id, from_card_id),
                    )

            # 7) credit_bills.user_id
            cur.execute(
                "update credit_bills set user_id=%s where user_id=%s",
                (to_user_id, from_user_id),
            )

            # 8) auth_accounts: migra se to_user não tem
            cur.execute("select id from auth_accounts where user_id=%s limit 1", (to_user_id,))
            to_has_auth = cur.fetchone() is not None

            if not to_has_auth:
                cur.execute(
                    "update auth_accounts set user_id=%s where user_id=%s",
                    (to_user_id, from_user_id),
                )

        conn.commit()


def user_score(user_id: int) -> int:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) as n from launches where user_id=%s", (user_id,))
        return int(cur.fetchone()["n"])


def choose_primary_user(a_user_id: int, b_user_id: int) -> tuple[int, int]:
    """Retorna (primary, secondary) baseado em quem tem mais dados."""
    if a_user_id == b_user_id:
        return a_user_id, b_user_id
    sa = user_score(a_user_id)
    sb = user_score(b_user_id)
    return (a_user_id, b_user_id) if sa >= sb else (b_user_id, a_user_id)


# ──────────────────────────────────────────────────────────────────────────────
# Usuário canônico (identidade entre plataformas)
# ──────────────────────────────────────────────────────────────────────────────

def get_or_create_canonical_user(provider: str, external_id: str) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select user_id from user_identities where provider=%s and external_id=%s",
                (provider, external_id),
            )
            row = cur.fetchone()
            if row:
                return int(row["user_id"])

            base = f"{provider}:{external_id}".encode("utf-8")
            for i in range(20):
                digest = hashlib.sha256(base + f":{i}".encode("utf-8")).digest()
                new_id = int.from_bytes(digest[:8], "big") % 2_000_000_000 + 1

                ensure_user_tx(cur, new_id)

                try:
                    cur.execute(
                        "insert into user_identities(provider, external_id, user_id) values (%s,%s,%s)",
                        (provider, external_id, new_id),
                    )
                    conn.commit()
                    return new_id
                except Exception:
                    conn.rollback()
                    with get_conn() as conn2:
                        with conn2.cursor() as cur2:
                            cur2.execute(
                                "select user_id from user_identities where provider=%s and external_id=%s",
                                (provider, external_id),
                            )
                            r2 = cur2.fetchone()
                            if r2:
                                return int(r2["user_id"])
                    continue

            raise RuntimeError("Falha ao criar user_id canônico (colisão repetida)")


# ──────────────────────────────────────────────────────────────────────────────
# Link codes (vinculação entre plataformas)
# ──────────────────────────────────────────────────────────────────────────────

def create_link_code(user_id: int, minutes_valid: int = 10) -> str:
    code = f"{secrets.randbelow(1_000_000):06d}"
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=minutes_valid)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into link_codes(code, user_id, expires_at) values (%s,%s,%s) "
                "on conflict (code) do update set user_id=excluded.user_id, expires_at=excluded.expires_at",
                (code, user_id, expires_at),
            )
        conn.commit()
    return code


def create_platform_onboarding_token(user_id: int, provider: str, minutes_valid: int = 15) -> str:
    token = f"pbw_{secrets.token_urlsafe(18)}"
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=minutes_valid)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into platform_onboarding_tokens(token, provider, user_id, expires_at)
                values (%s, %s, %s, %s)
                on conflict (token) do update
                set provider=excluded.provider, user_id=excluded.user_id,
                    expires_at=excluded.expires_at, consumed_at=null
                """,
                (token, provider, user_id, expires_at),
            )
        conn.commit()
    return token


def consume_platform_onboarding_token(token: str, provider: str) -> int | None:
    now = datetime.now(timezone.utc)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                delete from platform_onboarding_tokens
                where token = %s and provider = %s and expires_at > %s and consumed_at is null
                returning user_id
                """,
                (token, provider, now),
            )
            row = cur.fetchone()
        conn.commit()
    return int(row["user_id"]) if row else None


def consume_link_code(code: str) -> int | None:
    now = datetime.now(timezone.utc)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select user_id, expires_at from link_codes where code=%s", (code,))
            row = cur.fetchone()
            if not row:
                return None
            if row["expires_at"] < now:
                cur.execute("delete from link_codes where code=%s", (code,))
                return None
            user_id = int(row["user_id"])
            cur.execute("delete from link_codes where code=%s", (code,))
            return user_id


def bind_identity(provider: str, external_id: str, user_id: int) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into user_identities(provider, external_id, user_id) values (%s,%s,%s) "
                "on conflict (provider, external_id) do update set user_id=excluded.user_id",
                (provider, external_id, user_id),
            )
        conn.commit()


def link_platform_identity(provider: str, external_id: str, target_user_id: int) -> int:
    """
    Liga (provider, external_id) ao target_user_id.
    O target_user_id é SEMPRE o primário.
    """
    current_user_id = get_or_create_canonical_user(provider, external_id)
    if current_user_id == target_user_id:
        return target_user_id

    merge_users(current_user_id, target_user_id)
    bind_identity(provider, external_id, target_user_id)
    return target_user_id


# ──────────────────────────────────────────────────────────────────────────────
# Senha (helpers internos — usados por reports.py)
# ──────────────────────────────────────────────────────────────────────────────

def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _check_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False
