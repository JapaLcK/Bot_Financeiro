import os
import sys
import uuid
import pytest

sys.path.append(os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-for-pytest-only-32-bytes")

from db import init_db, ensure_user, get_conn



@pytest.fixture(scope="session", autouse=True)
def _init_schema():
    if not os.getenv("DATABASE_URL"):
        raise RuntimeError("Faltou DATABASE_URL no ambiente para rodar os testes.")
    init_db()


def _cleanup_user(user_id: int):
    """Apaga um user e todas as suas dependencias (NO ACTION + CASCADE)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Tabelas com on delete NO ACTION precisam ser apagadas a mao
            cur.execute("delete from credit_transactions where user_id = %s", (user_id,))
            cur.execute("delete from credit_bills where user_id = %s", (user_id,))
            cur.execute("delete from credit_cards where user_id = %s", (user_id,))
            cur.execute("delete from launches where user_id = %s", (user_id,))
            cur.execute("delete from pockets where user_id = %s", (user_id,))
            cur.execute("delete from investments where user_id = %s", (user_id,))
            cur.execute("delete from accounts where user_id = %s", (user_id,))
            # users.id cascateia o resto (auth_accounts, user_identities, etc.)
            cur.execute("delete from users where id = %s", (user_id,))
        conn.commit()


def _all_user_ids() -> set[int]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select id from users")
            return {row["id"] for row in cur.fetchall()}


@pytest.fixture(autouse=True)
def _auto_cleanup_orphan_users():
    """Salva quem ja existia em `users` antes do teste e apaga qualquer
    novo registro depois — pega ids secundarios criados manualmente
    dentro do teste (ex.: ensure_user(stale_wa_uid)) e os criados via
    confirm_email_verification, que nao usam o fixture `user_id`.

    Roda em todo teste (autouse) pra evitar leaks que quebram testes
    seguintes via FK accounts_user_id_fkey.
    """
    before = _all_user_ids()
    yield
    after = _all_user_ids()
    for orphan in (after - before):
        try:
            _cleanup_user(orphan)
        except Exception:
            # Se ainda assim falhar, silencia — o teste em si ja terminou.
            pass


@pytest.fixture()
def user_id():
    uid = int(uuid.uuid4().int % 10_000_000_000)  # bigint ok
    ensure_user(uid)
    yield uid
    _cleanup_user(uid)
