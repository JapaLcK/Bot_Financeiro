"""Roteamento das perguntas do fluxo de aporte em investimentos.

O risco desta parte não é a pergunta, é o que acontece quando o usuário
*ignora* a pergunta. Nas etapas de nome e de taxa qualquer texto é resposta
válida, então sem uma saída "saldo" viraria um investimento chamado "Saldo" —
e o pendente ficaria armado esperando um "sim" que o usuário nem lembra.
"""
from unittest.mock import patch

import pytest

from core import intent_router
from core.intent_classifier import IntentResult
from core.types import IncomingMessage


def _msg(text):
    return IncomingMessage(platform="whatsapp", user_id=123, text=text)


def _route(text, intent, confidence, pending):
    with patch("core.intent_router.h_help.infer_help_from_text", return_value=None), \
         patch("core.intent_router.h_pending.get_pending_clarification", return_value=None), \
         patch("core.intent_router.db.get_pending_action", return_value=pending), \
         patch("core.intent_router.db.clear_pending_action") as clear, \
         patch("core.intent_router.h_investments.resolve_pending",
               return_value="RESPOSTA-DO-FLUXO") as resolve:
        resp = intent_router.route(
            IntentResult(intent=intent, confidence=confidence, entities={}), _msg(text)
        )
    return resp, resolve, clear


_PENDENTES = [
    {"action_type": "investment_pick", "payload": {"amount": 800.0}},
    {"action_type": "investment_create", "payload": {"amount": 800.0, "etapa": "nome"}},
]


@pytest.mark.parametrize("pending", _PENDENTES, ids=lambda p: p["action_type"])
def test_resposta_vai_para_o_fluxo(pending):
    """Nome de investimento cai como out_of_scope/baixa confiança — é resposta."""
    resp, resolve, clear = _route("Renda Fixa BB", "out_of_scope", 0.9, pending)
    resolve.assert_called_once()
    clear.assert_not_called()
    assert resp == "RESPOSTA-DO-FLUXO"


@pytest.mark.parametrize("pending", _PENDENTES, ids=lambda p: p["action_type"])
@pytest.mark.parametrize("intent", ["confirm.yes", "confirm.no"])
def test_sim_e_nao_vao_para_o_fluxo(pending, intent):
    resp, resolve, clear = _route("sim", intent, 0.99, pending)
    resolve.assert_called_once()
    clear.assert_not_called()
    assert resp == "RESPOSTA-DO-FLUXO"


@pytest.mark.parametrize("pending", _PENDENTES, ids=lambda p: p["action_type"])
def test_intent_de_investimento_ainda_e_resposta(pending):
    """"CDB Nubank" pode ser classificado como investments.create e mesmo assim
    é o nome que o bot acabou de pedir."""
    resp, resolve, _clear = _route("CDB Nubank", "investments.create", 0.9, pending)
    resolve.assert_called_once()
    assert resp == "RESPOSTA-DO-FLUXO"


@pytest.mark.parametrize("pending", _PENDENTES, ids=lambda p: p["action_type"])
def test_outro_comando_claro_abandona_a_pergunta(pending):
    """'saldo' no meio do fluxo não pode virar nome de investimento, e o
    pendente precisa morrer para um 'sim' futuro não ressuscitá-lo."""
    resp, resolve, clear = _route("saldo", "balance.get", 0.95, pending)
    resolve.assert_not_called()
    clear.assert_called_once_with(123)
    assert resp != "RESPOSTA-DO-FLUXO"


@pytest.mark.parametrize("pending", _PENDENTES, ids=lambda p: p["action_type"])
def test_baixa_confianca_nao_abandona(pending):
    """Palpite fraco do classificador não desarma a pergunta em curso."""
    _resp, resolve, clear = _route("aquele lá", "balance.get", 0.3, pending)
    resolve.assert_called_once()
    clear.assert_not_called()


def test_fluxo_nao_intercepta_pendente_de_outro_tipo():
    _resp, resolve, _clear = _route(
        "sim", "confirm.yes", 0.99, {"action_type": "delete_launch", "payload": {}}
    )
    resolve.assert_not_called()


def test_none_do_fluxo_deixa_o_roteamento_seguir():
    """resolve_pending devolve None quando a mensagem não é resposta — aí o
    roteador segue, em vez de prender o usuário."""
    pending = {"action_type": "investment_create", "payload": {"etapa": "?"}}
    with patch("core.intent_router.h_help.infer_help_from_text", return_value=None), \
         patch("core.intent_router.h_pending.get_pending_clarification", return_value=None), \
         patch("core.intent_router.db.get_pending_action", return_value=pending), \
         patch("core.intent_router.h_investments.resolve_pending", return_value=None), \
         patch("core.intent_router.h_investments.list_investments",
               return_value="CARTEIRA") as lista:
        resp = intent_router.route(
            IntentResult(intent="investments.list", confidence=0.9, entities={}),
            _msg("investimentos"),
        )
    lista.assert_called_once()
    assert resp == "CARTEIRA"
