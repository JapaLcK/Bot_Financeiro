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
            reset=_reset_conn,
            open=True,
        )
        return _pool


def _reset_conn(conn) -> None:
    """Devolve a conexão ao pool no estado padrão (transacional).

    O psycopg_pool faz rollback sozinho ao devolver, mas NÃO desfaz um
    `conn.autocommit = True` — e uma conexão em autocommit é uma conexão sem
    rollback: cada statement já está commitado, então um erro no meio de uma
    operação deixa metade da escrita gravada. O init_db precisa de autocommit
    para a DDL (locks) e pegava a conexão do pool; sem esta rede, ela voltava
    envenenada e contaminava transações de dinheiro para sempre. Medido: 6 de 6
    `create_investment_db` que estouraram INSUFFICIENT_ACCOUNT depois de um
    init_db deixaram o investimento criado.
    """
    if conn.autocommit:
        conn.autocommit = False


def get_conn():
    """Retorna um conn do pool. Compatível com `with get_conn() as conn:`."""
    return _get_pool().connection()


def close_pool() -> None:
    """Fecha o pool e zera o singleton — a próxima chamada reabre lendo
    DATABASE_URL de novo.

    Existe para a suíte: o conftest cria um database por execução e precisa
    soltar as conexões antes de derrubá-lo no fim. Em produção o pool vive
    enquanto o processo viver, então isto não é chamado.
    """
    global _pool
    with _pool_lock:
        if _pool is None:
            return
        try:
            _pool.close()
        finally:
            _pool = None


# ── comparação de categoria case- E acento-insensível ────────────────────────
# Dados legados guardam a mesma categoria com/sem acento ("alimentacao" vs
# "alimentação"). Pra somar/filtrar por categoria sem perder linhas, comparamos
# ambos os lados normalizados (lower + remove acento) via translate — portável,
# não depende da extensão unaccent.
_CAT_ACCENTS_FROM = "áàâãäéèêëíìîïóòôõöúùûüç"
_CAT_ACCENTS_TO   = "aaaaaeeeeiiiiooooouuuuc"


def cat_norm_sql(expr: str) -> str:
    """Fragmento SQL que normaliza `expr` (coluna ou placeholder %s) pra
    comparação de categoria case- e acento-insensível."""
    return f"translate(lower({expr}), '{_CAT_ACCENTS_FROM}', '{_CAT_ACCENTS_TO}')"
