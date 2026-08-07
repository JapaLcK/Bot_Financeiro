"""One-click unsubscribe (RFC 8058) — entregabilidade Gmail.

Cobre as duas pontas do fluxo:
- Os e-mails dos agentes (e o helper unsub_headers) saem com List-Unsubscribe
  E List-Unsubscribe-Post, que é o que faz o Gmail mostrar o botão nativo
  "Cancelar inscrição" (a alternativa do usuário a "Marcar como spam").
- O POST /unsubscribe (destino do one-click do Gmail/Yahoo) valida o token
  HMAC e marca engagement_opt_out, sem interação.
"""
from __future__ import annotations

import asyncio
import uuid
from urllib.parse import parse_qs, urlparse

from db.connection import get_conn

import frontend.finance_bot_websocket_custom as app_mod
from core.services.email_service import make_unsub_url, unsub_headers


def test_unsub_headers_tem_one_click():
    h = unsub_headers("https://pigbankai.com/unsubscribe?uid=1&token=abc")
    assert h["List-Unsubscribe"] == "<https://pigbankai.com/unsubscribe?uid=1&token=abc>"
    assert h["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


def test_agent_report_email_sai_com_headers_one_click(monkeypatch):
    import core.services.email_service as es

    captured: dict = {}

    def fake_send(to, subject, html_body, text_body=None, from_addr=None,
                  headers=None, attachments=None):
        captured.update(headers=headers)
        return True

    monkeypatch.setattr(es, "send_email", fake_send)

    assert es.send_agent_report_email(
        "user@example.com", 42, "🎤 Repórter — A manchete de agosto",
        "<p>Sobrou R$ 400,00</p>", kind="reporter",
    )
    headers = captured["headers"]
    assert "/unsubscribe?uid=42&token=" in headers["List-Unsubscribe"]
    assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


# ─── POST /unsubscribe (one-click) ────────────────────────────────────────────

def _make_auth_account(user_id: int) -> str:
    email = f"unsub-{uuid.uuid4().hex[:8]}@test.local"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into auth_accounts(user_id, email, password_hash) values (%s, %s, 'x')",
                (user_id, email),
            )
        conn.commit()
    return email


def _engagement_opt_out(user_id: int) -> bool:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select engagement_opt_out from auth_accounts where user_id = %s",
                (user_id,),
            )
            return bool(cur.fetchone()["engagement_opt_out"])


def _token_for(user_id: int, email: str) -> str:
    qs = parse_qs(urlparse(make_unsub_url(user_id, email)).query)
    return qs["token"][0]


def test_post_unsubscribe_one_click_marca_opt_out(user_id):
    email = _make_auth_account(user_id)
    token = _token_for(user_id, email)

    res = asyncio.run(app_mod.unsubscribe_one_click(user_id, token))

    assert res.status_code == 200
    assert _engagement_opt_out(user_id) is True


def test_post_unsubscribe_token_invalido_da_400(user_id):
    _make_auth_account(user_id)

    res = asyncio.run(app_mod.unsubscribe_one_click(user_id, "token-forjado"))

    assert res.status_code == 400
    assert _engagement_opt_out(user_id) is False


def test_get_unsubscribe_continua_funcionando(user_id):
    email = _make_auth_account(user_id)
    token = _token_for(user_id, email)

    res = asyncio.run(app_mod.unsubscribe(user_id, token))

    assert res.status_code == 200
    assert _engagement_opt_out(user_id) is True


def test_post_unsubscribe_atravessa_o_middleware_csrf(user_id):
    """O POST do Gmail/Yahoo chega sem cookie/header CSRF — o path precisa
    estar em CSRF_EXEMPT_PATHS, senão o middleware devolve 403 antes do
    handler. Passa pela pilha HTTP completa (TestClient), não pelo handler
    direto como os testes acima."""
    from fastapi.testclient import TestClient

    email = _make_auth_account(user_id)
    token = _token_for(user_id, email)

    client = TestClient(app_mod.app)
    res = client.post(f"/unsubscribe?uid={user_id}&token={token}")

    assert res.status_code == 200
    assert _engagement_opt_out(user_id) is True
