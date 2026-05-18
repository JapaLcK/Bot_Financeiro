"""
core/refresh_tokens.py — Refresh tokens com rotação e detecção de roubo.

Modelo: access token JWT curto (15min) + refresh token opaco longo (14d), com
idle timeout (7d). Refresh token é rotacionado a cada uso — se um token já
usado for apresentado de novo, é sinal de roubo: revoga TUDO do user.

Tabela: auth_refresh_tokens (token_hash, user_id, session_jti, issued_at,
expires_at, used_at, revoked_at, ip, user_agent).

Plain token nunca é persistido — só `sha256(token)`. Caso o DB vaze, atacante
não consegue usar os refresh tokens sem o valor plain (que só viveu no cookie
do user).
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import sys
from datetime import datetime, timedelta, timezone

from db.connection import get_conn

logger = logging.getLogger(__name__)


# ─── Constantes ───────────────────────────────────────────────────────────────
ACCESS_TOKEN_TTL_MINUTES = 15           # JWT access — vai em toda request
REFRESH_TOKEN_TTL_DAYS = 14             # refresh absoluto
REFRESH_IDLE_TIMEOUT_DAYS = 7           # se sem uso por X dias, expira mesmo dentro do TTL absoluto
REFRESH_TOKEN_PREFIX = "rt_"            # prefixo legível (debug)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _hash(token: str) -> str:
    """SHA-256 hex do token plain. Determinístico (sem salt) pra permitir lookup."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _generate_token() -> str:
    """Gera um refresh token opaco urlsafe (~43 chars + prefixo)."""
    return f"{REFRESH_TOKEN_PREFIX}{secrets.token_urlsafe(32)}"


# ─── API pública ──────────────────────────────────────────────────────────────

def create_refresh_token(
    user_id: int,
    session_jti: str,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
) -> str:
    """Cria um refresh token novo. Retorna o plain token — só apresentado no
    cookie do user, nunca persistido em claro."""
    token = _generate_token()
    expires_at = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_TTL_DAYS)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            insert into auth_refresh_tokens
              (token_hash, user_id, session_jti, expires_at, ip, user_agent)
            values (%s, %s, %s, %s, %s, %s)
            """,
            (_hash(token), int(user_id), session_jti, expires_at, ip, user_agent),
        )
        conn.commit()
    return token


def consume_refresh_token(
    plain_token: str,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
) -> dict | None:
    """Tenta rotacionar o refresh: marca o atual como usado e emite um novo.

    Retorna dict com `user_id`, `session_jti`, `new_refresh_token` em sucesso.
    Retorna None em qualquer um destes casos:
      - token não encontrado / expirado / já revogado
      - **replay** (token já com used_at) → revoga TODO o user
      - **idle** (auth_sessions.last_seen_at < now - 7d) → revoga sessão

    Sempre falha silenciosa em exceções de DB — o caller trata None como
    "deslogar e mandar pro login".
    """
    if not plain_token or not plain_token.startswith(REFRESH_TOKEN_PREFIX):
        return None
    h = _hash(plain_token)
    now = datetime.now(timezone.utc)

    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                select user_id, session_jti, expires_at, used_at, revoked_at
                from auth_refresh_tokens
                where token_hash = %s
                for update
                """,
                (h,),
            )
            row = cur.fetchone()
            if not row:
                conn.commit()
                return None

            user_id = int(row["user_id"])
            session_jti = row["session_jti"]

            # Já revogado? só rejeita
            if row["revoked_at"] is not None:
                conn.commit()
                return None

            # Expirado (absoluto, 14d)?
            if row["expires_at"] < now:
                cur.execute(
                    "update auth_refresh_tokens set revoked_at = now() where token_hash = %s",
                    (h,),
                )
                conn.commit()
                return None

            # REPLAY: token já tinha sido usado → roubo detectado, revoga TUDO do user.
            if row["used_at"] is not None:
                logger.warning(
                    "[refresh] possível replay detectado user_id=%s — revogando todos os refresh tokens",
                    user_id,
                )
                cur.execute(
                    "update auth_refresh_tokens set revoked_at = now() where user_id = %s and revoked_at is null",
                    (user_id,),
                )
                # Revoga também a sessão correspondente (jti)
                cur.execute(
                    "update auth_sessions set revoked_at = now() where jti = %s and revoked_at is null",
                    (session_jti,),
                )
                conn.commit()
                return None

            # IDLE timeout — checa auth_sessions.last_seen_at
            cur.execute(
                "select last_seen_at, revoked_at from auth_sessions where jti = %s",
                (session_jti,),
            )
            sess = cur.fetchone()
            if not sess or sess["revoked_at"] is not None:
                conn.commit()
                return None
            idle_threshold = now - timedelta(days=REFRESH_IDLE_TIMEOUT_DAYS)
            if sess["last_seen_at"] < idle_threshold:
                cur.execute(
                    "update auth_sessions set revoked_at = now() where jti = %s",
                    (session_jti,),
                )
                cur.execute(
                    "update auth_refresh_tokens set revoked_at = now() where session_jti = %s",
                    (session_jti,),
                )
                conn.commit()
                return None

            # OK: marca atual como usado e emite novo refresh (mesma session_jti)
            cur.execute(
                "update auth_refresh_tokens set used_at = now() where token_hash = %s",
                (h,),
            )
            new_token = _generate_token()
            new_expires = now + timedelta(days=REFRESH_TOKEN_TTL_DAYS)
            cur.execute(
                """
                insert into auth_refresh_tokens
                  (token_hash, user_id, session_jti, expires_at, ip, user_agent)
                values (%s, %s, %s, %s, %s, %s)
                """,
                (_hash(new_token), user_id, session_jti, new_expires, ip, user_agent),
            )
            # Atualiza last_seen_at da sessão
            cur.execute(
                "update auth_sessions set last_seen_at = now() where jti = %s",
                (session_jti,),
            )
            conn.commit()

        return {
            "user_id": user_id,
            "session_jti": session_jti,
            "new_refresh_token": new_token,
        }
    except Exception as exc:
        logger.error("[refresh] erro ao consumir token: %s", exc, exc_info=True)
        return None


