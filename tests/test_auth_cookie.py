import asyncio
import json
import pytest
import uuid
import zipfile
from io import BytesIO
from types import SimpleNamespace
from fastapi import HTTPException
from fastapi.testclient import TestClient

import db
from db.users import _hash_password
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


def _csrf_headers(client: TestClient) -> dict[str, str]:
    token = "test-csrf-token"
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, token)
    return {dashboard.CSRF_HEADER_NAME: token}


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

    client = TestClient(dashboard.app)
    response = client.post(
        "/auth/login",
        headers=_csrf_headers(client),
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

    response = client.post("/auth/dashboard-token", headers=_csrf_headers(client))

    assert response.status_code == 200
    assert response.json()["email"] == "user@example.com"
    assert response.json()["dashboard_url"].startswith(dashboard.DASHBOARD_URL)
    assert "?token=" not in response.json()["dashboard_url"]
    assert "dashboard_token=" in response.headers["set-cookie"]


def test_mutating_browser_routes_require_csrf_token():
    token = dashboard._make_jwt(123, "user@example.com")
    client = TestClient(dashboard.app)
    client.cookies.set("auth_token", token)

    response = client.post("/auth/dashboard-token")

    assert response.status_code == 403
    assert response.json()["detail"] == "Token CSRF inválido ou ausente."


def test_dashboard_token_still_accepts_authorization_header():
    token = dashboard._make_jwt(123, "user@example.com")
    client = TestClient(dashboard.app)

    response = client.post(
        "/auth/dashboard-token",
        headers={"Authorization": f"Bearer {token}", **_csrf_headers(client)},
    )

    assert response.status_code == 200
    assert response.json()["email"] == "user@example.com"


def test_auth_validate_rejects_legacy_url_token(monkeypatch):
    monkeypatch.setattr(db, "consume_dashboard_session", lambda token: 123 if token == "one-time" else None)
    response = TestClient(dashboard.app).get("/auth/validate?token=one-time")

    assert response.status_code == 401


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
    client = TestClient(dashboard.app)
    response = client.post("/auth/logout", headers=_csrf_headers(client))

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["clear-site-data"] == '"cookies", "storage"'
    set_cookie = response.headers.get_list("set-cookie")
    assert any(cookie.startswith("auth_token=") and "Max-Age=0" in cookie for cookie in set_cookie)
    assert any(cookie.startswith("dashboard_token=") and "Max-Age=0" in cookie for cookie in set_cookie)


def test_account_export_downloads_full_archive_without_password_hash(user_id):
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into auth_accounts (user_id, email, password_hash, phone_e164, phone_status)
                values (%s, %s, %s, %s, 'confirmed')
                """,
                (user_id, f"export-{user_id}@example.com", _hash_password("secret123"), f"+1555{user_id}"),
            )
            cur.execute(
                """
                insert into launches (user_id, tipo, valor, nota, categoria, criado_em)
                values (%s, 'despesa', 42.50, 'Teste exportacao', 'testes', now())
                """,
                (user_id,),
            )
            cur.execute(
                """
                insert into credit_cards (user_id, name, closing_day, due_day)
                values (%s, 'Cartao teste exportacao', 10, 20)
                returning id
                """,
                (user_id,),
            )
            card_id = cur.fetchone()["id"]
            cur.execute(
                """
                insert into credit_bills (user_id, card_id, period_start, period_end)
                values (%s, %s, current_date - interval '30 days', current_date)
                returning id
                """,
                (user_id, card_id),
            )
            bill_id = cur.fetchone()["id"]
            cur.execute(
                """
                insert into credit_transactions (
                  bill_id, user_id, card_id, valor, categoria, nota, purchased_at,
                  group_id, installment_no, installments_total
                )
                values (%s, %s, %s, 42.50, 'testes', 'Parcela com UUID', current_date, %s, 1, 2)
                """,
                (bill_id, user_id, card_id, uuid.uuid4()),
            )
        conn.commit()

    token = dashboard.make_dashboard_token(user_id, hours=1)
    client = TestClient(dashboard.app)
    client.cookies.set("dashboard_token", token)

    response = client.get("/auth/account/export")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/zip")
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        payload = json.loads(archive.read("dados.json"))
        assert "csv/lancamentos.csv" in archive.namelist()
    assert payload["manifesto"]["user_id"] == user_id
    assert payload["manifesto"]["datasets"]["lancamentos"] >= 1
    assert payload["manifesto"]["datasets"]["transacoes_cartao"] >= 1
    assert "password_hash" not in json.dumps(payload)


def test_delete_account_schedules_deletion_and_blocks_dashboard_token(user_id, monkeypatch):
    import core.services.email_service as email_service

    sent_deletion_emails = []
    monkeypatch.setattr(
        email_service,
        "send_account_deletion_scheduled_email",
        lambda to, scheduled_for: sent_deletion_emails.append((to, scheduled_for)) or True,
    )

    email = f"delete-{user_id}@example.com"
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into auth_accounts (user_id, email, password_hash, phone_e164, phone_status)
                values (%s, %s, %s, %s, 'confirmed')
                """,
                (user_id, email, _hash_password("secret123"), f"+1556{user_id}"),
            )
        conn.commit()

    token = dashboard.make_dashboard_token(user_id, hours=1)
    client = TestClient(dashboard.app)
    client.cookies.set("dashboard_token", token)

    wrong = client.request("DELETE", "/auth/account", headers=_csrf_headers(client), json={"password": "wrong"})
    assert wrong.status_code == 401

    response = client.request("DELETE", "/auth/account", headers=_csrf_headers(client), json={"password": "secret123"})
    assert response.status_code == 200
    assert response.json()["status"] == "scheduled"
    assert "auth_token=" in response.headers["set-cookie"]
    assert "dashboard_token=" in response.headers["set-cookie"]
    assert sent_deletion_emails == [(email, response.json()["deletion_scheduled_for"])]

    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select deletion_status, deletion_scheduled_for from auth_accounts where user_id=%s",
            (user_id,),
        )
        row = cur.fetchone()
    assert row["deletion_status"] == "scheduled"
    assert row["deletion_scheduled_for"] is not None

    blocked = TestClient(dashboard.app)
    blocked.cookies.set("dashboard_token", token)
    assert blocked.get("/auth/validate").status_code == 403


