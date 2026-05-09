"""
tests/test_pockets_endpoints.py — Endpoints de movimentacao em caixinhas.

Cobre:
- POST /pockets/{user}/{name}/deposit (sucesso, valores invalidos, saldo insuficiente)
- POST /pockets/{user}/{name}/withdraw (sucesso, saldo insuficiente)
- 404 para caixinha inexistente
"""
from __future__ import annotations

import os
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
