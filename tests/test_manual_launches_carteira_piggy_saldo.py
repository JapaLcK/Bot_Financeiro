"""Preservação de `accounts.balance` (Carteira Piggy) para fluxos que o banco cobra.

Par da suíte `test_manual_launches_carteira_piggy.py` (separada pelo portão de
350 linhas): pagamento de fatura NÃO drena a Carteira para usuários com Open
Finance ativo (`delta_conta: 0`, origem `bank` — o extrato OF já reflete o
movimento); sem OF, continua debitando a Carteira como sempre. Inclui o P1 da
revisão Codex: a cobrança recorrente bancária (gravada pelo cobrador antes da
Q42) funde direto com a tx do banco, sem pendência.
"""
from __future__ import annotations

import db
from utils_date import today_tz

from tests.test_manual_launches_carteira_piggy import (  # noqa: F401 (helpers)
    _connect_fake_bank, _dashboard_client, _importa_of_tx,
)


def test_pay_bill_with_open_finance_does_not_drain_carteira(user_id):
    """Fatura paga por quem tem OF: o dinheiro saiu do BANCO (o extrato OF já
    reflete), então o registro interno não debita a Carteira Piggy — mas ainda
    marca a bill como paga."""
    _connect_fake_bank(user_id)
    db.add_launch_and_update_balance(user_id, "receita", 500, None, "seed")
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    _tx, _due, bill_id = db.add_credit_purchase(
        user_id, card_id, 200, "outros", "compra teste", today_tz())

    res = db.pay_bill_amount(user_id, card_id, "Nubank", None, bill_id=bill_id)

    assert res and res.get("paid") == 200
    assert float(db.get_balance(user_id)) == 500.0, "a Carteira Piggy foi drenada"
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select efeitos from launches where id=%s", (res["launch_id"],))
        efeitos = cur.fetchone()["efeitos"]
    assert efeitos["delta_conta"] == 0
    assert efeitos["funding_source"]["kind"] == "bank"
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select status from credit_bills where id=%s", (bill_id,))
        assert cur.fetchone()["status"] == "paid"


def test_pay_bill_sem_open_finance_continua_debitando(user_id):
    """Sem OF nada muda: pagar fatura debita a Carteira, como sempre."""
    db.add_launch_and_update_balance(user_id, "receita", 500, None, "seed")
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    _tx, _due, bill_id = db.add_credit_purchase(
        user_id, card_id, 200, "outros", "compra teste", today_tz())

    res = db.pay_bill_amount(user_id, card_id, "Nubank", None, bill_id=bill_id)

    assert float(db.get_balance(user_id)) == 300.0
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select efeitos from launches where id=%s", (res["launch_id"],))
        efeitos = cur.fetchone()["efeitos"]
    assert efeitos["delta_conta"] == -200
    assert "funding_source" not in efeitos


def test_pay_bill_route_with_open_finance_allows_zero_carteira(user_id):
    """Rota do dashboard: com OF, pagar fatura com Carteira zerada é aceito
    (antes devolvia 400 'Saldo insuficiente' — o pagamento ocorre no banco)."""
    _connect_fake_bank(user_id)
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    _tx, _due, bill_id = db.add_credit_purchase(
        user_id, card_id, 200, "outros", "compra teste", today_tz())
    client, headers = _dashboard_client(user_id, "pay-of@t.com")

    r = client.post(f"/bills/{user_id}/{bill_id}/pay", json={}, headers=headers)

    assert r.status_code == 200, r.text
    assert float(db.get_balance(user_id)) == 0.0


def test_pay_bill_route_sem_open_finance_segue_exigindo_saldo(user_id):
    """Controle do outro lado: sem OF a rota continua bloqueando saldo
    insuficiente na Carteira."""
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    _tx, _due, bill_id = db.add_credit_purchase(
        user_id, card_id, 200, "outros", "compra teste", today_tz())
    client, headers = _dashboard_client(user_id, "pay-sem-of@t.com")

    r = client.post(f"/bills/{user_id}/{bill_id}/pay", json={}, headers=headers)

    assert r.status_code == 400, r.text
    assert "Saldo insuficiente" in r.text


def test_recurring_of_charge_funde_direto_sem_pendencia(pro_user_id):
    """P1 (review Codex): cobrança recorrente em conta para usuário com OF é o
    DÉBITO BANCÁRIO PREVISTO — não dinheiro em espécie. Quando a tx do banco
    chega, o importador FUNDE direto (marcador `of_recurring`): sem 'ask', sem
    pendência, e o mês não conta em dobro.

    O cobrador foi removido (Q42), mas as linhas que ele gravou antes do corte
    continuam no banco e a tx do banco ainda pode chegar depois: o lançamento é
    semeado exatamente como ele gravava."""
    _connect_fake_bank(pro_user_id)
    hoje = today_tz()
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 21.90, alvo="recorrente:Netflix",
        nota="Cobrança automática · Netflix", categoria="streaming",
        is_internal_movement=False, apply_delta=False,
        extra_efeitos={"of_recurring": True},
    )
    assert float(db.get_balance(pro_user_id)) == 0.0, "a Carteira foi drenada"

    rep = _importa_of_tx(pro_user_id, hoje, "21.90", "NETFLIX COBRANCA",
                         f"tx-rec-{pro_user_id}")

    assert rep["auto_merged"] == 1, rep
    assert rep["pending"] == 0, rep
    assert rep["inserted"] == 0, rep
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) as n from launches where user_id=%s", (pro_user_id,))
        assert cur.fetchone()["n"] == 1, "a fusão tem de deixar UMA linha"