def test_account_deletion_is_scoped_to_authenticated_user(user_id):
    other_user_id = int(uuid.uuid4().int % 10_000_000_000)
    db.ensure_user(other_user_id)
    email = f"scoped-delete-{user_id}@example.com"
    other_email = f"scoped-keep-{other_user_id}@example.com"

    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into auth_accounts (user_id, email, password_hash, phone_e164, phone_status)
                    values (%s, %s, %s, %s, 'confirmed')
                    """,
                    (user_id, email, _hash_password("secret123"), f"+1557{user_id}"),
                )
                cur.execute(
                    """
                    insert into auth_accounts (user_id, email, password_hash, phone_e164, phone_status)
                    values (%s, %s, %s, %s, 'confirmed')
                    """,
                    (other_user_id, other_email, _hash_password("other123"), f"+1558{other_user_id}"),
                )
                cur.execute(
                    """
                    insert into launches (user_id, tipo, valor, nota, categoria, criado_em)
                    values (%s, 'despesa', 10, 'Remove somente este usuario', 'teste', now()),
                           (%s, 'receita', 99, 'Mantem outro usuario', 'teste', now())
                    """,
                    (user_id, other_user_id),
                )
            conn.commit()

        token = dashboard.make_dashboard_token(user_id, hours=1)
        client = TestClient(dashboard.app)
        client.cookies.set("dashboard_token", token)

        wrong_scope = client.request("DELETE", "/auth/account", headers=_csrf_headers(client), json={"password": "other123"})
        assert wrong_scope.status_code == 401

        response = client.request("DELETE", "/auth/account", headers=_csrf_headers(client), json={"password": "secret123"})
        assert response.status_code == 200
        assert response.json()["user_id"] == user_id

        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("select deletion_status from auth_accounts where user_id=%s", (user_id,))
                assert cur.fetchone()["deletion_status"] == "scheduled"
                cur.execute("select deletion_status from auth_accounts where user_id=%s", (other_user_id,))
                assert cur.fetchone()["deletion_status"] is None

                cur.execute(
                    "update auth_accounts set deletion_scheduled_for = now() - interval '1 minute' where user_id=%s",
                    (user_id,),
                )
            conn.commit()

        results = db.process_due_account_deletions(limit=10)
        assert any(r["user_id"] == user_id and r["deleted"] for r in results)
        assert any(r["user_id"] == user_id and r["email"] == email for r in results)

        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("select 1 from users where id=%s", (user_id,))
                assert cur.fetchone() is None
                cur.execute("select 1 from accounts where user_id=%s", (user_id,))
                assert cur.fetchone() is None
                cur.execute("select 1 from users where id=%s", (other_user_id,))
                assert cur.fetchone() is not None
                cur.execute("select 1 from accounts where user_id=%s", (other_user_id,))
                assert cur.fetchone() is not None
                cur.execute("select nota from launches where user_id=%s", (other_user_id,))
                assert cur.fetchone()["nota"] == "Mantem outro usuario"
    finally:
        db.delete_user_data(other_user_id)


def test_account_deletion_job_processes_due_accounts(user_id, monkeypatch):
    import core.services.email_service as email_service
    from scripts.account_deletion_job import run as run_account_deletion_job

    email = f"job-delete-{user_id}@example.com"
    sent_completed_emails = []
    monkeypatch.setattr(
        email_service,
        "send_account_deletion_completed_email",
        lambda to: sent_completed_emails.append(to) or True,
    )

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into auth_accounts (
                  user_id, email, password_hash, phone_e164, phone_status,
                  deletion_status, deletion_requested_at, deletion_scheduled_for
                )
                values (%s, %s, %s, %s, 'confirmed', 'scheduled', now() - interval '8 days', now() - interval '1 minute')
                """,
                (user_id, email, _hash_password("secret123"), f"+1559{user_id}"),
            )
            cur.execute(
                """
                insert into launches (user_id, tipo, valor, nota, categoria, criado_em)
                values (%s, 'despesa', 25, 'Job remove este usuario', 'teste', now())
                """,
                (user_id,),
            )
        conn.commit()

    assert run_account_deletion_job(limit=5) == 0
    assert sent_completed_emails == [email]

    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select 1 from users where id=%s", (user_id,))
        assert cur.fetchone() is None
        cur.execute("select 1 from accounts where user_id=%s", (user_id,))
        assert cur.fetchone() is None
        cur.execute("select 1 from launches where user_id=%s", (user_id,))
        assert cur.fetchone() is None


