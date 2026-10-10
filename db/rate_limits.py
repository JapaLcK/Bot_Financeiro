"""Teto persistente de tentativas em `auth_rate_limits`: janela fixa por (bucket, identifier)."""
from .connection import get_conn

# Fonte única do UPSERT da janela (§0.7): o `_check_persistent_rate_limit` async
# do monólito executa este mesmo SQL. Conta a tentativa e devolve o total da
# janela; janela vencida recomeça em 1.
# Parâmetros: (bucket, identifier, window_seconds, window_seconds).
RATE_LIMIT_UPSERT_SQL = """
    INSERT INTO auth_rate_limits (bucket, identifier, window_started_at, attempts, updated_at)
    VALUES (%s, %s, NOW(), 1, NOW())
    ON CONFLICT (bucket, identifier) DO UPDATE SET
        window_started_at = CASE
            WHEN auth_rate_limits.window_started_at <= NOW() - (%s * INTERVAL '1 second')
            THEN NOW()
            ELSE auth_rate_limits.window_started_at
        END,
        attempts = CASE
            WHEN auth_rate_limits.window_started_at <= NOW() - (%s * INTERVAL '1 second')
            THEN 1
            ELSE auth_rate_limits.attempts + 1
        END,
        updated_at = NOW()
    RETURNING
        attempts,
        EXTRACT(EPOCH FROM (NOW() - window_started_at)) AS elapsed_seconds
"""


def rate_limit_estourado(bucket: str, identifier: str, max_attempts: int, window_seconds: int) -> bool:
    """Conta esta tentativa e diz se ela passou do teto da janela."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(RATE_LIMIT_UPSERT_SQL, (bucket, identifier, window_seconds, window_seconds))
            row = cur.fetchone()
        conn.commit()
    return int(row["attempts"] or 0) > max_attempts
