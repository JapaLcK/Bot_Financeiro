import pytest
from fastapi.testclient import TestClient

import core.admin_dashboard as admin_dashboard
import frontend.finance_bot_websocket_custom as dashboard


@pytest.fixture(autouse=True)
def configured_admin(monkeypatch):
    async def _noop_log(*args, **kwargs):
        return None

    monkeypatch.setattr(admin_dashboard, "ADMIN_DASHBOARD_PASSWORD", "secret-admin")
    monkeypatch.setattr(admin_dashboard, "ADMIN_DASHBOARD_PASSWORD_HASH", "")
    monkeypatch.setattr(admin_dashboard, "log_system_event", _noop_log)


def _csrf_headers(client: TestClient) -> dict[str, str]:
    token = "test-admin-csrf"
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, token)
    return {dashboard.CSRF_HEADER_NAME: token}


def test_admin_dashboard_redirects_without_admin_session():
    response = TestClient(dashboard.app).get("/admin", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"


def test_admin_login_sets_http_only_cookie_and_unlocks_dashboard():
    client = TestClient(dashboard.app, base_url="https://testserver")

    login = client.post(
        "/admin/auth/login",
        headers=_csrf_headers(client),
        json={"username": "admin", "password": "secret-admin"},
    )

    assert login.status_code == 200
    set_cookie = login.headers["set-cookie"]
    assert "admin_auth_token=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie
    assert "SameSite=strict" in set_cookie
    assert "Path=/admin" in set_cookie

    me = client.get("/admin/auth/me")
    assert me.status_code == 200
    assert me.json() == {"username": "admin"}

    dashboard_response = client.get("/admin", follow_redirects=False)
    assert dashboard_response.status_code == 200
    assert dashboard_response.headers["cache-control"] == "no-store"


def test_admin_logout_clears_http_only_cookie():
    client = TestClient(dashboard.app)
    response = client.post("/admin/auth/logout", headers=_csrf_headers(client))

    assert response.status_code == 200
    set_cookie = response.headers["set-cookie"]
    assert "admin_auth_token=" in set_cookie
    assert "Max-Age=0" in set_cookie
    assert "Path=/admin" in set_cookie
