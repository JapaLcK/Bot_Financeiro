"""
db/connection.py — Conexão com o banco de dados PostgreSQL.

Usa psycopg_pool.ConnectionPool (síncrono) pra reusar conns. Sem pool,
cada chamada abria conn nova (~1-2s no Railway), fazendo cada endpoint
síncrono custar 3-6s. Com pool, conn reaproveitada → cada query custa só
o round-trip da query em si.

A interface `with get_conn() as conn:` continua idêntica — callers
existentes não precisam mudar.
"""
import os
import threading
import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


_pool: ConnectionPool | None = None
_pool_lock = threading.Lock()


def _get_pool() -> ConnectionPool:
    global _pool
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is not None:
            return _pool
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            raise RuntimeError("DATABASE_URL não está definido.")
        _pool = ConnectionPool(
            database_url,
            min_size=1,
            max_size=int(os.getenv("DB_POOL_MAX_SYNC", "8")),
            timeout=float(os.getenv("DB_CONNECT_TIMEOUT", "30")),
            kwargs={"row_factory": dict_row},
            open=True,
        )
        return _pool


def get_conn():
    """Retorna um conn do pool. Compatível com `with get_conn() as conn:`."""
    return _get_pool().connection()
