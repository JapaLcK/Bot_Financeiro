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


# ─── PATCH /launches (edição de data) ───────────────────────────────────────

def _get_launch_row(user_id: int, launch_id: int) -> dict | None:
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id, categoria, nota, valor, criado_em from launches where user_id=%s and id=%s",
                (user_id, launch_id),
            )
            return cur.fetchone()


def test_patch_launch_edita_data(user_id):
    """PATCH /launches/.../criado_em altera a data de um lançamento normal."""
    from zoneinfo import ZoneInfo
    launch_id, _seq, _bal = db.add_launch_and_update_balance(
        user_id, "despesa", 50.0, None, "lavagem carro", categoria="transporte"
    )
    client = TestClient(dashboard.app)
    _auth(client, user_id)

    # 12:30Z equivale a 09:30 em São Paulo (UTC-3)
    resp = client.patch(
        f"/launches/{user_id}/{launch_id}",
        json={"criado_em": "2026-06-02T12:30:00.000Z"},
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 200, resp.text

    row = _get_launch_row(user_id, launch_id)
    sp = row["criado_em"].astimezone(ZoneInfo("America/Sao_Paulo"))
    assert (sp.year, sp.month, sp.day) == (2026, 6, 2)
    assert (sp.hour, sp.minute) == (9, 30)


def test_patch_launch_data_invalida_400(user_id):
    launch_id, _seq, _bal = db.add_launch_and_update_balance(
        user_id, "despesa", 10.0, None, "cafe", categoria="alimentação"
    )
    client = TestClient(dashboard.app)
    _auth(client, user_id)

    resp = client.patch(
        f"/launches/{user_id}/{launch_id}",
        json={"criado_em": "not-a-date"},
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 400, resp.text


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


def test_list_bills_default_so_abertas_com_saldo(user_id):
    """`GET /bills/{user_id}` (sem params) só retorna bills em aberto com
    saldo > 0 — comportamento histórico usado pelo modal de pagamento."""
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.set_default_card(user_id, card_id)
    db.add_credit_purchase(user_id, card_id, 100, "outros", "compra", date.today())

    client = TestClient(dashboard.app)
    _auth(client, user_id)

    resp = client.get(f"/bills/{user_id}", headers={dashboard.CSRF_HEADER_NAME: ""})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["bills"]) == 1
    assert body["bills"][0]["status"] == "open"


def test_list_bills_card_id_filtra_por_cartao(pro_user_id):
    """`?card_id=N` retorna só as bills daquele cartão. Pro pra criar 2."""
    a_id = db.create_card(pro_user_id, "Nubank", closing_day=10, due_day=17)
    b_id = db.create_card(pro_user_id, "Inter", closing_day=15, due_day=22)
    db.add_credit_purchase(pro_user_id, a_id, 100, "outros", "a", date.today())
    db.add_credit_purchase(pro_user_id, b_id, 200, "outros", "b", date.today())

    client = TestClient(dashboard.app)
    _auth(client, pro_user_id)

    resp = client.get(f"/bills/{pro_user_id}?card_id={a_id}")
    assert resp.status_code == 200
    bills = resp.json()["bills"]
    assert len(bills) == 1
    assert bills[0]["card_id"] == a_id
    assert bills[0]["total"] == 100.0


def test_list_bills_include_closed_traz_paid(user_id):
    """`?include_closed=true` retorna bills pagas/fechadas pra navegação."""
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.set_default_card(user_id, card_id)
    # Cria parcelamento (gera 3 bills) e depois apaga → bills viram open com total=0
    # após o fix. Pra ter uma bill 'paid' real, precisamos pagar uma fatura,
    # então criamos compra, recebemos saldo e pagamos.
    db.add_launch_and_update_balance(user_id, "receita", 500, None, "seed")
    tx_id, _, _ = db.add_credit_purchase(user_id, card_id, 100, "outros", "compra", date.today())

    # Pega o bill_id da compra e paga
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select bill_id from credit_transactions where id=%s", (tx_id,))
            bill_id = cur.fetchone()["bill_id"]
    db.pay_bill_amount(user_id, card_id, "Nubank", 100.0)

    client = TestClient(dashboard.app)
    _auth(client, user_id)

    # Sem include_closed: bill paga não aparece
    resp_default = client.get(f"/bills/{user_id}?card_id={card_id}")
    assert all(b["status"] == "open" for b in resp_default.json()["bills"])

    # Com include_closed: aparece
    resp_full = client.get(f"/bills/{user_id}?card_id={card_id}&include_closed=true")
    statuses = {b["status"] for b in resp_full.json()["bills"]}
    assert "paid" in statuses


def test_post_launches_credito_a_vista(user_id):
    """POST /launches com tipo=credito sem parcelas registra compra à vista."""
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.set_default_card(user_id, card_id)

    client = TestClient(dashboard.app)
    _auth(client, user_id)

    resp = client.post(
        f"/launches/{user_id}",
        json={
            "tipo": "credito",
            "valor": 50,
            "alvo": "mercado",
            "card_id": card_id,
        },
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tipo"] == "credito"
    assert "credit_transaction_id" in body
    assert body.get("mode") != "installments"


def test_post_launches_credito_parcelado_cria_n_transacoes(user_id):
    """POST /launches com tipo=credito + parcelas=3 cria 3 transações."""
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.set_default_card(user_id, card_id)

    client = TestClient(dashboard.app)
    _auth(client, user_id)

    resp = client.post(
        f"/launches/{user_id}",
        json={
            "tipo": "credito",
            "valor": 300,
            "alvo": "celular",
            "card_id": card_id,
            "parcelas": 3,
        },
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "installments"
    assert body["installments_total"] == 3
    assert len(body["tx_ids"]) == 3


def test_post_launches_credito_parcelas_1_eh_a_vista(user_id):
    """`parcelas=1` cai no caminho à vista, não installments."""
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.set_default_card(user_id, card_id)

    client = TestClient(dashboard.app)
    _auth(client, user_id)

    resp = client.post(
        f"/launches/{user_id}",
        json={
            "tipo": "credito",
            "valor": 100,
            "card_id": card_id,
            "parcelas": 1,
        },
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("mode") != "installments"
    assert "credit_transaction_id" in body


def test_post_launches_credito_parcelas_invalidas_400(user_id):
    """parcelas fora do range [1,60] retorna 400."""
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.set_default_card(user_id, card_id)

    client = TestClient(dashboard.app)
    _auth(client, user_id)

    resp = client.post(
        f"/launches/{user_id}",
        json={
            "tipo": "credito",
            "valor": 100,
            "card_id": card_id,
            "parcelas": 100,
        },
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 400


def test_delete_pagamento_fatura_reverte_paid_amount(user_id):
    """Regression: apagar o launch de pagamento_fatura no histórico devolvia
    o saldo mas deixava `paid_amount` da bill intacto (zumbi). Agora o
    rollback usa `efeitos.bill_id` pra reverter o paid_amount E reabrir o
    status caso o pagamento não cubra mais o total."""
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.set_default_card(user_id, card_id)
    db.add_launch_and_update_balance(user_id, "receita", 500, None, "seed")
    tx_id, _due, bill_id = db.add_credit_purchase(
        user_id, card_id, 100, "outros", "compra", date.today(),
    )

    # Paga a fatura inteira
    db.pay_bill_amount(user_id, card_id, "Nubank", 100.0)

    # Confere: bill paga, paid_amount=100, status='paid'
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select paid_amount, status from credit_bills where id = %s",
                (bill_id,),
            )
            row = cur.fetchone()
    assert float(row["paid_amount"]) == 100.0
    assert row["status"] == "paid"

    # Acha o launch de pagamento_fatura criado
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id from launches where user_id=%s and categoria='pagamento_fatura' "
                "order by criado_em desc limit 1",
                (user_id,),
            )
            launch_id = cur.fetchone()["id"]

    # Apaga o launch — o rollback deve reverter o paid_amount E reabrir
    db.delete_launch_and_rollback(user_id, launch_id)

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select paid_amount, status, paid_at from credit_bills where id = %s",
                (bill_id,),
            )
            row = cur.fetchone()
    assert float(row["paid_amount"]) == 0.0, f"paid_amount nao revertido: {row['paid_amount']}"
    assert row["status"] == "open", f"status nao reaberto: {row['status']}"
    assert row["paid_at"] is None


def test_delete_pagamento_parcial_decrementa_paid_amount(user_id):
    """Pagamento parcial: paid_amount=50 sobre total=100. Apagar o launch
    deve decrementar paid pra 0 mas a bill já estava open."""
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.set_default_card(user_id, card_id)
    db.add_launch_and_update_balance(user_id, "receita", 500, None, "seed")
    _, _, bill_id = db.add_credit_purchase(
        user_id, card_id, 100, "outros", "compra", date.today(),
    )

    # Paga só R$ 50 (parcial)
    db.pay_bill_amount(user_id, card_id, "Nubank", 50.0)

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id from launches where user_id=%s and categoria='pagamento_fatura' "
                "order by criado_em desc limit 1",
                (user_id,),
            )
            launch_id = cur.fetchone()["id"]

    db.delete_launch_and_rollback(user_id, launch_id)

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select paid_amount, status from credit_bills where id = %s",
                (bill_id,),
            )
            row = cur.fetchone()
    assert float(row["paid_amount"]) == 0.0
    assert row["status"] == "open"


def test_delete_tx_inexistente_404(user_id):
    _seed_card_and_purchase(user_id)
    client = TestClient(dashboard.app)
    _auth(client, user_id)

    resp = client.delete(
        f"/credit-transactions/{user_id}/99999999",
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 404
