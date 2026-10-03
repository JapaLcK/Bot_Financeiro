"""Gasto fixo de A apontando para o cartão de B (#770).

O PATCH gravava qualquer `card_id` e as leituras juntavam `credit_cards` sem o dono: trocando
o id, A lia o nome do cartão de todo mundo. Agora o PATCH confere o dono como o `create` já
fazia (`CARTAO_NAO_ENCONTRADO`, nada gravado) e a junção leva `c.user_id = r.user_id` — linha
já gravada com o cartão de B volta com `card_name` None (a tela mostra "Cartão ?").
"""
from __future__ import annotations

import asyncio

from fastapi import HTTPException

import db
from conftest import usuario_pagante
from db.recurring import (create_recurring_expense, get_recurring_expense,
                          list_recurring_expenses, update_recurring_expense)
from frontend.finance_bot_websocket_custom import RecurringUpdatePayload, recurring_update_route
from tests._patrimonio_helpers import q
from tests.test_barreira_cartao import _limpo
from tests.test_fusao_of_superficies_da_carteira import _Req, sem_autorizacao  # noqa: F401


def _cena():
    a, b = usuario_pagante(), usuario_pagante()
    nubank = db.create_card(a, "Nubank", closing_day=10, due_day=17)
    inter = db.create_card(a, "Inter", closing_day=10, due_day=17)
    de_b = db.create_card(b, "Cartao do B", closing_day=10, due_day=17)
    q("update credit_cards set last4 = '9999', color = 'cor-do-b', flag = 'BandeiraDoB' where id = %s",
      (de_b,))
    rec = create_recurring_expense(a, "Netflix", 40, "outros", 5, "credit_card", card_id=nubank)["id"]
    return a, rec, nubank, inter, de_b


def _no_banco(rec):
    return q("select name, card_id, payment_type from recurring_expenses where id = %s", (rec,))


def test_patch_para_o_cartao_de_b_e_recusado_e_nada_e_gravado():
    a, rec, nubank, _, de_b = _cena()
    for extra in ({}, {"payment_type": "credit_card"}):
        try:
            update_recurring_expense(a, rec, name="Trocado", card_id=de_b, **extra)
            erro = None
        except ValueError as e:
            erro = str(e)
        assert _no_banco(rec) == {"name": "Netflix", "card_id": nubank, "payment_type": "credit_card"}
        assert erro == "CARTAO_NAO_ENCONTRADO"
    _limpo(get_recurring_expense(a, rec))


def test_rota_devolve_400_para_o_cartao_de_b(sem_autorizacao):  # noqa: F811
    a, rec, nubank, inter, de_b = _cena()
    try:
        asyncio.run(recurring_update_route(_Req(), a, rec, RecurringUpdatePayload(card_id=de_b)))
        erro = None
    except HTTPException as e:
        erro = (e.status_code, e.detail)
    assert _no_banco(rec)["card_id"] == nubank
    assert erro == (400, "Cartão não encontrado.")
    ok = asyncio.run(recurring_update_route(_Req(), a, rec, RecurringUpdatePayload(card_id=inter)))
    assert (ok["recurring"]["card_id"], ok["recurring"]["card_name"]) == (inter, "Inter")


def test_patch_para_cartao_proprio_e_para_conta_continua_funcionando():
    a, rec, nubank, inter, _ = _cena()
    r = update_recurring_expense(a, rec, card_id=inter)
    assert (r["card_name"], _no_banco(rec)["card_id"]) == ("Inter", inter)
    r = update_recurring_expense(a, rec, payment_type="credit_card", card_id=nubank)
    assert (r["card_name"], _no_banco(rec)["card_id"]) == ("Nubank", nubank)
    r = update_recurring_expense(a, rec, payment_type="account")
    assert (r["card_name"], _no_banco(rec)["card_id"], r["payment_type"]) == (None, None, "account")


def test_linha_ja_gravada_com_o_cartao_de_b_nao_mostra_o_nome_dele():
    a, rec, nubank, _, de_b = _cena()
    legit = create_recurring_expense(a, "Spotify", 20, "outros", 6, "credit_card", card_id=nubank)["id"]
    q("update recurring_expenses set card_id = %s where id = %s", (de_b, rec))

    lista = {r["id"]: r for r in list_recurring_expenses(a)}
    assert "Nubank" in _limpo(lista)
    assert (lista[rec]["card_name"], lista[legit]["card_name"]) == (None, "Nubank")

    assert _limpo(get_recurring_expense(a, rec))  # sem nada de B
    assert get_recurring_expense(a, rec)["card_name"] is None
    assert get_recurring_expense(a, legit)["card_name"] == "Nubank"
