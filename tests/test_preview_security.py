"""
tests/test_preview_security.py

Cobre o endpoint /preview/login e os bloqueios de ações sensíveis quando
o JWT carrega claim is_preview=true.
"""
from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard
import db


def _client_with_preview_key(monkeypatch, key: str = "test-preview-key"):
    monkeypatch.setattr(dashboard, "PREVIEW_KEY", key)
    return TestClient(dashboard.app)


def test_preview_login_disabled_when_key_unset(monkeypatch):
    monkeypatch.setattr(dashboard, "PREVIEW_KEY", "")
    resp = TestClient(dashboard.app).post("/preview/login", json={"key": "anything"})
    assert resp.status_code == 503


def test_preview_login_rejects_wrong_key(monkeypatch):
    client = _client_with_preview_key(monkeypatch)
    resp = client.post("/preview/login", json={"key": "wrong"})
    assert resp.status_code == 401


def test_preview_login_rejects_missing_key(monkeypatch):
    client = _client_with_preview_key(monkeypatch)
    resp = client.post("/preview/login", json={})
    assert resp.status_code == 401


def test_preview_login_accepts_correct_key_and_returns_jwt(monkeypatch):
    client = _client_with_preview_key(monkeypatch, key="ok-key")
    resp = client.post("/preview/login", json={"key": "ok-key"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["is_preview"] is True
    assert body["user_id"] == db.PREVIEW_USER_ID
    assert body["token"]
    # Decodifica o JWT e confirma o claim is_preview
    payload = dashboard._decode_jwt(body["token"])
    assert payload is not None
    assert payload.get("is_preview") is True
    assert int(payload["sub"]) == db.PREVIEW_USER_ID


def test_preview_login_accepts_key_via_header(monkeypatch):
    client = _client_with_preview_key(monkeypatch, key="hdr-key")
    resp = client.post("/preview/login", headers={"X-Preview-Key": "hdr-key"})
    assert resp.status_code == 200


def _preview_auth_headers(login_body: dict) -> dict[str, str]:
    """Constroi headers usando o Authorization Bearer + CSRF do login.

    No TestClient HTTP, cookies marcados Secure nao sao enviados de volta;
    por isso enviamos o token via Authorization e o CSRF via header dedicado.
    """
    return {
        "Authorization": f"Bearer {login_body['token']}",
        dashboard.CSRF_HEADER_NAME: login_body["csrf_token"],
        "Cookie": f"{dashboard.CSRF_COOKIE_NAME}={login_body['csrf_token']}",
    }


def test_preview_jwt_blocks_account_deletion(monkeypatch):
    """JWT de preview nao pode disparar exclusao de conta."""
    client = _client_with_preview_key(monkeypatch, key="del-key")
    login = client.post("/preview/login", json={"key": "del-key"})
    assert login.status_code == 200
    login_body = login.json()

    resp = client.request(
        "DELETE",
        "/auth/account",
        json={"password": "irrelevant"},
        headers=_preview_auth_headers(login_body),
    )
    assert resp.status_code == 403
    assert "preview" in resp.json().get("detail", "").lower()


def test_preview_jwt_blocks_billing_checkout(monkeypatch):
    """JWT de preview nao pode criar checkout do Stripe."""
    client = _client_with_preview_key(monkeypatch, key="bill-key")
    login = client.post("/preview/login", json={"key": "bill-key"})
    assert login.status_code == 200
    login_body = login.json()

    resp = client.post(
        "/billing/create-checkout",
        headers=_preview_auth_headers(login_body),
    )
    assert resp.status_code == 403


def test_is_preview_user_helper():
    assert db.is_preview_user(db.PREVIEW_USER_ID) is True
    assert db.is_preview_user(123456) is False
    assert db.is_preview_user(None) is False


def test_ensure_preview_user_idempotent():
    """Chamar ensure_preview_user duas vezes nao quebra."""
    db.ensure_preview_user()
    db.ensure_preview_user()
    # Confirma que o user existe
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select id from users where id = %s", (db.PREVIEW_USER_ID,))
            assert cur.fetchone() is not None


def test_reset_preview_seeds_data():
    """Apos reset, user demo tem investimentos, caixinhas e lancamentos."""
    db.reset_preview_user_data()
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select count(*) as n from investments where user_id = %s",
                (db.PREVIEW_USER_ID,),
            )
            assert cur.fetchone()["n"] >= 3

            cur.execute(
                "select count(*) as n from pockets where user_id = %s",
                (db.PREVIEW_USER_ID,),
            )
            assert cur.fetchone()["n"] >= 2

            cur.execute(
                "select count(*) as n from launches where user_id = %s",
                (db.PREVIEW_USER_ID,),
            )
            assert cur.fetchone()["n"] >= 5

            cur.execute(
                "select balance from accounts where user_id = %s",
                (db.PREVIEW_USER_ID,),
            )
            assert cur.fetchone()["balance"] > 0


def test_reset_preview_is_idempotent():
    """Reset duas vezes seguidas nao acumula dados duplicados."""
    db.reset_preview_user_data()
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select count(*) as n from investments where user_id = %s",
                (db.PREVIEW_USER_ID,),
            )
            count_first = cur.fetchone()["n"]

    db.reset_preview_user_data()
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select count(*) as n from investments where user_id = %s",
                (db.PREVIEW_USER_ID,),
            )
            count_second = cur.fetchone()["n"]

    assert count_first == count_second
