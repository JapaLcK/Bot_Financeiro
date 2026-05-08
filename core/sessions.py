"""
core/sessions.py — Sessoes ativas do dashboard (per-device).

Cada login bem-sucedido insere uma row em `auth_sessions` e o JWT carrega o
`jti` correspondente. `_get_current_user` confere se a sessao esta ativa a
cada request autenticada — permite revogar individualmente ou "encerrar
todas as outras".

Bot do WhatsApp/Discord NAO tem sessao trackeada aqui. Apenas o dashboard.

Notas de design:
- last_seen_at e atualizado com debounce (so se passou >= TOUCH_DEBOUNCE_SEC)
  para nao martelar o banco em cada request.
- Tokens legados sem `jti` sao grandfathered (nao quebra logins existentes
  no rollout); novos logins sempre embedam jti.
- Falha de DB no touch e silenciosa — auth nao pode quebrar por causa de
  metrica de last_seen.
"""
from __future__ import annotations

import sys
import uuid
from typing import Any

from db.connection import get_conn


TOUCH_DEBOUNCE_SEC = 60


def _new_jti() -> str:
    return uuid.uuid4().hex


def create_session(
    user_id: int,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
) -> str:
    """Cria uma sessao ativa e devolve o jti. Levanta em caso de falha de DB
    (login nao deve completar sem ter sessao registrada)."""
    jti = _new_jti()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into auth_sessions (jti, user_id, ip, user_agent)
                values (%s, %s, %s, %s)
                """,
                (jti, int(user_id), ip, (user_agent or "")[:512] or None),
            )
        conn.commit()
    return jti


def get_active_session(jti: str | None) -> dict | None:
    """Retorna a row se ativa (revoked_at IS NULL), senao None."""
    if not jti:
        return None
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select jti, user_id, ip, user_agent, created_at, last_seen_at
                    from auth_sessions
                    where jti = %s and revoked_at is null
                    """,
                    (jti,),
                )
                return cur.fetchone()
    except Exception as exc:
        print(f"[sessions] get_active_session failed: {exc}", file=sys.stderr)
        return None


def touch_session(jti: str | None) -> None:
    """Atualiza last_seen_at, com debounce de TOUCH_DEBOUNCE_SEC. Falha silenciosa."""
    if not jti:
        return
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    update auth_sessions
                       set last_seen_at = now()
                     where jti = %s
                       and revoked_at is null
                       and last_seen_at < now() - (%s || ' seconds')::interval
                    """,
                    (jti, str(TOUCH_DEBOUNCE_SEC)),
                )
            conn.commit()
    except Exception as exc:
        print(f"[sessions] touch_session failed: {exc}", file=sys.stderr)


def list_user_sessions(user_id: int) -> list[dict]:
    """Lista as sessoes ATIVAS do usuario, mais recente primeiro."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select jti, ip, user_agent, created_at, last_seen_at
                    from auth_sessions
                    where user_id = %s and revoked_at is null
                    order by last_seen_at desc
                    """,
                    (int(user_id),),
                )
                return list(cur.fetchall())
    except Exception as exc:
        print(f"[sessions] list_user_sessions failed: {exc}", file=sys.stderr)
        return []


def revoke_session(user_id: int, jti: str) -> bool:
    """Revoga uma sessao especifica do usuario. Retorna True se algo foi revogado.

    O `user_id` e exigido para evitar que um usuario revogue a sessao de outro
    (defesa em profundidade — a rota ja autoriza, mas a query reforca)."""
    if not jti:
        return False
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    update auth_sessions
                       set revoked_at = now()
                     where jti = %s
                       and user_id = %s
                       and revoked_at is null
                    """,
                    (jti, int(user_id)),
                )
                changed = cur.rowcount
            conn.commit()
        return bool(changed)
    except Exception as exc:
        print(f"[sessions] revoke_session failed: {exc}", file=sys.stderr)
        return False


def revoke_other_sessions(user_id: int, current_jti: str | None) -> int:
    """Revoga todas as sessoes ativas do usuario EXCETO a corrente.

    Se current_jti e None/vazio, revoga todas. Retorna a contagem revogada."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                if current_jti:
                    cur.execute(
                        """
                        update auth_sessions
                           set revoked_at = now()
                         where user_id = %s
                           and jti <> %s
                           and revoked_at is null
                        """,
                        (int(user_id), current_jti),
                    )
                else:
                    cur.execute(
                        """
                        update auth_sessions
                           set revoked_at = now()
                         where user_id = %s and revoked_at is null
                        """,
                        (int(user_id),),
                    )
                changed = cur.rowcount
            conn.commit()
        return int(changed or 0)
    except Exception as exc:
        print(f"[sessions] revoke_other_sessions failed: {exc}", file=sys.stderr)
        return 0


def device_label(user_agent: str | None) -> str:
    """Heuristica simples para extrair um label legivel do User-Agent.

    Devolve algo tipo "Chrome on macOS" ou "Mobile Safari on iOS". Best-effort,
    nada de detec-tudo — UA-parsers de verdade sao caros e dependem de db.
    """
    ua = (user_agent or "").strip()
    if not ua:
        return "Dispositivo desconhecido"
    ua_low = ua.lower()
    # Browser
    browser = "Navegador"
    if "edg/" in ua_low:
        browser = "Edge"
    elif "chrome/" in ua_low and "chromium" not in ua_low and "edg/" not in ua_low:
        browser = "Chrome"
    elif "firefox/" in ua_low:
        browser = "Firefox"
    elif "safari/" in ua_low and "chrome/" not in ua_low:
        browser = "Safari"
    elif "opera" in ua_low or "opr/" in ua_low:
        browser = "Opera"
    # OS
    os_name = "Outro"
    if "iphone" in ua_low or "ios" in ua_low:
        os_name = "iOS"
    elif "ipad" in ua_low:
        os_name = "iPadOS"
    elif "android" in ua_low:
        os_name = "Android"
    elif "mac os x" in ua_low or "macintosh" in ua_low:
        os_name = "macOS"
    elif "windows" in ua_low:
        os_name = "Windows"
    elif "linux" in ua_low:
        os_name = "Linux"
    return f"{browser} • {os_name}"
