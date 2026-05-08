"""
tests/test_sessions.py — Cobertura de sessoes ativas (core/sessions.py + endpoints).

Cobre:
- Helper: create / get_active / list / revoke / revoke_other / touch / device_label
- Endpoints: GET /sessions, DELETE /sessions/{jti}, DELETE /sessions
- _get_current_user grandfathering: tokens sem jti continuam validos
- _get_current_user rejeita tokens com jti revogado
- /auth/logout revoga a sessao corrente
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

os.environ.setdefault("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())

import db
import frontend.finance_bot_websocket_custom as dashboard
from core.sessions import (
    create_session,
    device_label,
    get_active_session,
    list_user_sessions,
    revoke_other_sessions,
    revoke_session,
    touch_session,
)
from db.connection import get_conn


def _csrf_headers(client: TestClient) -> dict:
    token = "test-csrf-token"
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, token)
    return {dashboard.CSRF_HEADER_NAME: token}


def _set_session_cookies(client: TestClient, user_id: int, email: str, jti: str | None) -> None:
    """Injeta auth_token (com ou sem jti) e dashboard_token. TestClient roda em
    http:// e cookies Secure nao roundtripam — set manual e o caminho usado nos
    outros tests."""
    auth_token = dashboard._make_jwt(user_id, email, jti=jti) if jti else dashboard._make_jwt(user_id, email)
    client.cookies.set(dashboard.AUTH_COOKIE_NAME, auth_token)
    client.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, dashboard.make_dashboard_token(user_id, hours=1))


# ── Helper unit tests ──────────────────────────────────────────────────────

def test_create_session_persists_row(user_id):
    jti = create_session(user_id, ip="203.0.113.5", user_agent="pytest/1.0")
    row = get_active_session(jti)
    assert row is not None
    assert row["user_id"] == user_id
    assert row["ip"] == "203.0.113.5"
    assert row["user_agent"] == "pytest/1.0"


def test_get_active_session_returns_none_for_revoked(user_id):
    jti = create_session(user_id, ip="1.2.3.4")
    assert revoke_session(user_id, jti) is True
    assert get_active_session(jti) is None


def test_revoke_session_only_works_for_owner(user_id):
    """Defesa em profundidade: jti+user_id devem casar para revogar."""
    jti = create_session(user_id, ip="1.2.3.4")
    foreign_user = user_id + 1
    assert revoke_session(foreign_user, jti) is False
    assert get_active_session(jti) is not None  # ainda ativa


def test_revoke_other_sessions_keeps_current(user_id):
    j1 = create_session(user_id, ip="1.1.1.1")
    j2 = create_session(user_id, ip="2.2.2.2")
    j3 = create_session(user_id, ip="3.3.3.3")
    n = revoke_other_sessions(user_id, current_jti=j2)
    assert n == 2
    assert get_active_session(j1) is None
    assert get_active_session(j2) is not None  # corrente preservada
    assert get_active_session(j3) is None


def test_revoke_other_sessions_without_current_revokes_all(user_id):
    j1 = create_session(user_id, ip="1.1.1.1")
    j2 = create_session(user_id, ip="2.2.2.2")
    n = revoke_other_sessions(user_id, current_jti=None)
    assert n == 2
    assert get_active_session(j1) is None
    assert get_active_session(j2) is None


def test_list_user_sessions_only_returns_active(user_id):
    j1 = create_session(user_id, ip="1.1.1.1")
    j2 = create_session(user_id, ip="2.2.2.2")
    revoke_session(user_id, j1)
    rows = list_user_sessions(user_id)
    jtis = {r["jti"] for r in rows}
    assert j2 in jtis
    assert j1 not in jtis


def test_touch_session_debounces(user_id):
    """A primeira chamada nao move last_seen porque o row foi criado agora
    (last_seen_at = now). So depois de TOUCH_DEBOUNCE_SEC."""
    jti = create_session(user_id, ip="1.1.1.1")
    before = get_active_session(jti)["last_seen_at"]
    touch_session(jti)  # debounced — ainda dentro da janela
    after = get_active_session(jti)["last_seen_at"]
    assert after == before

    # Forca last_seen para o passado e tenta de novo: deve atualizar.
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update auth_sessions set last_seen_at = %s where jti = %s",
                (datetime.now(timezone.utc) - timedelta(minutes=5), jti),
            )
        conn.commit()
    touch_session(jti)
    after2 = get_active_session(jti)["last_seen_at"]
    assert after2 > before


def test_device_label_heuristics():
    chrome_mac = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) Chrome/120.0.0.0 Safari/537.36"
    safari_ios = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Safari/604.1"
    firefox_linux = "Mozilla/5.0 (X11; Linux x86_64; rv:122.0) Gecko/20100101 Firefox/122.0"
    assert device_label(chrome_mac) == "Chrome • macOS"
    assert device_label(safari_ios) == "Safari • iOS"
    assert device_label(firefox_linux) == "Firefox • Linux"
    assert device_label(None) == "Dispositivo desconhecido"
    assert device_label("") == "Dispositivo desconhecido"


# ── Endpoint tests ─────────────────────────────────────────────────────────

def test_sessions_endpoint_lists_with_current_flag(user_id):
    email = f"sess-{user_id}@t.com"
    user = db.register_auth_user(email, "senha-forte-123")
    real_uid = int(user["user_id"])

    j_other = create_session(real_uid, ip="1.1.1.1", user_agent="other-ua")
    j_current = create_session(real_uid, ip="2.2.2.2", user_agent="current-ua")

    client = TestClient(dashboard.app)
    _set_session_cookies(client, real_uid, email, j_current)

    resp = client.get(f"/settings/{real_uid}/sessions", headers=_csrf_headers(client))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["current_jti"] == j_current
    by_jti = {s["jti"]: s for s in body["sessions"]}
    assert by_jti[j_current]["is_current"] is True
    assert by_jti[j_other]["is_current"] is False


def test_revoke_single_session_blocks_current_jti(user_id):
    email = f"block-{user_id}@t.com"
    user = db.register_auth_user(email, "senha-forte-123")
    real_uid = int(user["user_id"])

    j_current = create_session(real_uid, ip="1.1.1.1")
    client = TestClient(dashboard.app)
    _set_session_cookies(client, real_uid, email, j_current)

    resp = client.delete(
        f"/settings/{real_uid}/sessions/{j_current}",
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 400, resp.text
    assert get_active_session(j_current) is not None  # nao revogou


def test_revoke_single_session_works_for_other(user_id):
    email = f"single-{user_id}@t.com"
    user = db.register_auth_user(email, "senha-forte-123")
    real_uid = int(user["user_id"])

    j_current = create_session(real_uid, ip="1.1.1.1")
    j_other = create_session(real_uid, ip="2.2.2.2")
    client = TestClient(dashboard.app)
    _set_session_cookies(client, real_uid, email, j_current)

    resp = client.delete(
        f"/settings/{real_uid}/sessions/{j_other}",
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 200, resp.text
    assert get_active_session(j_other) is None
    assert get_active_session(j_current) is not None


def test_revoke_others_endpoint(user_id):
    email = f"others-{user_id}@t.com"
    user = db.register_auth_user(email, "senha-forte-123")
    real_uid = int(user["user_id"])

    j_current = create_session(real_uid, ip="1.1.1.1")
    j_a = create_session(real_uid, ip="2.2.2.2")
    j_b = create_session(real_uid, ip="3.3.3.3")
    client = TestClient(dashboard.app)
    _set_session_cookies(client, real_uid, email, j_current)

    resp = client.delete(f"/settings/{real_uid}/sessions", headers=_csrf_headers(client))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["revoked"] == 2
    assert get_active_session(j_a) is None
    assert get_active_session(j_b) is None
    assert get_active_session(j_current) is not None


# ── _get_current_user behavior ─────────────────────────────────────────────

def test_get_current_user_grandfathers_token_without_jti(user_id):
    """Tokens legados (sem jti) continuam validos no rollout."""
    email = f"legacy-{user_id}@t.com"
    user = db.register_auth_user(email, "senha-forte-123")
    real_uid = int(user["user_id"])

    client = TestClient(dashboard.app)
    # Sem jti — token estilo pre-rollout
    _set_session_cookies(client, real_uid, email, jti=None)

    resp = client.get("/auth/me", headers=_csrf_headers(client))
    assert resp.status_code == 200, resp.text


def test_get_current_user_rejects_revoked_jti(user_id):
    email = f"revoked-{user_id}@t.com"
    user = db.register_auth_user(email, "senha-forte-123")
    real_uid = int(user["user_id"])

    jti = create_session(real_uid, ip="1.1.1.1")
    revoke_session(real_uid, jti)

    client = TestClient(dashboard.app)
    _set_session_cookies(client, real_uid, email, jti)

    resp = client.get("/auth/me", headers=_csrf_headers(client))
    assert resp.status_code == 401, resp.text


# ── Logout revogation ──────────────────────────────────────────────────────

def test_logout_revokes_current_session(user_id):
    email = f"logout-{user_id}@t.com"
    user = db.register_auth_user(email, "senha-forte-123")
    real_uid = int(user["user_id"])

    jti = create_session(real_uid, ip="1.1.1.1")
    client = TestClient(dashboard.app)
    _set_session_cookies(client, real_uid, email, jti)

    resp = client.post("/auth/logout", headers=_csrf_headers(client))
    assert resp.status_code == 200
    assert get_active_session(jti) is None


# ── dashboard_token jti binding ────────────────────────────────────────────

def test_dashboard_token_with_revoked_jti_is_rejected(user_id):
    """Apos revogar a sessao, o dashboard_token correspondente deixa de funcionar."""
    email = f"dash-rev-{user_id}@t.com"
    user = db.register_auth_user(email, "senha-forte-123")
    real_uid = int(user["user_id"])

    jti = create_session(real_uid, ip="1.1.1.1")
    client = TestClient(dashboard.app)
    # Apenas o dashboard_token (sem auth_token) — simula um dispositivo onde
    # o usuario so abriu o dashboard via magic-link ou cookie do dashboard.
    client.cookies.set(
        dashboard.DASHBOARD_COOKIE_NAME,
        dashboard.make_dashboard_token(real_uid, hours=1, jti=jti),
    )

    # Antes de revogar: o endpoint protegido por _authorize_dashboard_access funciona.
    ok = client.get(f"/settings/{real_uid}/security", headers=_csrf_headers(client))
    assert ok.status_code == 200, ok.text

    revoke_session(real_uid, jti)

    # Depois de revogar: 401, mesmo que o JWT do dashboard ainda nao tenha expirado.
    blocked = client.get(f"/settings/{real_uid}/security", headers=_csrf_headers(client))
    assert blocked.status_code == 401, blocked.text


def test_dashboard_token_legacy_no_jti_is_grandfathered(user_id):
    """dashboard_token sem jti (rollout) continua valido ate expirar naturalmente."""
    email = f"dash-legacy-{user_id}@t.com"
    user = db.register_auth_user(email, "senha-forte-123")
    real_uid = int(user["user_id"])

    client = TestClient(dashboard.app)
    # Token sem jti (legacy)
    client.cookies.set(
        dashboard.DASHBOARD_COOKIE_NAME,
        dashboard.make_dashboard_token(real_uid, hours=1),
    )

    resp = client.get(f"/settings/{real_uid}/security", headers=_csrf_headers(client))
    assert resp.status_code == 200, resp.text


def test_magic_link_creates_auth_session(user_id):
    """/d/{code} agora cria uma row em auth_sessions e a lista de sessoes a inclui."""
    email = f"magic-{user_id}@t.com"
    user = db.register_auth_user(email, "senha-forte-123")
    real_uid = int(user["user_id"])

    # Cria um magic-link para esse usuario
    from datetime import datetime, timedelta, timezone
    code = f"magic-{user_id}-test"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into dashboard_sessions (code, user_id, expires_at)
                values (%s, %s, %s)
                """,
                (code, real_uid, datetime.now(timezone.utc) + timedelta(minutes=5)),
            )
        conn.commit()

    before = len(list_user_sessions(real_uid))

    client = TestClient(dashboard.app)
    resp = client.get(f"/d/{code}", follow_redirects=False)
    assert resp.status_code == 302, resp.text
    assert "dashboard_token=" in resp.headers.get("set-cookie", "")

    after = list_user_sessions(real_uid)
    assert len(after) == before + 1  # uma sessao a mais foi criada