def revoke_refresh_token(plain_token: str) -> bool:
    """Revoga um único refresh token (usado no logout). Retorna True se algo foi revogado."""
    if not plain_token or not plain_token.startswith(REFRESH_TOKEN_PREFIX):
        return False
    h = _hash(plain_token)
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "update auth_refresh_tokens set revoked_at = now() where token_hash = %s and revoked_at is null",
                (h,),
            )
            ok = cur.rowcount > 0
            conn.commit()
        return ok
    except Exception as exc:
        print(f"[refresh] revoke falhou: {exc}", file=sys.stderr)
        return False


def revoke_session_refresh_tokens(session_jti: str) -> int:
    """Revoga todos os refresh tokens de uma sessão (chamado por revoke_session)."""
    if not session_jti:
        return 0
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "update auth_refresh_tokens set revoked_at = now() where session_jti = %s and revoked_at is null",
                (session_jti,),
            )
            n = cur.rowcount
            conn.commit()
        return n or 0
    except Exception as exc:
        print(f"[refresh] revoke_session falhou: {exc}", file=sys.stderr)
        return 0


def revoke_user_refresh_tokens(user_id: int) -> int:
    """Revoga TODOS os refresh tokens de um user (used em panic / replay detection)."""
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "update auth_refresh_tokens set revoked_at = now() where user_id = %s and revoked_at is null",
                (int(user_id),),
            )
            n = cur.rowcount
            conn.commit()
        return n or 0
    except Exception as exc:
        print(f"[refresh] revoke_user falhou: {exc}", file=sys.stderr)
        return 0


def cleanup_expired_refresh_tokens() -> int:
    """Limpeza periódica de refresh tokens expirados/revogados há mais de 30d.
    Não usar pra produção sem chamar de um cron — só housekeeping."""
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                delete from auth_refresh_tokens
                where (revoked_at is not null and revoked_at < now() - interval '30 days')
                   or (expires_at < now() - interval '7 days')
                """
            )
            n = cur.rowcount
            conn.commit()
        return n or 0
    except Exception as exc:
        print(f"[refresh] cleanup falhou: {exc}", file=sys.stderr)
        return 0
