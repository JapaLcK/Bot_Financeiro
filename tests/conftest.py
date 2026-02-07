import os
import sys
import uuid
import pytest

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from db import init_db, ensure_user, get_conn



@pytest.fixture(scope="session", autouse=True)
def _init_schema():
    if not os.getenv("DATABASE_URL"):
        raise RuntimeError("Faltou DATABASE_URL no ambiente para rodar os testes.")
    init_db()


def _cleanup_user(user_id: int):
    # limpa somente dados deste user_id (seguro)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("delete from pending_actions where user_id = %s", (user_id,))
            cur.execute("delete from launches where user_id = %s", (user_id,))
            cur.execute("delete from pockets where user_id = %s", (user_id,))
            cur.execute("delete from investments where user_id = %s", (user_id,))
            cur.execute("delete from accounts where user_id = %s", (user_id,))
            cur.execute("delete from users where id = %s", (user_id,))
        conn.commit()


@pytest.fixture()
def user_id():
    uid = int(uuid.uuid4().int % 10_000_000_000)  # bigint ok
    ensure_user(uid)
    yield uid
    _cleanup_user(uid)
