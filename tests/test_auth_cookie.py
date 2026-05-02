import asyncio
import pytest
import uuid
from types import SimpleNamespace
from fastapi import HTTPException
from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard


def _clear_rate_limits(*identifiers: str):
    if not identifiers:
        return
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "delete from auth_rate_limits where identifier = any(%s)",
                (list(identifiers),),
            )
        conn.commit()


def test_login_sets_auth_token_cookie(monkeypatch):
    async def _noop_log(*args, **kwargs):
        return None

    monkeypatch.setattr(
        db,
        "login_auth_user",
        lambda email, password: {
            "user_id": 123,
            "email": email.strip().lower(),
            "plan": "free",
        },
    )
    monkeypatch.setattr(db, "create_link_code", lambda user_id, minutes_valid: "ABC123")
    monkeypatch.setattr(dashboard, "log_auth_login_event", _noop_log)

    response = TestClient(dashboard.app).post(
        "/auth/login",
        json={"email": "User@Example.com", "password": "secret123"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "user@example.com"
    assert "token" not in data
    set_cookie = response.headers["set-cookie"]
    assert "auth_token=" in set_cookie
    assert "dashboard_token=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie
    assert "SameSite=strict" in set_cookie
    assert "Max-Age=86400" in set_cookie
    assert "?token=" not in response.json()["dashboard_url"]


def test_dashboard_token_accepts_auth_cookie_without_authorization_header():
    token = dashboard._make_jwt(123, "user@example.com")
    client = TestClient(dashboard.app)
    client.cookies.set("auth_token", token)

    response = client.post("/auth/dashboard-token")

    assert response.status_code == 200
    assert response.json()["email"] == "user@example.com"
    assert response.json()["dashboard_url"].startswith(dashboard.DASHBOARD_URL)
    assert "?token=" not in response.json()["dashboard_url"]
    assert "dashboard_token=" in response.headers["set-cookie"]


def test_dashboard_token_still_accepts_authorization_header():
    token = dashboard._make_jwt(123, "user@example.com")

    response = TestClient(dashboard.app).post(
        "/auth/dashboard-token",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["email"] == "user@example.com"


def test_auth_validate_consumes_url_token_and_sets_dashboard_cookie(monkeypatch):
    monkeypatch.setattr(db, "consume_dashboard_session", lambda token: 123 if token == "one-time" else None)

    response = TestClient(dashboard.app).get("/auth/validate?token=one-time")

    assert response.status_code == 200
    assert response.json() == {"user_id": 123}
    assert "dashboard_token=" in response.headers["set-cookie"]


def test_auth_validate_accepts_dashboard_cookie_without_url_token():
    dashboard_token = dashboard.make_dashboard_token(123, hours=1)
    client = TestClient(dashboard.app)
    client.cookies.set("dashboard_token", dashboard_token)

    response = client.get("/auth/validate")

    assert response.status_code == 200
    assert response.json() == {"user_id": 123}


def test_users_endpoint_is_not_exposed_with_dashboard_cookie():
    dashboard_token = dashboard.make_dashboard_token(123, hours=1)
    client = TestClient(dashboard.app)
    client.cookies.set("dashboard_token", dashboard_token)

    response = client.get("/users")

    assert response.status_code == 404


def test_magic_link_redirect_sets_cookie_without_token_in_url(monkeypatch):
    monkeypatch.setattr(db, "consume_dashboard_session", lambda code: 123 if code == "one-time" else None)

    response = TestClient(dashboard.app).get("/d/one-time", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/app"
    assert "?token=" not in response.headers["location"]
    assert "dashboard_token=" in response.headers["set-cookie"]


def test_logout_expires_auth_and_dashboard_cookies():
    response = TestClient(dashboard.app).post("/auth/logout")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["clear-site-data"] == '"cookies", "storage"'
    set_cookie = response.headers.get_list("set-cookie")
    assert any(cookie.startswith("auth_token=") and "Max-Age=0" in cookie for cookie in set_cookie)
    assert any(cookie.startswith("dashboard_token=") and "Max-Age=0" in cookie for cookie in set_cookie)


def test_dashboard_html_is_not_cached():
    response = TestClient(dashboard.app).get("/app")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"


def test_health_endpoint_does_not_expose_infrastructure():
    response = TestClient(dashboard.app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_rate_limit_returns_friendly_error(monkeypatch):
    monkeypatch.setattr(db, "create_password_reset_token", lambda email: None)
    client = TestClient(dashboard.app)
    email = f"reset-{uuid.uuid4().hex}@example.com"
    _clear_rate_limits("ip:testclient", f"email:{email}")

    try:
        for _ in range(3):
            response = client.post("/auth/forgot-password", json={"email": email})
            assert response.status_code == 200

        response = client.post("/auth/forgot-password", json={"email": email})

        assert response.status_code == 429
        assert response.json()["detail"] == "Muitas tentativas. Aguarde alguns minutos e tente novamente."
    finally:
        _clear_rate_limits("ip:testclient", f"email:{email}")


def test_email_rate_limit_blocks_same_email_even_when_case_changes():
    email = f"login-{uuid.uuid4().hex}@example.com"
    email_identifier = f"email:{email}"
    ip_identifiers = [f"ip:198.51.100.{i}" for i in range(1, 7)]
    _clear_rate_limits(email_identifier, *ip_identifiers)
    try:
        for i in range(1, 6):
            request = SimpleNamespace(client=SimpleNamespace(host=f"198.51.100.{i}"))
            asyncio.run(dashboard._check_auth_rate_limits("login", request, email.upper()))

        with pytest.raises(HTTPException) as exc_info:
            request = SimpleNamespace(client=SimpleNamespace(host="198.51.100.6"))
            asyncio.run(dashboard._check_auth_rate_limits("login", request, email))

        assert exc_info.value.status_code == 429
        assert exc_info.value.detail == "Muitas tentativas. Aguarde alguns minutos e tente novamente."

        other_identifier = f"email:other-{uuid.uuid4().hex}@example.com"
        asyncio.run(dashboard._check_persistent_rate_limit("login", other_identifier, 5, 60))
    finally:
        _clear_rate_limits(email_identifier, *ip_identifiers, locals().get("other_identifier", ""))
