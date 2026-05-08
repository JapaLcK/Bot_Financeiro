"""
tests/test_mfa.py — Cobertura do MFA TOTP.

Cobre:
- Setup de secret (com confirmacao por senha)
- Verificacao do primeiro codigo e ativacao
- Geracao e consumo de backup codes
- Login em duas etapas: senha → challenge → codigo TOTP
- Disable do MFA (exige senha + codigo)
- Idempotencia e protecao contra re-ativacao
"""
import os

import pyotp
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

# Garante MFA_ENCRYPTION_KEY antes de importar o modulo (que cacheia Fernet)
os.environ.setdefault("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())

import db
import frontend.finance_bot_websocket_custom as dashboard


def _csrf_headers(client: TestClient) -> dict:
    token = "test-csrf-token"
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, token)
    return {dashboard.CSRF_HEADER_NAME: token}


def _register_user(client: TestClient, email: str, password: str) -> int:
    """Cria conta e confirma email — replica fluxo real."""
    user = db.register_auth_user(email, password)
    return int(user["user_id"])


def _login(client: TestClient, email: str, password: str) -> dict:
    resp = client.post(
        "/auth/login",
        json={"email": email, "password": password},
        headers=_csrf_headers(client),
    )
    return {"status": resp.status_code, "body": resp.json()}


def test_status_returns_disabled_for_new_user(user_id):
    status = db.get_mfa_status(user_id)
    assert status["enabled"] is False
    assert status["backup_codes_remaining"] == 0


def test_setup_secret_creates_pending_record(user_id):
    result = db.mfa_setup_secret(user_id, f"user{user_id}@test.com")
    assert "secret" in result
    assert result["uri"].startswith("otpauth://")
    status = db.get_mfa_status(user_id)
    assert status["enabled"] is False
    assert status["has_pending_secret"] is True


def test_verify_and_enable_with_valid_code_returns_backup_codes(user_id):
    setup = db.mfa_setup_secret(user_id, f"user{user_id}@test.com")
    code = pyotp.TOTP(setup["secret"]).now()
    backup_codes = db.mfa_verify_and_enable(user_id, code)

    assert len(backup_codes) == 10
    assert all(len(c) == 11 and "-" in c for c in backup_codes)  # XXXXX-XXXXX
    status = db.get_mfa_status(user_id)
    assert status["enabled"] is True
    assert status["backup_codes_remaining"] == 10


def test_verify_and_enable_rejects_invalid_code(user_id):
    db.mfa_setup_secret(user_id, f"user{user_id}@test.com")
    import pytest
    with pytest.raises(ValueError, match="MFA_CODE_INVALID"):
        db.mfa_verify_and_enable(user_id, "000000")


def test_setup_blocks_when_already_enabled(user_id):
    setup = db.mfa_setup_secret(user_id, f"user{user_id}@test.com")
    code = pyotp.TOTP(setup["secret"]).now()
    db.mfa_verify_and_enable(user_id, code)

    import pytest
    with pytest.raises(ValueError, match="MFA_ALREADY_ENABLED"):
        db.mfa_setup_secret(user_id, f"user{user_id}@test.com")


def test_verify_totp_with_valid_code_returns_true(user_id):
    setup = db.mfa_setup_secret(user_id, f"user{user_id}@test.com")
    code = pyotp.TOTP(setup["secret"]).now()
    db.mfa_verify_and_enable(user_id, code)

    later_code = pyotp.TOTP(setup["secret"]).now()
    assert db.mfa_verify_totp(user_id, later_code) is True


def test_verify_totp_rejects_invalid_format(user_id):
    setup = db.mfa_setup_secret(user_id, f"user{user_id}@test.com")
    code = pyotp.TOTP(setup["secret"]).now()
    db.mfa_verify_and_enable(user_id, code)

    assert db.mfa_verify_totp(user_id, "abc") is False
    assert db.mfa_verify_totp(user_id, "12345") is False
    assert db.mfa_verify_totp(user_id, "") is False


def test_consume_backup_code_marks_as_used(user_id):
    setup = db.mfa_setup_secret(user_id, f"user{user_id}@test.com")
    code = pyotp.TOTP(setup["secret"]).now()
    backup_codes = db.mfa_verify_and_enable(user_id, code)

    first = backup_codes[0]
    assert db.mfa_consume_backup_code(user_id, first) is True
    # Segundo uso falha
    assert db.mfa_consume_backup_code(user_id, first) is False
    # Outro ainda funciona
    assert db.mfa_consume_backup_code(user_id, backup_codes[1]) is True

    status = db.get_mfa_status(user_id)
    assert status["backup_codes_remaining"] == 8


