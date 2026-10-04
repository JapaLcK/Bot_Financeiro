"""Fatura de A pendurada no cartão de B (#770): nenhum escritor grava isso; é defesa em
profundidade. Decisão do dono: a fatura SOME das listas e dá 404 no detalhe e no pagamento
(INNER JOIN com `c.user_id = b.user_id`, como cashflow.py, a tela Cartões e o tile); as
compras dela seguem contando em gastos (barreira do #762). Exceção: o estorno de
`rebuild_bill_totals` usa LEFT, senão o excesso pago seria clampado sem devolver o dinheiro.

Cena (`_cena`): A tem "Nubank" com 80 numa fatura (legítima) e 4321 noutra, de outro
período, cujo `card_id` foi trocado para "Cartao do B" (alheia).
"""
from __future__ import annotations

import asyncio
import sys
from decimal import Decimal

import pytest
from fastapi import HTTPException

import db
from conftest import usuario_pagante
from core.handlers.credit import _MONTH_NAMES_PT
from frontend.routes.cards import (
    PayBillPayload, get_bill_detail_route, list_bills_route, pay_bill_route,
)
from tests._fusao_of_helpers import ia_fora, manda  # noqa: F401 (fixture)
from tests._patrimonio_helpers import q
from tests.test_barreira_cartao import _limpo
from tests.test_fusao_of_superficies_da_carteira import _Req, sem_autorizacao  # noqa: F401
from tests.test_resumo_mes_regra import INICIO


def _cena():
    a, b = usuario_pagante(), usuario_pagante()
    cartao_a = db.create_card(a, "Nubank", closing_day=10, due_day=17)
    legitima = db.add_credit_purchase(a, cartao_a, 80, "mercado", "de A", INICIO.replace(day=5))[2]
    alheia = db.add_credit_purchase(a, cartao_a, 4321, "mercado", "alheia", INICIO.replace(day=15))[2]
    cartao_b = db.create_card(b, "Cartao do B", closing_day=10, due_day=17)
    q("update credit_cards set last4 = '9999', color = 'cor-do-b', flag = 'BandeiraDoB' where id = %s",
      (cartao_b,))
    q("update credit_bills set card_id = %s where id = %s", (cartao_b, alheia))
    db.set_balance(a, Decimal("10000"))  # saldo para a alheia também caber: a recusa é pelo dono
    return a, cartao_b, legitima, alheia


def _pagamentos(a):
    return q("select alvo, valor from launches where user_id = %s and categoria = 'pagamento_fatura'",
             (a,), fetch=True)


def test_listas_de_fatura_mostram_a_de_a_e_somem_com_a_alheia():
    a, _, legitima, alheia = _cena()
    for rows in (db.list_open_bills(a), db.list_bills_with_debt(a)):
        _limpo(rows)
        ids = {r["id"]: r["card_name"] for r in rows}
        assert ids.get(legitima) == "Nubank" and alheia not in ids, ids


def test_estorno_da_reconciliacao_devolve_o_excesso_da_alheia_sem_o_nome_de_b():
    a, _, legitima, alheia = _cena()
    q("update credit_bills set paid_amount = total + 30 where id = %s", (alheia,))
    q("update credit_bills set paid_amount = total + 7 where id = %s", (legitima,))
    res = db.rebuild_bill_totals(a, refund_overpayments=True)
    assert (res["paid_clamped"], res["refunded"]) == (2, 37.0), res  # INNER perderia os 30
    estornos = q("select alvo, valor from launches where user_id = %s and categoria = %s",
                 (a, "estorno_pagamento_fatura"), fetch=True)
    _limpo(estornos)
    assert sorted((e["alvo"], float(e["valor"])) for e in estornos) == [
        ("estorno_fatura:Cartão", 30.0), ("estorno_fatura:Nubank", 7.0)]


def test_rotas_de_fatura_escondem_e_recusam_a_alheia(sem_autorizacao):
    a, cartao_b, legitima, alheia = _cena()
    lista = asyncio.run(list_bills_route(_Req(), a, include_closed=True))["bills"]
    _limpo(lista)
    assert [(b["id"], b["card_name"]) for b in lista] == [(legitima, "Nubank")]
    assert asyncio.run(list_bills_route(_Req(), a, card_id=cartao_b, include_closed=True))["bills"] == []

    assert asyncio.run(get_bill_detail_route(_Req(), a, legitima))["bill"]["card_name"] == "Nubank"
    with pytest.raises(HTTPException) as e:
        asyncio.run(get_bill_detail_route(_Req(), a, alheia))
    assert e.value.status_code == 404

    antes = q("select * from credit_bills where id = %s", (alheia,))
    with pytest.raises(HTTPException) as e:
        asyncio.run(pay_bill_route(_Req(), a, alheia, PayBillPayload()))
    assert e.value.status_code == 404
    assert q("select * from credit_bills where id = %s", (alheia,)) == antes
    assert _pagamentos(a) == []

    pago = asyncio.run(pay_bill_route(_Req(), a, legitima, PayBillPayload()))
    assert (pago["ok"], pago["card_name"], pago["paid"]) == (True, "Nubank", 80.0)
    assert [(p["alvo"], float(p["valor"])) for p in _pagamentos(a)] == [("fatura:Nubank", 80.0)]


def _mes(bill_id):
    return _MONTH_NAMES_PT[q("select period_end from credit_bills where id = %s", (bill_id,))
                           ["period_end"].month - 1].lower()


def test_conversa_nao_lista_nem_paga_a_alheia(ia_fora):
    a, _, legitima, alheia = _cena()
    faturas = manda(a, "faturas")
    _limpo(faturas)
    assert "Nubank" in faturas and "4.321" not in faturas, faturas

    antes = q("select * from credit_bills where id = %s", (alheia,))
    assert "Nenhuma fatura em aberto" in manda(a, f"pagar fatura {_mes(alheia)}")
    assert q("select * from credit_bills where id = %s", (alheia,)) == antes
    assert _pagamentos(a) == []

    pago = manda(a, f"pagar fatura {_mes(legitima)}")
    assert "Pagamento registrado" in pago and "Nubank" in pago, pago
    assert [p["alvo"] for p in _pagamentos(a)] == ["fatura:Nubank"]
    assert not ia_fora, ia_fora


def test_diagnostico_mostra_a_alheia_sem_o_nome_de_b(monkeypatch, capsys):
    a, _, legitima, alheia = _cena()
    from scripts.diag_bills import main
    monkeypatch.setattr(sys, "argv", ["diag_bills.py", str(a)])
    main()
    out = capsys.readouterr().out
    _limpo(out)
    linhas = {int(l.strip()[1:].split()[0]): l for l in out.splitlines() if l.strip().startswith("#")}
    assert "Nubank" in linhas[legitima] and "(cartão alheio)" in linhas[alheia], out
