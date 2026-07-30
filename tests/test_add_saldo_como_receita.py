"""
"adicione/adicionar/somar X de saldo" é tratado como RECEITA (adiciona dinheiro
ao saldo), igual "recebi". Só vale quando a frase fala de "saldo" — "adicionar"
sozinho é ambíguo (cartão, caixinha, categoria) e não pode virar receita.

Inclui o fix do multiplicador "mil": "10 mil" = 10000 (antes o \\d+ pegava 10).
"""
from __future__ import annotations

from decimal import Decimal

import pytest

import db
from parsers import _extract_valor, parse_receita_despesa_natural
from core.intent_classifier import classify
from core.handlers import launches


# --- multiplicador "mil"/"milhão" ----------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("10 mil", 10000.0),
    ("2,5 mil", 2500.0),
    ("3 milhoes", 3000000.0),
    ("1 mil e 500", 1500.0),
    ("2 mil e 300", 2300.0),
    ("10 mil reais de saldo", 10000.0),
    ("recebi 10 mil", 10000.0),
    ("50", 50.0),            # inteiro simples inalterado
    ("30,50", 30.5),         # decimal inalterado
    ("3 milhas de corrida", 3.0),   # "milhas" não é multiplicador (pega o "3" simples, não 3000)
])
def test_extract_valor_mil(text, expected):
    assert _extract_valor(text) == expected


# --- parse: adicionar...saldo → receita ----------------------------------

def test_parse_adicione_saldo_receita(user_id):
    p = parse_receita_despesa_natural(user_id, "adicione 10 mil de saldo")
    assert p is not None
    assert p["tipo"] == "receita"
    assert p["valor"] == 10000.0
    assert p["alvo"] == "saldo"


def test_parse_somar_ao_saldo(user_id):
    p = parse_receita_despesa_natural(user_id, "somar 500 ao saldo")
    assert p["tipo"] == "receita"
    assert p["valor"] == 500.0


def test_adicionar_sem_saldo_nao_vira_receita(user_id):
    # "adicionar cartao nubank" NÃO é lançamento — não pode ser sequestrado
    assert parse_receita_despesa_natural(user_id, "adicionar cartao nubank") is None


# --- classify roteia pra launches.add ------------------------------------

@pytest.mark.parametrize("text", [
    "adicione 10 mil de saldo",
    "adicionar 500 ao saldo",
    "adicionei 200 de saldo",
    "somar 1000 no saldo",
])
def test_classify_roteia_launches_add(text, user_id):
    r = classify(text, user_id=user_id)
    assert r.intent == "launches.add"


def test_classify_adicionar_cartao_nao_vai_pra_launches(user_id):
    r = classify("adicionar cartao nubank", user_id=user_id)
    assert r.intent != "launches.add"


# --- ponta a ponta: registra receita e sobe o saldo ----------------------

def test_add_registra_receita_e_sobe_saldo(user_id):
    msg = launches.add(user_id, "adicione 10 mil de saldo", {})
    assert "Receita registrada" in msg
    rows = db.list_launches(user_id, limit=1)
    assert rows[0]["tipo"] == "receita"
    assert float(rows[0]["valor"]) == 10000.0
    assert db.get_balance(user_id) == Decimal("10000")
