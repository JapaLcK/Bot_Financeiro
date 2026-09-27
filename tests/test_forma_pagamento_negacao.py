"""Q40 — negação no texto: o texto não decide a forma (review Codex P1 no #633).

"não foi em dinheiro" gravava na Carteira; "pix não" virava banco. Regra:
qualquer "não/nem/nunca/sem" → nenhuma forma pelo texto. Com banco, o Piggy
pergunta (ou repete a pergunta); sem banco, grava na Carteira como antes (A2).
"não" sozinho cancela: `test_a7_cancelar_apaga_e_nao_grava`.
Fonte da regra: `core/handlers/forma_pagamento.py::NEGACAO_RE`.
"""
from __future__ import annotations

import pytest

import db
from tests._fusao_of_helpers import ia_fora, manda, uid_pro  # noqa: F401 (fixtures)
from tests.test_forma_pagamento_conversa import (  # noqa: F401 (fixtures)
    com_of, compras_credito, manuais, pendencia, sem_of)

PERGUNTA = "payment_method_choice"


@pytest.mark.parametrize("resposta", [
    "não foi em dinheiro", "nao foi dinheiro", "não, foi em dinheiro", "não, dinheiro",
    "não, pix", "pix não", "dinheiro não", "sem ser no pix",
])
def test_resposta_com_negacao_repete_a_pergunta(com_of, ia_fora, resposta):
    manda(com_of, "gastei 500 no mercado")
    r = manda(com_of, resposta)
    assert manuais(com_of) == 0, r
    assert "Não registrei" not in r, r
    assert pendencia(com_of) == PERGUNTA, r
    assert not ia_fora


@pytest.mark.parametrize("frase", [
    "gastei 50 no mercado, não foi no pix", "gastei 50 em dinheiro porque não tinha pix",
    "gastei 50 no mercado, nem pix nem cartão, foi dinheiro vivo",
    "gastei 50 sem desconto no pix", "gastei 50 no mercado, não foi no cartão",
])
def test_frase_com_negacao_pergunta(com_of, ia_fora, frase):
    r = manda(com_of, frase)
    assert "dinheiro vivo" in r and "Não registrei" not in r, r
    assert manuais(com_of) == 0 and compras_credito(com_of) == 0
    assert pendencia(com_of) == PERGUNTA


@pytest.mark.parametrize("frase", [
    "gastei 50 no mercado, não foi no pix", "gastei 50 em dinheiro porque não tinha pix"])
def test_frase_com_negacao_sem_of_grava_como_antes(sem_of, ia_fora, frase):
    manda(sem_of, frase)
    assert manuais(sem_of) == 1 and pendencia(sem_of) != PERGUNTA


def test_cartao_negado_com_of_pergunta_e_dinheiro_grava_na_carteira(com_of, ia_fora):
    manda(com_of, "gastei 50 no mercado, não foi no cartão")
    r = manda(com_of, "dinheiro")
    assert manuais(com_of) == 1 and compras_credito(com_of) == 0, r


# "no cartão, não foi no débito": o débito negado não tira a compra do cartão
# manual (sem a guarda ela ia para a Carteira; antes da Q2b era crédito).
def test_debito_negado_com_cartao_manual_e_credito(com_of, sem_of, ia_fora):
    for uid in (com_of, sem_of):
        card = db.create_card(uid, "Manual", closing_day=10, due_day=17)
        db.set_default_card(uid, card)
        manda(uid, "gastei 50 no cartão, não foi no débito")
        assert compras_credito(uid) == 1 and manuais(uid) == 0, uid
