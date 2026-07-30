"""
"adicione X" ganha destino:
  - com "saldo"       → receita (test_add_saldo_como_receita.py)
  - com "caixinha Y"  → depósito na caixinha
  - com "investimento" → aporte
  - SEM destino       → o bot PERGUNTA onde (clarification) e a resposta
                        (saldo / caixinha Y / investimento Y) completa a ação.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

import db
from core.intent_classifier import classify
from core.intent_router import route
from core.types import IncomingMessage


def _mk(uid, text):
    return IncomingMessage(platform="whatsapp", user_id=uid, text=text,
                           message_id="x", attachments=[], external_id="x", raw={})


def _route(uid, text):
    r = classify(text, user_id=uid)
    return r.intent, route(r, _mk(uid, text))


# --- roteamento direto ----------------------------------------------------

def test_adicione_caixinha_roteia_deposito(user_id):
    intent, _ = _route(user_id, "adicione 300 na caixinha viagem")
    assert intent == "pockets.deposit"


def test_adicionar_cartao_nao_e_sequestrado(user_id):
    intent, _ = _route(user_id, "adicionar cartao nubank")
    assert intent != "funds.add_ask"
    assert intent != "launches.add"


# --- bare "adicione X" pergunta onde --------------------------------------

def test_adicione_sem_destino_pergunta_onde(user_id):
    intent, resp = _route(user_id, "adicione 10 mil")
    assert intent == "funds.add_ask"
    assert "onde" in resp.lower()
    assert "10.000" in resp
    # armou a clarification
    p = db.get_pending_action(user_id)
    assert p and p["action_type"] == "clarification"
    assert p["payload"]["intent"] == "funds.add_ask"


def test_responde_saldo_registra_receita(user_id):
    _route(user_id, "adicione 10 mil")
    _, resp = _route(user_id, "saldo")
    assert "Receita registrada" in resp
    assert db.get_balance(user_id) == Decimal("10000")


def test_responde_caixinha_deposita(user_id):
    # precisa de saldo na conta pra mover pra caixinha
    db.add_launch_and_update_balance(user_id, "receita", 5000, None, "seed")
    db.create_pocket(user_id, "viagem")
    _route(user_id, "adicione 300")            # pergunta onde
    _, resp = _route(user_id, "caixinha viagem")  # responde o destino
    assert "caixinha" in resp.lower()
    assert "viagem" in resp.lower()
    # 5000 - 300 = 4700 na conta
    assert db.get_balance(user_id) == Decimal("4700")


def test_responde_destino_invalido_repergunta(user_id):
    _route(user_id, "adicione 10 mil")
    _, resp = _route(user_id, "sei la")
    assert "destino" in resp.lower() or "saldo" in resp.lower()
    # mantém a pendência pra não perder o valor
    p = db.get_pending_action(user_id)
    assert p and p["payload"]["intent"] == "funds.add_ask"


def test_cancelar_a_pergunta(user_id):
    _route(user_id, "adicione 10 mil")
    _, resp = _route(user_id, "cancelar")
    assert "cancel" in resp.lower()
    assert db.get_balance(user_id) == Decimal("0")
