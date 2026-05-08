"""
tests/test_audit.py — Cobertura do audit log estruturado (core/audit.py).

Cobre:
- record_audit_event grava ip/UA/details corretamente
- record_audit_event falha silenciosa quando algo quebra
- is_known_login_ip: ignora rows mfa_pending e responde correto
- maybe_record_login_from_new_ip: fire na 1a vez, idempotente depois
- E2E: POST /auth/mfa/enable grava mfa_enabled em audit_events
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pyotp
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

os.environ.setdefault("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())

import db
import frontend.finance_bot_websocket_custom as dashboard
from core.audit import (
    AuditEvent,
    is_known_login_ip,
    maybe_record_login_from_new_ip,
    record_audit_event,
)
from db.connection import get_conn


def _audit_rows(user_id: int) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select event, ip, user_agent, details from audit_events where user_id = %s order by id",
                (user_id,),
            )
            return list(cur.fetchall())


def _login_event(user_id: int, success: bool, ip: str, failure_reason: str | None = None):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into auth_login_events (user_id, email, success, ip_address, user_agent, failure_reason)
                values (%s, %s, %s, %s, %s, %s)
                """,
                (user_id, f"u{user_id}@t.com", success, ip, "pytest/1.0", failure_reason),
            )
        conn.commit()


class _FakeRequest:
    def __init__(self, ip: str = "203.0.113.10", ua: str = "pytest/1.0"):
        self.headers = {"x-forwarded-for": ip, "user-agent": ua}

        class _C:
            host = "127.0.0.1"

        self.client = _C()


def test_record_audit_event_persists_full_payload(user_id):
    record_audit_event(
        user_id,
        AuditEvent.MFA_ENABLED,
        request=_FakeRequest("198.51.100.7"),
        details={"foo": "bar"},
    )
    rows = _audit_rows(user_id)
    assert len(rows) == 1
    assert rows[0]["event"] == "mfa_enabled"
    assert rows[0]["ip"] == "198.51.100.7"
    assert rows[0]["user_agent"] == "pytest/1.0"
    assert rows[0]["details"] == {"foo": "bar"}


def test_record_audit_event_swallows_db_errors(user_id):
    """Falha na escrita nao pode propagar — auditoria nao deve quebrar fluxo."""
    with patch("core.audit.get_conn", side_effect=RuntimeError("simulated outage")):
        record_audit_event(user_id, AuditEvent.MFA_DISABLED)  # nao deve raise
    assert _audit_rows(user_id) == []


def test_is_known_login_ip_filters_mfa_pending(user_id):
    # Sem nenhum registro: IP eh "novo".
    assert is_known_login_ip(user_id, "192.0.2.50") is False

    # Registro mfa_pending NAO conta como conhecido (login incompleto).
    _login_event(user_id, success=True, ip="192.0.2.50", failure_reason="mfa_pending")
    assert is_known_login_ip(user_id, "192.0.2.50") is False

    # Login completo (failure_reason=null) marca o IP como conhecido.
    _login_event(user_id, success=True, ip="192.0.2.50", failure_reason=None)
    assert is_known_login_ip(user_id, "192.0.2.50") is True

    # IP diferente continua novo.
    assert is_known_login_ip(user_id, "192.0.2.99") is False


def test_is_known_login_ip_without_ip_returns_true():
    """Sem IP (proxy mal configurado) o helper prefere nao alarmar."""
    assert is_known_login_ip(123, None) is True
    assert is_known_login_ip(123, "") is True


def test_maybe_record_login_from_new_ip_idempotent(user_id):
    req = _FakeRequest("203.0.113.42")

    # 1a vez: IP desconhecido, dispara o audit.
    maybe_record_login_from_new_ip(user_id, request=req)
    rows = _audit_rows(user_id)
    assert len(rows) == 1
    assert rows[0]["event"] == "login_from_new_ip"
    assert rows[0]["ip"] == "203.0.113.42"

    # Marca o IP como conhecido (simula log_auth_login_event apos o audit).
    _login_event(user_id, success=True, ip="203.0.113.42", failure_reason=None)

    # 2a vez: NAO dispara (IP ja conhecido).
    maybe_record_login_from_new_ip(user_id, request=req)
    assert len(_audit_rows(user_id)) == 1


def _csrf_headers(client: TestClient) -> dict:
    token = "test-csrf-token"
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, token)
    return {dashboard.CSRF_HEADER_NAME: token}


def test_mfa_enable_writes_audit_event(user_id):
    """E2E: ativar MFA via endpoint dispara registro em audit_events.

    Cria JWT direto e injeta no cookie (cookie real eh Secure, nao roundtrip
    em TestClient sobre http://). Mesmo padrao usado em tests/test_auth_cookie.py.
    """
    email = f"audit-{user_id}@t.com"
    user = db.register_auth_user(email, "senha-forte-123")
    real_user_id = int(user["user_id"])

    setup = db.mfa_setup_secret(real_user_id, email)

    client = TestClient(dashboard.app)
    client.cookies.set(dashboard.AUTH_COOKIE_NAME, dashboard._make_jwt(real_user_id, email))

    code = pyotp.TOTP(setup["secret"]).now()
    resp = client.post(
        "/auth/mfa/enable",
        json={"code": code},
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 200, resp.text

    enabled = [r for r in _audit_rows(real_user_id) if r["event"] == "mfa_enabled"]
    assert len(enabled) == 1