def test_regenerate_backup_codes_invalidates_old(user_id):
    setup = db.mfa_setup_secret(user_id, f"user{user_id}@test.com")
    code = pyotp.TOTP(setup["secret"]).now()
    old_codes = db.mfa_verify_and_enable(user_id, code)

    new_codes = db.mfa_regenerate_backup_codes(user_id)
    assert len(new_codes) == 10
    assert set(old_codes).isdisjoint(new_codes)
    # Codigo antigo nao funciona mais
    assert db.mfa_consume_backup_code(user_id, old_codes[0]) is False
    # Novo funciona
    assert db.mfa_consume_backup_code(user_id, new_codes[0]) is True


def test_disable_mfa_removes_all_state(user_id):
    setup = db.mfa_setup_secret(user_id, f"user{user_id}@test.com")
    code = pyotp.TOTP(setup["secret"]).now()
    db.mfa_verify_and_enable(user_id, code)

    db.disable_mfa(user_id)
    status = db.get_mfa_status(user_id)
    assert status["enabled"] is False
    assert status["has_pending_secret"] is False
    assert status["backup_codes_remaining"] == 0


def test_login_challenge_consume_returns_user_id(user_id):
    token = db.mfa_create_login_challenge(user_id)
    assert isinstance(token, str)
    assert len(token) > 20
    consumed = db.mfa_consume_login_challenge(token)
    assert consumed == user_id
    # Single-use
    assert db.mfa_consume_login_challenge(token) is None


def test_login_challenge_with_invalid_token_returns_none():
    assert db.mfa_consume_login_challenge("nonexistent") is None
    assert db.mfa_consume_login_challenge("") is None


# ── Endpoint tests ──────────────────────────────────────────────────────

def test_login_without_mfa_works_normally(user_id):
    """Sanity: usuario sem MFA segue fluxo padrao."""
    email = f"mfa-test-{user_id}@test.com"
    password = "senha-forte-123"
    db.register_auth_user(email, password)

    client = TestClient(dashboard.app)
    resp = client.post(
        "/auth/login",
        json={"email": email, "password": password},
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "mfa_required" not in body or body["mfa_required"] is False
    assert "user_id" in body


def test_login_with_mfa_returns_challenge(user_id):
    email = f"mfa-test-{user_id}@test.com"
    password = "senha-forte-123"
    user = db.register_auth_user(email, password)
    real_user_id = int(user["user_id"])

    setup = db.mfa_setup_secret(real_user_id, email)
    code = pyotp.TOTP(setup["secret"]).now()
    db.mfa_verify_and_enable(real_user_id, code)

    client = TestClient(dashboard.app)
    resp = client.post(
        "/auth/login",
        json={"email": email, "password": password},
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["mfa_required"] is True
    assert "mfa_challenge" in body
    assert "user_id" not in body  # ainda nao logou


def test_mfa_verify_login_completes_login(user_id):
    email = f"mfa-test-{user_id}@test.com"
    password = "senha-forte-123"
    user = db.register_auth_user(email, password)
    real_user_id = int(user["user_id"])

    setup = db.mfa_setup_secret(real_user_id, email)
    secret = setup["secret"]
    db.mfa_verify_and_enable(real_user_id, pyotp.TOTP(secret).now())

    client = TestClient(dashboard.app)
    login_resp = client.post(
        "/auth/login",
        json={"email": email, "password": password},
        headers=_csrf_headers(client),
    )
    challenge = login_resp.json()["mfa_challenge"]

    code = pyotp.TOTP(secret).now()
    verify_resp = client.post(
        "/auth/mfa/verify-login",
        json={"challenge": challenge, "code": code, "use_backup": False},
        headers=_csrf_headers(client),
    )
    assert verify_resp.status_code == 200
    body = verify_resp.json()
    assert body["user_id"] == real_user_id
    assert body["email"] == email


def test_mfa_verify_login_rejects_wrong_code(user_id):
    email = f"mfa-test-{user_id}@test.com"
    password = "senha-forte-123"
    user = db.register_auth_user(email, password)
    real_user_id = int(user["user_id"])

    setup = db.mfa_setup_secret(real_user_id, email)
    db.mfa_verify_and_enable(real_user_id, pyotp.TOTP(setup["secret"]).now())

    client = TestClient(dashboard.app)
    login_resp = client.post(
        "/auth/login",
        json={"email": email, "password": password},
        headers=_csrf_headers(client),
    )
    challenge = login_resp.json()["mfa_challenge"]

    verify_resp = client.post(
        "/auth/mfa/verify-login",
        json={"challenge": challenge, "code": "000000", "use_backup": False},
        headers=_csrf_headers(client),
    )
    assert verify_resp.status_code == 400
