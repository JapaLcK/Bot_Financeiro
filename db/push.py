"""
db/push.py — Device tokens do APNs (push do app iOS).

Guarda o token do aparelho (device token do APNs) associado ao usuário logado.
O registro vem do app: o AppDelegate obtém o token nativo e o entrega pro
WebView, que faz POST autenticado em /api/push/register (reusa os cookies de
sessão). O envio de push (core/services/push_service.py) lê daqui.

Regras:
- unique(token): o mesmo aparelho re-registrando atualiza o dono e o timestamp
  (troca de conta no mesmo device) — nunca duplica linha.
- environment ('sandbox'|'production'): o token só é válido no ambiente do APNs
  que o gerou; o sender precisa saber qual usar.
- delete_push_token: chamado quando o APNs devolve 410 (Unregistered) — o app
  foi desinstalado ou o token expirou.
"""
from __future__ import annotations

from .connection import get_conn


def register_push_token(
    user_id: int,
    token: str,
    platform: str = "ios",
    environment: str = "production",
) -> None:
    """Upsert do device token. Re-registro atualiza dono + updated_at."""
    token = (token or "").strip()
    if not token:
        return
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into push_tokens (user_id, token, platform, environment)
                values (%s, %s, %s, %s)
                on conflict (token) do update
                  set user_id = excluded.user_id,
                      platform = excluded.platform,
                      environment = excluded.environment,
                      updated_at = now()
                """,
                (int(user_id), token, platform, environment),
            )
        conn.commit()


def delete_push_token(token: str) -> None:
    """Remove um token (desinstalação / APNs 410 Unregistered)."""
    token = (token or "").strip()
    if not token:
        return
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("delete from push_tokens where token = %s", (token,))
        conn.commit()


def list_push_tokens(user_id: int) -> list[dict]:
    """Tokens ativos de um usuário (pode ter mais de um aparelho)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select token, platform, environment
                  from push_tokens
                 where user_id = %s
                 order by updated_at desc
                """,
                (int(user_id),),
            )
            return cur.fetchall() or []
