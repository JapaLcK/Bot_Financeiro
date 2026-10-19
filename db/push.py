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
- delete_push_token: chamado quando o APNs recusa o token (410 Unregistered,
  BadDeviceToken, DeviceTokenNotForTopic) e na baixa feita pelo dono. Com
  `user_id`, só apaga se o token for dele.
- session_jti: a sessão (auth_sessions.jti) que registrou o token. `list_push_tokens`
  só devolve token de sessão ativa e com refresh utilizável (D4 do plano do push);
  NULL (registro antigo) nunca recebe.
- push "reconectar": preferência (auth_accounts.push_bank_reconnect_opt_out) e
  trava por conexão (open_finance_connections.push_reconectar_em).
"""
from __future__ import annotations

import db_support as _db_support

from .connection import get_conn


def register_push_token(
    user_id: int,
    token: str,
    platform: str = "ios",
    environment: str = "production",
    session_jti: str | None = None,
) -> None:
    """Upsert do device token. Re-registro atualiza dono, sessão + updated_at."""
    token = (token or "").strip()
    if not token:
        return
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into push_tokens (user_id, token, platform, environment, session_jti)
                values (%s, %s, %s, %s, %s)
                on conflict (token) do update
                  set user_id = excluded.user_id,
                      session_jti = excluded.session_jti,
                      platform = excluded.platform,
                      environment = excluded.environment,
                      updated_at = now()
                """,
                (int(user_id), token, platform, environment, session_jti),
            )
        conn.commit()


def delete_push_token(token: str, user_id: int | None = None) -> None:
    """Remove um token. Com `user_id`, só se for dele (baixa pela rota); sem ele,
    por token (APNs recusou o token — caminho do sender)."""
    token = (token or "").strip()
    if not token:
        return
    with get_conn() as conn:
        with conn.cursor() as cur:
            if user_id is None:
                cur.execute("delete from push_tokens where token = %s", (token,))
            else:
                cur.execute(
                    "delete from push_tokens where token = %s and user_id = %s",
                    (token, int(user_id)),
                )
        conn.commit()


def list_push_tokens(user_id: int) -> list[dict]:
    """Tokens entregáveis de um usuário: sessão do registro ativa (não revogada)
    e com um refresh ainda utilizável (não usado, não revogado, não vencido)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select pt.token, pt.platform, pt.environment
                  from push_tokens pt
                  join auth_sessions s on s.jti = pt.session_jti
                                      and s.user_id = pt.user_id
                                      and s.revoked_at is null
                 where pt.user_id = %s
                   and exists (select 1 from auth_refresh_tokens r
                                where r.session_jti = pt.session_jti
                                  and r.user_id = pt.user_id
                                  and r.used_at is null
                                  and r.revoked_at is null
                                  and r.expires_at > now())
                 order by pt.updated_at desc
                """,
                (int(user_id),),
            )
            return cur.fetchall() or []


def set_push_bank_reconnect_opt_out(user_id: int, opt_out: bool) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update auth_accounts set push_bank_reconnect_opt_out = %s where user_id = %s",
                (opt_out, int(user_id)),
            )
        conn.commit()
    _db_support.invalidate_auth_user_cache(user_id)


def push_bank_reconnect_bloqueado(user_id: int) -> bool:
    """A pessoa desligou o push "banco precisa de atenção"?

    Leitura direta, sem o cache de 10 s do `get_auth_user`: decide se manda
    mensagem proativa. `user_id` não é unique em `auth_accounts`: bloqueado se
    QUALQUER linha tem opt-out (agregação, não a ordem física). Nenhuma linha =
    True (sem informação não autoriza), como `get_whatsapp_updates_opt_out`.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select count(*) as n,"
                "       coalesce(bool_or(coalesce(push_bank_reconnect_opt_out, false)), false) as opt"
                "  from auth_accounts where user_id = %s",
                (int(user_id),),
            )
            row = cur.fetchone()
    return row["n"] == 0 or bool(row["opt"])


# Trava do push "reconectar". Nenhuma toca `updated_at` (o resto do app o lê como
# "mudou de verdade"). Toda query filtra por user_id.

def limpar_push_reconectar(user_id: int) -> None:
    """Conexão que voltou a SAUDÁVEL na tela (atualizada/parcial) pode avisar num
    próximo episódio. Sair de "pede reconexão" para "erro temporário" não abre a
    trava: o banco que oscila LOGIN_ERROR → ERROR → LOGIN_ERROR é um episódio só.
    Mesma leitura da tela (`_read_open_finance_connections`), sem classificador novo."""
    from .open_finance import _read_open_finance_connections
    with get_conn() as conn:
        with conn.cursor() as cur:
            saudaveis = [int(c["id"]) for c in _read_open_finance_connections(cur, int(user_id))
                         if c["ui"].get("state") in ("updated", "partial")]
            if saudaveis:
                cur.execute(
                    "update open_finance_connections set push_reconectar_em = null"
                    " where user_id = %s and push_reconectar_em is not null and id = any(%s)",
                    (int(user_id), saudaveis),
                )
        conn.commit()


def reivindicar_push_reconectar(user_id: int, ids: list[int]) -> list[int]:
    """Carimba as conexões ainda sem push neste episódio e devolve quais foram."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update open_finance_connections set push_reconectar_em = now()"
                " where user_id = %s and id = any(%s) and push_reconectar_em is null"
                " returning id",
                (int(user_id), [int(i) for i in ids]),
            )
            rows = cur.fetchall() or []
        conn.commit()
    return [r["id"] for r in rows]


def soltar_push_reconectar(user_id: int, ids: list[int]) -> None:
    """Desfaz a reivindicação (o push não saiu) para a próxima varredura tentar."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update open_finance_connections set push_reconectar_em = null"
                " where user_id = %s and id = any(%s)",
                (int(user_id), [int(i) for i in ids]),
            )
        conn.commit()
