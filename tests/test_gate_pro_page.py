"""gate_pro_page (/changelog, /blog/{slug}) aceita as mesmas sessões que o
/auth/validate: com o access expirado e o dashboard_token válido, o Pro vê a
página em vez de cair no login e dali na /home."""
import uuid

import pytest
from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
from core.sessions import create_session, revoke_session


@pytest.fixture
def so_dashboard():
    """Client só com o dashboard_token (sem auth_token) de uma sessão ativa."""
    uid = int(db.register_auth_user(f"gate-pro-{uuid.uuid4().hex}@example.com", "senha-teste-123")["user_id"])
    jti = create_session(uid)
    client = TestClient(dashboard.app)
    client.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, dashboard.make_dashboard_token(uid, hours=1, jti=jti))
    yield client, uid, jti
    client.close()


def _changelog(client, monkeypatch, pro):
    monkeypatch.setattr("core.services.plan_service.is_pro", lambda uid: pro)
    return client.get("/changelog", follow_redirects=False)


def test_pro_so_com_dashboard_token_ve_a_pagina(so_dashboard, monkeypatch):
    client, _, _ = so_dashboard
    assert _changelog(client, monkeypatch, pro=True).status_code == 200


def test_sessao_revogada_vai_para_o_login(so_dashboard, monkeypatch):
    client, uid, jti = so_dashboard
    revoke_session(uid, jti)
    response = _changelog(client, monkeypatch, pro=True)
    assert response.status_code == 302
    assert response.headers["location"] == "/login?next=/changelog"


def test_nao_pro_so_com_dashboard_token_vai_para_precos(so_dashboard, monkeypatch):
    client, _, _ = so_dashboard
    response = _changelog(client, monkeypatch, pro=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/precos"
