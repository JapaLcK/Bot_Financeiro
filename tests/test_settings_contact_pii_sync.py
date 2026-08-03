"""
tests/test_settings_contact_pii_sync.py — Regressão: PATCH /settings/{uid}/security/contact
deve manter email_hash/phone_hash em sincronia com as colunas em claro.

Bug original: a rota atualizava `email`/`phone_e164` em claro sem recalcular
`email_hash`/`phone_hash`. Como o auto-link do WhatsApp busca a conta por
`phone_hash` e o login por `email_hash`, quem trocava o número pelo site
ficava com o hash apontando pro número antigo e o bot nunca mais reconhecia
a conta ("no_match" eterno, mesmo com o cadastro correto no site).
"""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard
from core.crypto import hash_pii_optional
from db import attempt_whatsapp_phone_link
from db.connection import get_conn
from tests._helpers_pii import insert_auth_account_pii


def _client_for(user_id: int, email: str) -> tuple[TestClient, dict]:
    client = TestClient(dashboard.app)
    client.cookies.set(dashboard.AUTH_COOKIE_NAME, dashboard._make_jwt(user_id, email))
    client.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, dashboard.make_dashboard_token(user_id, hours=1))
    csrf = "test-csrf-token"
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, csrf)
    return client, {dashboard.CSRF_HEADER_NAME: csrf}


def _auth_row(user_id: int) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select email, email_hash, phone_e164, phone_hash, phone_status
              from auth_accounts
             where user_id = %s
            """,
            (user_id,),
        )
        return cur.fetchone()


def test_patch_contact_phone_resyncs_hash_e_bot_reconhece(user_id):
    email = f"pii-sync-{uuid.uuid4().hex[:8]}@test.local"
    old_phone = "5565988887777"
    new_phone = "5565999088732"

    with get_conn() as conn, conn.cursor() as cur:
        insert_auth_account_pii(cur, user_id, email, phone=old_phone, phone_status="confirmed")
        conn.commit()

    client, csrf_headers = _client_for(user_id, email)
    resp = client.patch(
        f"/settings/{user_id}/security/contact",
        json={"phone": "+55 65 99908-8732"},
        headers=csrf_headers,
    )
    assert resp.status_code == 200, resp.text

    row = _auth_row(user_id)
    assert row["phone_e164"] == new_phone
    # O hash tem que acompanhar o número novo — é ele que o auto-link consulta.
    assert row["phone_hash"] == hash_pii_optional(new_phone, kind="phone")
    assert row["phone_status"] == "pending"

    # Fim a fim: o bot (auto-link por phone_hash) reconhece o número novo.
    result = attempt_whatsapp_phone_link(new_phone)
    assert result["status"] in {"linked", "already_linked"}
    assert result["user_id"] == user_id


def test_patch_contact_email_resyncs_hash(user_id):
    email = f"pii-sync-{uuid.uuid4().hex[:8]}@test.local"
    new_email = f"pii-sync-novo-{uuid.uuid4().hex[:8]}@test.local"

    with get_conn() as conn, conn.cursor() as cur:
        insert_auth_account_pii(cur, user_id, email)
        conn.commit()

    client, csrf_headers = _client_for(user_id, email)
    resp = client.patch(
        f"/settings/{user_id}/security/contact",
        json={"email": new_email},
        headers=csrf_headers,
    )
    assert resp.status_code == 200, resp.text

    row = _auth_row(user_id)
    assert row["email"] == new_email
    assert row["email_hash"] == hash_pii_optional(new_email, kind="email")
