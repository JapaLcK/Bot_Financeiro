"""Lançamentos manuais exclusivos para dinheiro (Carteira Piggy).

Decisão de produto (2026-09): lançamento manual representa estritamente
dinheiro em espécie da Carteira Piggy. Compras em cartão de crédito manuais
só valem para cartão FORA do Open Finance (`credit_cards.open_finance_account_id
is null`); cartão coberto tem as compras importadas automaticamente e o
lançamento manual é recusado (400 na API, aviso no bot). A FUSÃO SILENCIOSA
com transações do Open Finance foi removida nos dois sentidos: a reversa
(`reconcile_manual_launch`) saiu dos três escritores (rota do dashboard,
handler do bot, entrada rápida) e a direta rebaixa 'auto' → 'ask' no importador
quando o candidato é manual — o dinheiro em espécie NUNCA some em silêncio.
A reconciliação CONFIRMAVEL permanece: o casamento ambíguo vira pendência e o
usuário decide (confirm/reject); outras fontes elegíveis (`ofx`) seguem
auto-fundindo. E `accounts.balance` (a Carteira) não é drenado por fluxos que
o banco cobra: pagamento de fatura (`pay_bill_amount` grava `delta_conta: 0`,
`funding_source: {"kind": "bank"}`) e cobrança de gasto fixo em conta
(`recurring_charger` com `apply_delta=False`) — para usuários com Open Finance
O par `test_manual_launches_carteira_piggy_saldo.py` cobre a preservação de
`accounts.balance` (pagamento de fatura e cobrança recorrente) — separado pelo
portão de 350 linhas, compartilhando os helpers deste módulo.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

import db
from conftest import promote_to_pro
import frontend.finance_bot_websocket_custom as dashboard
from utils_date import today_tz


# ── helpers ──────────────────────────────────────────────────────────────────

def _connect_fake_bank(user_id: int, nome: str = "Conta") -> int:
    connection = db.save_pluggy_open_finance_item(
        user_id,
        {"id": f"item-cp-{user_id}-{nome}", "connector": {"id": 612, "name": "Nubank"},
         "status": "UPDATED"},
    )
    db.save_open_finance_sync(
        connection["id"],
        [{
            "provider_account_id": f"acc-cp-{user_id}-{nome}",
            "name": nome, "type": "BANK", "subtype": "CHECKING_ACCOUNT",
            "currency": "BRL", "balance": Decimal("1000.00"), "raw": {},
            "transactions": [],
        }],
    )
    return connection["id"]


def _importa_of_tx(user_id: int, dia, valor: str, descricao: str, tx_id: str) -> dict:
    """Importa UMA tx OF pelo caminho de produção (espelho Pluggy → importador)."""
    conexao = db.save_pluggy_open_finance_item(
        user_id,
        {"id": f"item-{tx_id}", "connector": {"id": 612, "name": "Nubank"},
         "status": "UPDATED"},
    )
    db.save_open_finance_sync(conexao["id"], [{
        "provider_account_id": f"acc-{tx_id}",
        "name": "Nubank Conta", "type": "BANK", "subtype": "CHECKING_ACCOUNT",
        "currency": "BRL", "balance": Decimal("1000.00"), "raw": {},
        "transactions": [{
            "provider_transaction_id": tx_id,
            "description": descricao,
            "amount": Decimal("-" + valor),
            "transaction_date": dia,
            "transacted_at": None,
            "category": "mercado",
            "raw": {},
        }],
    }])
    return db.import_open_finance_launches(user_id, conexao["id"])


def _dashboard_client(user_id: int, email: str):
    promote_to_pro(user_id)  # atravessa o gate de acesso do v2
    from fastapi.testclient import TestClient

    client = TestClient(dashboard.app)
    client.cookies.set(dashboard.AUTH_COOKIE_NAME, dashboard._make_jwt(user_id, email))
    client.cookies.set(dashboard.DASHBOARD_COOKIE_NAME,
                       dashboard.make_dashboard_token(user_id, hours=1))
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, "t")
    return client, {dashboard.CSRF_HEADER_NAME: "t", "Content-Type": "application/json"}


def _cobertura_open_finance(user_id: int, card_id: int) -> None:
    """Liga o cartão a uma conta OF — o estado 'coberto pelo Open Finance'."""
    of_account_id = db.list_bank_accounts(user_id)[0]["id"]
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "update credit_cards set open_finance_account_id=%s where user_id=%s and id=%s",
            (of_account_id, user_id, card_id),
        )
        conn.commit()


def _status_tx_of(tx_id: str) -> str:
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select reconciliation_status from open_finance_transactions "
                    "where provider_transaction_id=%s", (tx_id,))
        return cur.fetchone()["reconciliation_status"]


# ── reconciliação desativada nas duas ordens ─────────────────────────────────

def test_reconciliation_order_manual_then_of_import(user_id):
    """Manual primeiro, importação OF depois: a tx bancária NÃO funde com o
    lançamento de dinheiro — `auto_merged=0` e o OF launch entra como linha
    separada."""
    hoje = today_tz()
    db.add_launch_and_update_balance(user_id, "despesa", 50, "Mercado", "mercado", "mercado")

    rep = _importa_of_tx(user_id, hoje, "50.00", "MERCADO PAGUE MENOS", f"tx-a-{user_id}")

    assert rep["auto_merged"] == 0, rep
    assert rep["inserted"] == 1, rep
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) as n from launches where user_id=%s", (user_id,))
        assert cur.fetchone()["n"] == 2  # manual + OF launch separado


def test_reconciliation_order_of_import_then_manual(user_id):
    """OF primeiro, manual depois (caminho do handler do bot): a reconciliação
    reversa foi removida — a tx OF continua 'imported' no PRÓPRIO OF launch, o
    OF launch não é apagado e o manual vira linha própria."""
    from core.handlers import launches as h_launches

    hoje = today_tz()
    tx_id = f"tx-b-{user_id}"
    rep = _importa_of_tx(user_id, hoje, "50.00", "MERCADO PAGUE MENOS", tx_id)
    assert rep["inserted"] == 1

    h_launches.add_from_entities(
        user_id, tipo="despesa", valor=50, alvo="Mercado Pague Menos",
        nota="Mercado Pague Menos", categoria="mercado",
    )

    assert _status_tx_of(tx_id) == "imported"
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select imported_launch_id from open_finance_transactions "
                    "where provider_transaction_id=%s", (tx_id,))
        of_launch_id = cur.fetchone()["imported_launch_id"]
        cur.execute("select count(*) as n from launches where user_id=%s and id=%s",
                    (user_id, of_launch_id))
        assert cur.fetchone()["n"] == 1, "o OF launch foi apagado pela reversa"
        cur.execute("select count(*) as n from launches where user_id=%s and source='manual'",
                    (user_id,))
        assert cur.fetchone()["n"] == 1, "o lançamento manual não foi criado"


def test_quick_entry_does_not_trigger_reverse_reconciliation(user_id):
    """Entrada rápida (primeira linha do pipeline do bot) também não reconcilia:
    tx OF de mesmo valor/data fica 'imported' no próprio launch."""
    from core.services.quick_entry import handle_quick_entry

    hoje = today_tz()
    tx_id = f"tx-c-{user_id}"
    rep = _importa_of_tx(user_id, hoje, "50.00", "MERCADO PAGUE MENOS", tx_id)
    assert rep["inserted"] == 1

    out = handle_quick_entry(user_id, "gastei 50 no mercado")
    assert out is not None

    assert _status_tx_of(tx_id) == "imported"


# ── cartão coberto vs não coberto ────────────────────────────────────────────

def test_credit_launch_blocked_for_covered_card(user_id):
    """Cartão com `open_finance_account_id` não aceita compra manual: 400 na API
    e aviso no bot, sem tocar em `credit_transactions`."""
    from core.handlers import credit as h_credit

    _connect_fake_bank(user_id)
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    _cobertura_open_finance(user_id, card_id)
    client, headers = _dashboard_client(user_id, "covered@t.com")

    r = client.post(f"/launches/{user_id}",
                    json={"tipo": "credito", "valor": 50, "card_id": card_id},
                    headers=headers)
    assert r.status_code == 400, r.text
    assert "Open Finance" in r.text

    msg = h_credit.add_credit_from_entities(user_id, valor=50, card_name="Nubank")
    assert "sincronizado via Open Finance" in msg

    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) as n from credit_transactions where user_id=%s", (user_id,))
        assert cur.fetchone()["n"] == 0


@pytest.mark.parametrize("como", ["pausar", "deletar"])
def test_credit_launch_allowed_when_covered_connection_inactive(user_id, como):
    """P2 (review Codex): `open_finance_account_id` não é prova de sync ativo.
    Conexão PAUSED (trial vencido) ou DELETED mantém o vínculo e para de
    importar — compra manual no cartão tem de ser PERMITIDA (API e bot), senão
    o usuário fica sem registrar gasto nenhum."""
    from core.handlers import credit as h_credit

    conn_id = _connect_fake_bank(user_id)
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    _cobertura_open_finance(user_id, card_id)
    if como == "pausar":
        db.pause_open_finance_connection(conn_id)
    else:
        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute("update open_finance_connections set status='DELETED' where id=%s",
                        (conn_id,))
            conn.commit()

    msg = h_credit.add_credit_from_entities(user_id, valor=50, card_name="Nubank")
    assert "🪪" in msg

    client, headers = _dashboard_client(user_id, f"inactive-{como}@t.com")
    r = client.post(f"/launches/{user_id}",
                    json={"tipo": "credito", "valor": 80, "card_id": card_id},
                    headers=headers)
    assert r.status_code == 200, r.text


def test_credit_launch_allowed_for_uncovered_card(user_id):
    """Cartão manual (`open_finance_account_id` NULL) continua lançável — MESMO
    com Open Finance ativo em outros produtos (aqui, a conta bancária)."""
    from core.handlers import credit as h_credit

    _connect_fake_bank(user_id)
    card_id = db.create_card(user_id, "Cartao Manual", closing_day=10, due_day=17)
    client, headers = _dashboard_client(user_id, "uncovered@t.com")

    r = client.post(f"/launches/{user_id}",
                    json={"tipo": "credito", "valor": 80, "card_id": card_id},
                    headers=headers)
    assert r.status_code == 200, r.text

    msg = h_credit.add_credit_from_entities(user_id, valor=30, card_name="Cartao Manual")
    assert "🪪" in msg


def test_funding_source_rejected_for_credit(user_id):
    card_id = db.create_card(user_id, "Cartao Manual", closing_day=10, due_day=17)
    client, headers = _dashboard_client(user_id, "fs-credit@t.com")

    r = client.post(f"/launches/{user_id}",
                    json={"tipo": "credito", "valor": 50, "card_id": card_id,
                          "funding_source": "carteira"},
                    headers=headers)
    assert r.status_code == 400, r.text
    assert "funding_source não se aplica a compras no crédito" in r.text


# ── validação de funding_source e rótulos ────────────────────────────────────

@pytest.mark.parametrize("tipo", ["receita", "despesa"])
def test_funding_source_invalid_rejected(user_id, tipo):
    client, headers = _dashboard_client(user_id, f"fs-inv-{tipo}@t.com")
    r = client.post(f"/launches/{user_id}",
                    json={"tipo": tipo, "valor": 50, "funding_source": "banco"},
                    headers=headers)
    assert r.status_code == 400, r.text
    assert "Carteira Piggy" in r.text


@pytest.mark.parametrize("funding", [None, "carteira", "piggy"])
def test_funding_source_carteira_accepted(user_id, funding):
    client, headers = _dashboard_client(user_id, f"fs-ok-{funding}@t.com")
    payload = {"tipo": "receita", "valor": 100}
    if funding is not None:
        payload["funding_source"] = funding
    r = client.post(f"/launches/{user_id}", json=payload, headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["funding_source"] == {"kind": "carteira", "label": "Carteira Piggy"}


def test_bot_shows_carteira_piggy_when_of_connected(user_id):
    """Sem OF o saldo segue 'Saldo'; com OF conectado vira 'Saldo (Carteira
    Piggy)' — o usuário precisa saber que aquele número é o dinheiro em espécie,
    não o saldo do banco."""
    from core.handlers import launches as h_launches

    msg_sem = h_launches.add_from_entities(
        user_id, tipo="receita", valor=100, alvo="salario", categoria="salario")
    assert "🏦 Saldo:" in msg_sem
    assert "Carteira Piggy" not in msg_sem

    _connect_fake_bank(user_id)
    msg_com = h_launches.add_from_entities(
        user_id, tipo="despesa", valor=20, alvo="padaria", categoria="alimentacao")
    assert "👛 Saldo (Carteira Piggy):" in msg_com


def test_quick_entry_label_carteira_piggy_when_of_connected(user_id):
    from core.services.quick_entry import handle_quick_entry

    _connect_fake_bank(user_id)
    out = handle_quick_entry(user_id, "recebi 200 freelas")
    assert "👛 Saldo (Carteira Piggy):" in out.text
