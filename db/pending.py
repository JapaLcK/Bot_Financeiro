"""
db/pending.py — Ações pendentes de confirmação (ex: "apagar lançamento?").
"""
from datetime import datetime, timedelta, timezone

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .connection import get_conn
from .users import ensure_user


def set_pending_action(user_id: int, action_type: str, payload: dict, minutes: int = 10):
    """Cria/atualiza uma ação pendente de confirmação (persistente no Postgres)."""
    ensure_user(user_id)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=minutes)

    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                insert into pending_actions (user_id, action_type, payload, expires_at)
                values (%s, %s, %s, %s)
                on conflict (user_id)
                do update set action_type = excluded.action_type,
                              payload = excluded.payload,
                              created_at = now(),
                              expires_at = excluded.expires_at
                """,
                (user_id, action_type, Jsonb(payload), expires_at),
            )
        conn.commit()


def get_pending_action(user_id: int):
    """Retorna a ação pendente se existir e não estiver expirada. Senão None."""
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "select user_id, action_type, payload, created_at, expires_at "
                "from pending_actions where user_id = %s",
                (user_id,),
            )
            row = cur.fetchone()
        conn.commit()

    if not row:
        return None

    if row["expires_at"] <= datetime.now(timezone.utc):
        clear_pending_action(user_id)
        return None

    return row


def clear_pending_action(user_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("delete from pending_actions where user_id = %s", (user_id,))
        conn.commit()
