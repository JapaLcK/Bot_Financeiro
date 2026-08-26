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
from contextvars import ContextVar

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


_pool: ConnectionPool | None = None
_pool_lock = threading.Lock()

# Quantas vezes um `commit()` LEVANTOU nesta thread. Só isso: commit que volta
# sem erro não é ambíguo, e falha antes do commit não gravou nada.
_commits_ambiguos: ContextVar[int] = ContextVar("db_commits_ambiguos", default=0)


def commits_ambiguos() -> int:
    """Contador monotônico de commits que estouraram — leia antes e depois.

    Quando a conexão cai ENQUANTO o Postgres confirma o COMMIT, a chamada
    levanta mas a transação pode ter sido gravada assim mesmo. De fora, isso é
    indistinguível de uma falha ANTES do commit, e a diferença decide se dá para
    repetir o trabalho: repetir um aporte já commitado debita a origem e credita
    o destino DUAS vezes (Codex, PR #144).

    Quem precisa distinguir compara o valor de antes com o de depois — delta 0
    significa "nada chegou a ser confirmado", e só aí repetir é seguro. É o que
    `db.restore_pending_on_error` faz para decidir se devolve a pergunta.

    Contador, e não flag booleana, porque não há onde zerar: um erro engolido
    lá atrás deixaria a flag ligada para sempre.
    """
    return _commits_ambiguos.get()


class _Conn(psycopg.Connection):
    """Conexão que marca o commit ambíguo. Ver `commits_ambiguos`.

    Cobre também o commit implícito do `with get_conn() as conn:` — o
    `Connection.__exit__` do psycopg chama este mesmo `commit()`.
    """

    def commit(self) -> None:
        try:
            super().commit()
        except BaseException:
            _commits_ambiguos.set(_commits_ambiguos.get() + 1)
            raise


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
            connection_class=_Conn,
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
