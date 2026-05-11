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


def test_patch_em_parcelamento_propaga_para_o_grupo(user_id):
    """Editar categoria de uma parcela atualiza TODAS as parcelas do grupo."""
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.set_default_card(user_id, card_id)
    result = db.add_credit_purchase_installments(
        user_id=user_id,
        card_id=card_id,
        valor_total=300.0,
        categoria="outros",
        nota="celular",
        purchased_at=date.today(),
        installments=3,
    )
    info = result[0] if isinstance(result, tuple) else result
    tx_ids = info["tx_ids"]

    client = TestClient(dashboard.app)
    _auth(client, user_id)

    # Edita só a primeira parcela
    resp = client.patch(
        f"/credit-transactions/{user_id}/{tx_ids[0]}",
        json={"categoria": "compras online"},
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 200, resp.text

    # As outras 2 parcelas também devem ter mudado
    for tx in tx_ids:
        row = _get_tx_row(user_id, tx)
        assert row["categoria"] == "compras online", f"tx {tx} não propagou"


def test_patch_em_tx_single_nao_afeta_outras(user_id):
    """Edit em compra à vista não toca em outras compras (sem group_id)."""
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.set_default_card(user_id, card_id)
    tx1, _, _ = db.add_credit_purchase(user_id, card_id, 50, "outros", "compra A", date.today())
    tx2, _, _ = db.add_credit_purchase(user_id, card_id, 80, "outros", "compra B", date.today())

    client = TestClient(dashboard.app)
    _auth(client, user_id)

    resp = client.patch(
        f"/credit-transactions/{user_id}/{tx1}",
        json={"categoria": "alimentação"},
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 200

    assert _get_tx_row(user_id, tx1)["categoria"] == "alimentação"
    assert _get_tx_row(user_id, tx2)["categoria"] == "outros"  # intacta


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


def test_delete_parcelamento_e_recriar_reabre_bills_zumbis(user_id):
    """Regression: apagar parcelamento esvaziava bills (total=0), mas elas
    ficavam status='paid' (zumbis). Recriar parcelamento no mesmo período
    reusava o id da bill zumbi e a fatura sumia do dashboard porque
    `list_open_bills` filtra por status='open'.

    Fix em duas frentes:
      - `undo_credit_transaction` só fecha como paid se paid > 0
      - `get_or_create_bill_by_period` reabre paid/closed sem pagamento real
    """
    card_id = db.create_card(user_id, "Nubank", closing_day=1, due_day=10)
    db.set_default_card(user_id, card_id)

    # Cria parcelamento — 3 bills 'open'
    r1 = db.add_credit_purchase_installments(
        user_id=user_id, card_id=card_id, valor_total=150,
        categoria="outros", nota="primeiro", purchased_at=date.today(), installments=3,
    )
    info1 = r1[0] if isinstance(r1, tuple) else r1
    tx_ids_1 = info1["tx_ids"]

    # Apaga o grupo via endpoint (mesmo caminho do dashboard)
    client = TestClient(dashboard.app)
    _auth(client, user_id)
    resp = client.delete(f"/credit-transactions/{user_id}/{tx_ids_1[0]}", headers=_csrf_headers(client))
    assert resp.status_code == 200, resp.text

    # Agora cria outro parcelamento NO MESMO PERÍODO
    r2 = db.add_credit_purchase_installments(
        user_id=user_id, card_id=card_id, valor_total=300,
        categoria="outros", nota="segundo", purchased_at=date.today(), installments=3,
    )
    info2 = r2[0] if isinstance(r2, tuple) else r2
    assert len(info2["tx_ids"]) == 3

    # Todas as 3 bills devem aparecer em list_open_bills (não viraram zumbi)
    open_bills = db.list_open_bills(user_id)
    bills_card = [b for b in open_bills if b["card_id"] == card_id]
    assert len(bills_card) == 3, f"esperava 3 bills abertas, achei {len(bills_card)}: {bills_card}"
    # E o total deve ser R$ 300 (o novo parcelamento), não R$ 150 + R$ 300
    soma_totais = sum(float(b["total"]) for b in bills_card)
    assert soma_totais == 300.0, f"esperava R$ 300 total, achei R$ {soma_totais}"


def test_delete_tx_inexistente_404(user_id):
    _seed_card_and_purchase(user_id)
    client = TestClient(dashboard.app)
    _auth(client, user_id)

    resp = client.delete(
        f"/credit-transactions/{user_id}/99999999",
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 404
