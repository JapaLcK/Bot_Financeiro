"""Sessões lidas por quem tem SÓ o `dashboard_token` (12h): o /painel depois de 15 min parado.

O `auth_token` (15 min) já expirou e o cookie sumiu; o jti da sessão corrente vem do
`dashboard_token`, que o gate (`resolve_dashboard_user_id`) já decodificou. Antes,
`_current_session_jti` só lia o `auth_token`: o "encerrar os outros" revogava a PRÓPRIA sessão.

Controle negativo (à mão): voltar `_current_session_jti` a ler só o `auth_token` deixa
vermelhos os 3 casos. Positivo: a OUTRA sessão é revogada, e o Bearer do app segue em tests/test_sessoes_app.py.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
from conftest import promote_to_pro
from core.sessions import create_session, get_active_session


def _cliente_so_dashboard(user_id: int, jti: str) -> tuple[TestClient, dict]:
    c = TestClient(dashboard.app)
    c.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, dashboard.make_dashboard_token(user_id, hours=1, jti=jti))
    c.cookies.set(dashboard.CSRF_COOKIE_NAME, "t")
    return c, {dashboard.CSRF_HEADER_NAME: "t"}


def _conta(user_id):
    uid = int(db.register_auth_user(f"so-dash-{user_id}@t.com", "senha-forte-123")["user_id"])
    promote_to_pro(uid)
    return uid, create_session(uid, ip="1.1.1.1"), create_session(uid, ip="2.2.2.2")


def test_lista_marca_a_atual(user_id):
    uid, atual, outra = _conta(user_id)
    c, h = _cliente_so_dashboard(uid, atual)
    corpo = c.get(f"/settings/{uid}/sessions", headers=h).json()
    assert corpo["current_jti"] == atual
    assert {s["jti"]: s["is_current"] for s in corpo["sessions"]} == {atual: True, outra: False}


def test_encerrar_as_outras_poupa_a_atual(user_id):
    uid, atual, outra = _conta(user_id)
    c, h = _cliente_so_dashboard(uid, atual)
    r = c.delete(f"/settings/{uid}/sessions", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["revoked"] == 1
    assert get_active_session(outra) is None
    assert get_active_session(atual) is not None


def test_encerrar_a_propria_pelo_jti_da_400(user_id):
    uid, atual, _ = _conta(user_id)
    c, h = _cliente_so_dashboard(uid, atual)
    assert c.delete(f"/settings/{uid}/sessions/{atual}", headers=h).status_code == 400
    assert get_active_session(atual) is not None
