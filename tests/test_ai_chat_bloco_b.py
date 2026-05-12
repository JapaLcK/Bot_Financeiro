"""
Cobre as 3 read tools do Sprint 2 Bloco B (cartão):
- get_total_debt: soma agregada das faturas em aberto
- list_installments: parcelamentos ativos
- forecast_next_bill: projeção da próxima fatura

São tools de leitura, sem efeito colateral pra além de criar bills vazias
quando `forecast_next_bill` é chamada (side effect existente do helper
`get_next_bill_summary`).
"""
from datetime import date

import db
from core.services.ai_chat.tools.cards import (
    _forecast_next_bill,
    _get_total_debt,
    _list_installments,
)


# ─── get_total_debt ─────────────────────────────────────────────────────────


def test_total_debt_zero_sem_cartao(user_id):
    out = _get_total_debt(user_id, {})
    assert out == {"total_debt": 0, "bills": [], "count": 0}


def test_total_debt_soma_faturas_em_aberto(user_id):
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.add_credit_purchase(user_id, card_id, 100, "lazer", "show", date.today())
    db.add_credit_purchase(user_id, card_id, 50, "alimentação", "ifood", date.today())

    out = _get_total_debt(user_id, {})
    assert out["total_debt"] == 150.0
    assert out["count"] == 1  # uma fatura aberta
    assert out["bills"][0]["card_name"] == "Nubank"
    assert out["bills"][0]["remaining"] == 150.0


def test_total_debt_agrega_dois_cartoes(pro_user_id):
    nubank = db.create_card(pro_user_id, "Nubank", closing_day=10, due_day=17)
    itau = db.create_card(pro_user_id, "Itaú", closing_day=15, due_day=22)
    db.add_credit_purchase(pro_user_id, nubank, 200, "x", "x", date.today())
    db.add_credit_purchase(pro_user_id, itau, 80, "y", "y", date.today())

    out = _get_total_debt(pro_user_id, {})
    assert out["total_debt"] == 280.0
    assert out["count"] == 2


# ─── list_installments ──────────────────────────────────────────────────────


def test_list_installments_vazio_sem_parcelamentos(user_id):
    out = _list_installments(user_id, {})
    assert out == {"groups": [], "count": 0}


def test_list_installments_mostra_grupo_parcelado(user_id):
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.add_credit_purchase_installments(
        user_id, card_id, 300, "lazer", "geladeira", date.today(), installments=3
    )

    out = _list_installments(user_id, {})
    assert out["count"] == 1
    g = out["groups"][0]
    assert g["card_name"] == "Nubank"
    assert g["nota"] == "geladeira"
    assert g["n_total"] == 3
    assert g["n_pending"] == 3
    assert g["total"] == 300.0
    assert g["total_pending"] == 300.0


def test_list_installments_only_pending_false_inclui_quitado(user_id):
    """Com only_pending=False, deveriam aparecer parcelamentos sem bills abertas.

    Como `add_credit_purchase_installments` cria as bills com status='open' por
    padrão, e nesse teste a gente não fecha nenhuma, o filtro tem efeito nulo
    aqui. Mantém o teste pra cobrir a flag não-default.
    """
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.add_credit_purchase_installments(
        user_id, card_id, 100, "x", "tv", date.today(), installments=2
    )

    out_all = _list_installments(user_id, {"only_pending": False})
    assert out_all["count"] == 1


# ─── forecast_next_bill ─────────────────────────────────────────────────────


def test_forecast_next_bill_zero_sem_cartao(user_id):
    out = _forecast_next_bill(user_id, {})
    assert out == {"total": 0, "cards": [], "count": 0}


def test_forecast_next_bill_pega_parcelamento_futuro(user_id):
    """Parcela de N/3 cai na fatura DO MÊS SEGUINTE — forecast deve enxergar."""
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.add_credit_purchase_installments(
        user_id, card_id, 300, "lazer", "tv", date.today(), installments=3
    )

    out = _forecast_next_bill(user_id, {})
    assert out["count"] == 1
    assert out["cards"][0]["card_name"] == "Nubank"
    # A próxima fatura tem 1 parcela de 100 (300/3).
    assert out["total"] == 100.0


def test_forecast_next_bill_filtra_por_card_name(pro_user_id):
    db.create_card(pro_user_id, "Nubank", closing_day=10, due_day=17)
    itau = db.create_card(pro_user_id, "Itaú", closing_day=15, due_day=22)
    db.add_credit_purchase_installments(
        pro_user_id, itau, 200, "x", "y", date.today(), installments=2
    )

    out = _forecast_next_bill(pro_user_id, {"card_name": "Itaú"})
    assert out["count"] == 1
    assert out["cards"][0]["card_name"] == "Itaú"


def test_forecast_next_bill_card_inexistente_retorna_erro(user_id):
    out = _forecast_next_bill(user_id, {"card_name": "Bradesco"})
    assert "error" in out
