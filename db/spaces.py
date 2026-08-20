"""
db/spaces.py — Espaços financeiros (Fase 1).

Um espaço é uma área da vida financeira do usuário (Pessoal, Casa, Fazenda…).
Cada usuário tem exatamente um espaço `is_default`, criado sob demanda por
`ensure_default_space`. Em `launches`/`credit_transactions`, `space_id NULL`
significa "espaço default" — por isso não há backfill de dados existentes.

Regra dura do projeto: toda query com `where user_id = %s`. Nunca vazar entre
usuários — inclusive ao tocar `open_finance_accounts` (que não tem `user_id`
direto; validar sempre contra o dono via `open_finance_connections`).
"""
from .connection import get_conn
from .users import ensure_user

DEFAULT_SPACE_NAME = "Pessoal"
DEFAULT_SPACE_EMOJI = "🏦"
DEFAULT_SPACE_COLOR = "#7c3aed"

SPACE_COLUMNS = "id, name, emoji, color, is_default, is_archived, created_at"


def ensure_default_space(user_id: int) -> int:
    """Retorna o id do espaço default do usuário, criando-o se necessário.

    Idempotente e seguro sob concorrência: o índice parcial
    `uq_financial_spaces_one_default` garante 1 default por usuário, e o
    `on conflict (user_id, name)` resolve corrida de criação para a mesma linha.
    """
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id from financial_spaces where user_id=%s and is_default",
                (user_id,),
            )
            row = cur.fetchone()
            if row:
                return int(row["id"])

            cur.execute(
                """
                insert into financial_spaces(user_id, name, emoji, color, is_default)
                values (%s, %s, %s, %s, true)
                on conflict (user_id, name)
                  do update set is_default = true, is_archived = false
                returning id
                """,
                (user_id, DEFAULT_SPACE_NAME, DEFAULT_SPACE_EMOJI, DEFAULT_SPACE_COLOR),
            )
            conn.commit()
            return int(cur.fetchone()["id"])


def get_default_space_id(user_id: int) -> int:
    """Alias explícito de `ensure_default_space` para leitura."""
    return ensure_default_space(user_id)


def list_spaces(user_id: int, *, include_archived: bool = False) -> list[dict]:
    """Lista os espaços do usuário (default primeiro, depois por nome)."""
    clause = "" if include_archived else " and is_archived = false"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                select {SPACE_COLUMNS}
                from financial_spaces
                where user_id = %s{clause}
                order by is_default desc, lower(name)
                """,
                (user_id,),
            )
            return [dict(r) for r in cur.fetchall()]


def create_space(
    user_id: int,
    name: str,
    *,
    emoji: str | None = None,
    color: str | None = None,
) -> dict:
    """Cria (ou reativa, se arquivado) um espaço não-default. Idempotente por nome."""
    ensure_user(user_id)
    clean = (name or "").strip()
    if not clean:
        raise ValueError("nome do espaço não pode ser vazio")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                insert into financial_spaces(user_id, name, emoji, color)
                values (%s, %s, coalesce(%s, %s), coalesce(%s, %s))
                on conflict (user_id, name)
                  do update set is_archived = false
                returning {SPACE_COLUMNS}
                """,
                (
                    user_id,
                    clean,
                    emoji,
                    DEFAULT_SPACE_EMOJI,
                    color,
                    DEFAULT_SPACE_COLOR,
                ),
            )
            conn.commit()
            return dict(cur.fetchone())
