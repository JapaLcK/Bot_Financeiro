"""
tests/test_pockets_endpoints.py — Endpoints de movimentacao em caixinhas.

Cobre:
- POST /pockets/{user}/{name}/deposit (sucesso, valores invalidos, saldo insuficiente)
- POST /pockets/{user}/{name}/withdraw (sucesso, saldo insuficiente)
- 404 para caixinha inexistente
"""
from __future__ import annotations

import os
from datetime import date, timedelta
from decimal import Decimal

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

os.environ.setdefault("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())

import db
import frontend.finance_bot_websocket_custom as dashboard


def _auth(client: TestClient, user_id: int, email: str = "pkt@t.com") -> None:
    """Injeta cookies para passar por _authorize_dashboard_access."""
    client.cookies.set(dashboard.AUTH_COOKIE_NAME, dashboard._make_jwt(user_id, email))
    client.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, dashboard.make_dashboard_token(user_id, hours=1))


def _csrf_headers(client: TestClient) -> dict:
    token = "test-csrf-token"
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, token)
    return {dashboard.CSRF_HEADER_NAME: token, "Content-Type": "application/json"}


def _seed(user_id: int, account_balance: float = 1000.0, pocket_name: str = "viagem") -> None:
    """Cria conta com saldo + caixinha vazia."""
    db.add_launch_and_update_balance(user_id, "receita", account_balance, None, "seed")
    db.create_pocket(user_id, pocket_name)


