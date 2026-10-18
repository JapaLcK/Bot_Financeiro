"""Os ganchos `ao_confirmar` e `ao_armar` no ramo `requires_confirmation` do runner (`_dispatch_tool`).

CONTROLES NEGATIVOS (rodados na entrega): tirar o `ao_confirmar` do ramo deixa vermelho o primeiro teste
(a pendência e o resumo ficam com os args do modelo); chamar o `ao_armar` ANTES de armar (ou sem checar o
`armada`) deixa vermelhos o segundo (a pendência ainda não existe) e o terceiro (roda com `_OUTRO_PEDIDO`).
CONTROLE POSITIVO: as escritas SEM gancho gravam exatamente os args do modelo, sem chave nova.
"""
from __future__ import annotations

import json

import pytest

import db
from core.services.ai_chat import runner
from core.services.ai_chat.tools import get_tool
from core.services.ai_chat.tools._base import OUTRO_PEDIDO, Tool


def _fake(**kw):
    return Tool(schema={"type": "function", "function": {"name": "fake_apagar", "parameters": {"type": "object"}}},
                is_write=True, requires_confirmation=True, execute=lambda uid, a: "feito",
                summary=lambda a: f"apagar {a.get('quem')}", **kw)


@pytest.fixture
def com_fake(monkeypatch):
    def _liga(tool):
        monkeypatch.setattr(runner, "get_tool", lambda nome: tool if nome == "fake_apagar" else get_tool(nome))
    return _liga


def test_ao_confirmar_ajusta_a_pendencia_e_o_resumo(user_id, com_fake):
    com_fake(_fake(ao_confirmar=lambda uid, a: {**a, "quem": "do banco"}))
    historico, final = runner._dispatch_tool(user_id, "fake_apagar", {"quem": "do modelo"})
    assert final is None
    assert json.loads(historico)["summary"] == "apagar do banco"
    pend = db.ai_get_pending_action(user_id)
    assert pend["tool_args"] == {"quem": "do banco"} and pend["summary"] == "apagar do banco"


def test_ao_armar_roda_depois_de_armada(user_id, com_fake):
    vistos = []
    com_fake(_fake(ao_armar=lambda uid, a: vistos.append((uid, a, db.ai_get_pending_action(uid)))))
    runner._dispatch_tool(user_id, "fake_apagar", {"quem": "x"})
    (uid, a, pend), = vistos
    assert (uid, a) == (user_id, {"quem": "x"}) and pend["tool_name"] == "fake_apagar"


def test_ao_armar_nao_roda_com_outro_pedido_pendente(user_id, com_fake):
    runner._dispatch_tool(user_id, "delete_category_rule", {"keyword": "uber"})  # a outra pendência, viva
    runner._PENDENCIA_DO_TURNO.set(None)  # e de OUTRO turno (senão cai em `_JA_PENDENTE_NO_TURNO`)
    chamou = []
    com_fake(_fake(ao_armar=lambda uid, a: chamou.append(1)))
    historico, final = runner._dispatch_tool(user_id, "fake_apagar", {"quem": "x"})
    assert final == OUTRO_PEDIDO and chamou == []
    assert db.ai_get_pending_action(user_id)["tool_name"] == "delete_category_rule"


@pytest.mark.parametrize("nome,args", [
    ("delete_category_rule", {"keyword": "uber"}),
    ("create_category_rule", {"keyword": "uber", "category": "transporte"}),
    ("delete_all_launches", {}),
])
def test_escrita_sem_gancho_grava_os_args_do_modelo(user_id, nome, args):
    db.add_launch_and_update_balance(user_id, "despesa", 10, "x", None)  # o apagar-tudo só arma com o que apagar
    historico, final = runner._dispatch_tool(user_id, nome, args)
    assert final is None, historico
    pend = db.ai_get_pending_action(user_id)
    assert pend["tool_name"] == nome and pend["tool_args"] == args
    assert json.loads(historico)["args"] == args
