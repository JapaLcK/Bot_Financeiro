"""
tests/test_credit_transactions_endpoints.py — endpoints de edição/exclusão
de compras no cartão de crédito (PATCH/DELETE /credit-transactions/...).

Cobre:
  - PATCH atualiza categoria e nota
  - PATCH 404 quando tx não existe
  - PATCH 400 quando body vazio
  - DELETE remove compra à vista
  - DELETE em compra parcelada desfaz o grupo inteiro
  - DELETE 404 quando tx não existe
"""
from __future__ import annotations

import os
from datetime import date

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

os.environ.setdefault("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())

import db
import frontend.finance_bot_websocket_custom as dashboard


def _auth(client: TestClient, user_id: int, email: str = "credit@t.com") -> None:
    client.cookies.set(dashboard.AUTH_COOKIE_NAME, dashboard._make_jwt(user_id, email))
    client.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, dashboard.make_dashboard_token(user_id, hours=1))


def _csrf_headers(client: TestClient) -> dict:
    token = "test-csrf-token"
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, token)
    return {dashboard.CSRF_HEADER_NAME: token, "Content-Type": "application/json"}


def _seed_card_and_purchase(user_id: int, valor: float = 100.0, categoria: str = "outros", nota: str = "compra teste"):
    """Cria cartão + 1 compra à vista. Retorna (card_id, tx_id)."""
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.set_default_card(user_id, card_id)
    tx_id, _due, _bill_id = db.add_credit_purchase(
        user_id=user_id,
        card_id=card_id,
        valor=valor,
        categoria=categoria,
        nota=nota,
        purchased_at=date.today(),
    )
    return card_id, tx_id


def _get_tx_row(user_id: int, tx_id: int) -> dict | None:
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id, categoria, nota, valor from credit_transactions where user_id=%s and id=%s",
                (user_id, tx_id),
            )
            return cur.fetchone()


# ─── PATCH ──────────────────────────────────────────────────────────────────

def test_patch_atualiza_categoria_e_nota(user_id):
    _card_id, tx_id = _seed_card_and_purchase(user_id, categoria="outros", nota="x")
    client = TestClient(dashboard.app)
    _auth(client, user_id)

    resp = client.patch(
        f"/credit-transactions/{user_id}/{tx_id}",
        json={"categoria": "alimentação", "nota": "mercado"},
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["tx_id"] == tx_id

    row = _get_tx_row(user_id, tx_id)
    assert row["categoria"] == "alimentação"
    assert row["nota"] == "mercado"


def test_patch_so_categoria(user_id):
    _card_id, tx_id = _seed_card_and_purchase(user_id, categoria="outros", nota="original")
    client = TestClient(dashboard.app)
    _auth(client, user_id)

    resp = client.patch(
        f"/credit-transactions/{user_id}/{tx_id}",
        json={"categoria": "lazer"},
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 200, resp.text
    row = _get_tx_row(user_id, tx_id)
    assert row["categoria"] == "lazer"
    assert row["nota"] == "original"  # nota intacta


def test_patch_body_vazio_400(user_id):
    _card_id, tx_id = _seed_card_and_purchase(user_id)
    client = TestClient(dashboard.app)
    _auth(client, user_id)

    resp = client.patch(
        f"/credit-transactions/{user_id}/{tx_id}",
        json={},
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 400


def test_patch_tx_inexistente_404(user_id):
    _seed_card_and_purchase(user_id)  # cria cartão mas tx_id 99999999 não existe
    client = TestClient(dashboard.app)
    _auth(client, user_id)

    resp = client.patch(
        f"/credit-transactions/{user_id}/99999999",
        json={"categoria": "lazer"},
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 404


# ─── DELETE ─────────────────────────────────────────────────────────────────

def test_delete_compra_a_vista(user_id):
    _card_id, tx_id = _seed_card_and_purchase(user_id, valor=44.90)
    client = TestClient(dashboard.app)
    _auth(client, user_id)

    resp = client.delete(
        f"/credit-transactions/{user_id}/{tx_id}",
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["mode"] == "single"
    assert body["removed_count"] == 1
    assert body["removed_total"] == 44.90

    assert _get_tx_row(user_id, tx_id) is None


def test_delete_compra_parcelada_desfaz_grupo(user_id):
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.set_default_card(user_id, card_id)
    # 3x R$ 90 (= R$ 270)
    result = db.add_credit_purchase_installments(
        user_id=user_id,
        card_id=card_id,
        valor_total=270.0,
        categoria="outros",
        nota="celular",
        purchased_at=date.today(),
        installments=3,
    )
    # add_credit_purchase_installments retorna ({"group_id":..., "tx_ids":[...]}, total)
    info = result[0] if isinstance(result, tuple) else result
    tx_ids = info["tx_ids"]
    assert len(tx_ids) == 3

    client = TestClient(dashboard.app)
    _auth(client, user_id)

    # Apaga a primeira parcela → o grupo inteiro deve sumir
    resp = client.delete(
        f"/credit-transactions/{user_id}/{tx_ids[0]}",
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "group"
    assert body["removed_count"] == 3

    # Todas as 3 transações foram removidas
    for tx in tx_ids:
        assert _get_tx_row(user_id, tx) is None


def test_delete_tx_inexistente_404(user_id):
    _seed_card_and_purchase(user_id)
    client = TestClient(dashboard.app)
    _auth(client, user_id)

    resp = client.delete(
        f"/credit-transactions/{user_id}/99999999",
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 404