def test_deposit_endpoint_moves_balance(user_id):
    _seed(user_id)
    client = TestClient(dashboard.app)
    _auth(client, user_id)

    resp = client.post(
        f"/pockets/{user_id}/viagem/deposit",
        json={"amount": 250, "nota": "primeiro aporte"},
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert float(body["account_balance"]) == 750.0
    assert float(body["pocket_balance"]) == 250.0


def test_deposit_endpoint_rejects_zero_amount(user_id):
    _seed(user_id)
    client = TestClient(dashboard.app)
    _auth(client, user_id)

    resp = client.post(
        f"/pockets/{user_id}/viagem/deposit",
        json={"amount": 0},
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 400
    assert "maior que zero" in resp.json()["detail"]


def test_deposit_endpoint_rejects_when_account_insufficient(user_id):
    _seed(user_id, account_balance=100.0)
    client = TestClient(dashboard.app)
    _auth(client, user_id)

    resp = client.post(
        f"/pockets/{user_id}/viagem/deposit",
        json={"amount": 500},
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 400
    assert "conta principal" in resp.json()["detail"]


def test_withdraw_endpoint_moves_balance(user_id):
    _seed(user_id)
    db.pocket_deposit_from_account(user_id, "viagem", 400, "preparo")
    client = TestClient(dashboard.app)
    _auth(client, user_id)

    resp = client.post(
        f"/pockets/{user_id}/viagem/withdraw",
        json={"amount": 150, "nota": "comprei passagem"},
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert float(body["pocket_balance"]) == 250.0
    assert float(body["account_balance"]) == 750.0  # 600 (apos deposit) + 150


def test_withdraw_endpoint_rejects_when_pocket_insufficient(user_id):
    _seed(user_id)
    db.pocket_deposit_from_account(user_id, "viagem", 50, "")
    client = TestClient(dashboard.app)
    _auth(client, user_id)

    resp = client.post(
        f"/pockets/{user_id}/viagem/withdraw",
        json={"amount": 200},
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 400
    assert "caixinha" in resp.json()["detail"].lower()


def test_deposit_endpoint_returns_404_for_unknown_pocket(user_id):
    db.add_launch_and_update_balance(user_id, "receita", 500, None, "seed")
    client = TestClient(dashboard.app)
    _auth(client, user_id)

    resp = client.post(
        f"/pockets/{user_id}/inexistente/deposit",
        json={"amount": 100},
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 404


def test_create_pocket_endpoint_accepts_interest_toggle(user_id):
    client = TestClient(dashboard.app)
    _auth(client, user_id)

    resp = client.post(
        f"/pockets/{user_id}",
        json={"name": "reserva", "interest_enabled": False, "interest_rate": 1.0},
        headers=_csrf_headers(client),
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["pocket"]["interest_enabled"] is False
    assert body["pocket"]["interest_rate"] == 1.0

    row = db.list_pockets(user_id, accrue=False)[0]
    assert row["interest_enabled"] is False
    assert Decimal(str(row["interest_rate"])) == Decimal("1.0")


def test_pocket_meta_endpoint_updates_cdi_percent(user_id):
    client = TestClient(dashboard.app)
    _auth(client, user_id)
    db.create_pocket(user_id, "reserva")
    pocket = db.list_pockets(user_id, accrue=False)[0]

    resp = client.patch(
        f"/pockets/{user_id}/{pocket['id']}/meta",
        json={"interest_enabled": True, "interest_rate": 1.15},
        headers=_csrf_headers(client),
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["pocket"]["interest_enabled"] is True
    assert body["pocket"]["interest_rate"] == 1.15

    row = db.list_pockets(user_id, accrue=False)[0]
    assert row["interest_enabled"] is True
    assert Decimal(str(row["interest_rate"])) == Decimal("1.15")


def test_pocket_accrues_at_default_100_percent_cdi(user_id):
    _seed(user_id)
    db.pocket_deposit_from_account(user_id, "viagem", 1000, "aporte")

    pocket = db.list_pockets(user_id, accrue=False)[0]
    start = date(2026, 4, 14)
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                update pocket_lots
                   set opened_at=%s, last_date=%s
                 where user_id=%s and pocket_id=%s
                """,
                (start, start, user_id, pocket["id"]),
            )
            cur.execute(
                "update pockets set last_interest_date=%s where user_id=%s and id=%s",
                (start, user_id, pocket["id"]),
            )
        conn.commit()

    original_fetch = db._get_cdi_daily_map
    db._get_cdi_daily_map = lambda _cur, _start, _end: {
        date(2026, 4, 15): 0.05,
        date(2026, 4, 16): 0.06,
    }
    try:
        rows = db.accrue_all_pockets(user_id, today=date(2026, 4, 17))
    finally:
        db._get_cdi_daily_map = original_fetch

    expected = Decimal(str(1000 * (1 + 0.05 / 100) * (1 + 0.06 / 100)))
    assert abs(Decimal(str(rows[0]["balance"])) - expected) < Decimal("0.000001")


def test_pocket_accrues_using_configured_cdi_percent(user_id):
    _seed(user_id)
    client = TestClient(dashboard.app)
    _auth(client, user_id)
    pocket = db.list_pockets(user_id, accrue=False)[0]
    resp = client.patch(
        f"/pockets/{user_id}/{pocket['id']}/meta",
        json={"interest_enabled": True, "interest_rate": 1.15},
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 200, resp.text
    db.pocket_deposit_from_account(user_id, "viagem", 1000, "aporte")

    start = date(2026, 4, 14)
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                update pocket_lots
                   set opened_at=%s, last_date=%s
                 where user_id=%s and pocket_id=%s
                """,
                (start, start, user_id, pocket["id"]),
            )
            cur.execute(
                "update pockets set last_interest_date=%s where user_id=%s and id=%s",
                (start, user_id, pocket["id"]),
            )
        conn.commit()

    original_fetch = db._get_cdi_daily_map
    db._get_cdi_daily_map = lambda _cur, _start, _end: {
        date(2026, 4, 15): 0.05,
        date(2026, 4, 16): 0.06,
    }
    try:
        rows = db.accrue_all_pockets(user_id, today=date(2026, 4, 17))
    finally:
        db._get_cdi_daily_map = original_fetch

    expected = Decimal(str(1000 * (1 + 0.05 / 100 * 1.15) * (1 + 0.06 / 100 * 1.15)))
    assert abs(Decimal(str(rows[0]["balance"])) - expected) < Decimal("0.000001")


def test_pocket_withdraw_applies_ir_iof_on_gain(user_id):
    _seed(user_id)
    db.pocket_deposit_from_account(user_id, "viagem", 1000, "aporte")
    pocket = db.list_pockets(user_id, accrue=False)[0]
    opened_at = date.today() - timedelta(days=10)

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                update pocket_lots
                   set balance=%s, principal_remaining=%s, opened_at=%s, last_date=%s
                 where user_id=%s and pocket_id=%s
                """,
                (Decimal("1100"), Decimal("1000"), opened_at, date.today(), user_id, pocket["id"]),
            )
            cur.execute(
                "update pockets set balance=%s, last_interest_date=%s where user_id=%s and id=%s",
                (Decimal("1100"), date.today(), user_id, pocket["id"]),
            )
        conn.commit()

    launch_id, new_acc, new_pocket, canon, taxes = db.pocket_withdraw_to_account(
        user_id, "viagem", 1100, "resgate"
    )

    assert launch_id
    assert canon == "viagem"
    assert Decimal(str(new_pocket)) == Decimal("0")
    assert Decimal(str(taxes["iof"])) == Decimal("66.0")
    assert Decimal(str(taxes["ir"])) == Decimal("7.65")
    assert Decimal(str(taxes["net"])) == Decimal("1026.35")
    assert Decimal(str(new_acc)) == Decimal("1026.35")