def test_dashboard_html_is_not_cached():
    response = TestClient(dashboard.app).get("/app")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"


def test_health_endpoint_does_not_expose_infrastructure():
    response = TestClient(dashboard.app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_sitemap_lists_public_pages_only():
    response = TestClient(dashboard.app).get("/sitemap.xml")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    assert "<loc>https://pigbankai.com</loc>" in response.text
    assert "<loc>https://pigbankai.com/whatsapp</loc>" in response.text
    assert "<loc>https://pigbankai.com/funcionalidades</loc>" in response.text
    assert "<loc>https://pigbankai.com/como-funciona</loc>" in response.text
    assert "<loc>https://pigbankai.com/precos</loc>" in response.text
    assert "<loc>https://pigbankai.com/suporte</loc>" in response.text
    assert "<loc>https://pigbankai.com/privacy</loc>" in response.text
    assert "<loc>https://pigbankai.com/changelog</loc>" in response.text
    assert "/app" not in response.text
    assert "/settings" not in response.text


def test_public_seo_pages_are_served():
    client = TestClient(dashboard.app)

    for path in ["/whatsapp", "/funcionalidades", "/como-funciona", "/precos", "/suporte"]:
        response = client.get(path)

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert 'rel="canonical"' in response.text


def test_robots_points_to_sitemap_and_blocks_private_paths():
    response = TestClient(dashboard.app).get("/robots.txt")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "Allow: /" in response.text
    assert "Disallow: /app" in response.text
    assert "Disallow: /auth/" in response.text
    assert "Sitemap: https://pigbankai.com/sitemap.xml" in response.text


def test_rate_limit_returns_friendly_error(monkeypatch):
    monkeypatch.setattr(db, "create_password_reset_token", lambda email: None)
    client = TestClient(dashboard.app)
    email = f"reset-{uuid.uuid4().hex}@example.com"
    _clear_rate_limits("ip:testclient", f"email:{email}")

    try:
        for _ in range(3):
            response = client.post("/auth/forgot-password", headers=_csrf_headers(client), json={"email": email})
            assert response.status_code == 200

        response = client.post("/auth/forgot-password", headers=_csrf_headers(client), json={"email": email})

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
