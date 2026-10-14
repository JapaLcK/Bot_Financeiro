"""A conversa "compara esse mês com o mês passado" pelo `handle_incoming`, com banco real (CLAUDE.md §3).

Mock só do LLM (molde de `tests/test_investido_conversa.py`): o runner, as tools, o gate de plano e o
roteamento são os de verdade. Prova o CAMINHO e os NÚMEROS entregues à IA; a escolha do `mes` e a
redação são do modelo real, só verificáveis em produção.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import core.handle_incoming as hi
import core.intent_classifier as ic
import db
from conftest import usuario_pagante
from core.intent_classifier import IntentResult
from core.services import plan_service as plans
from core.types import IncomingMessage
from utils_date import today_tz

REDIGIDO = "RESPOSTA-REDIGIDA-PELO-MODELO"


class _IA:
    """OpenAI falso: no 1º round pede `compare_periods(mes=<mês corrente>)`; com a tool respondida, redige."""

    def __init__(self):
        self.args, self.tool_msgs, self.chamadas = {"mes": f"{today_tz():%Y-%m}"}, [], 0

    def create(self, **kw):
        assert kw.get("tools"), "só a IA conversacional fala com o modelo aqui"
        self.chamadas += 1
        ultima = kw["messages"][-1]
        if ultima["role"] == "tool":
            self.tool_msgs.append(json.loads(ultima["content"]))
            msg = SimpleNamespace(content=REDIGIDO, tool_calls=[])
        else:
            call = SimpleNamespace(model_dump=lambda: {
                "id": f"c{self.chamadas}", "type": "function",
                "function": {"name": "compare_periods", "arguments": json.dumps(self.args)}})
            msg = SimpleNamespace(content=None, tool_calls=[call])
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


@pytest.fixture
def ia(monkeypatch):
    import openai

    fake = _IA()
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: SimpleNamespace(chat=SimpleNamespace(completions=fake)))
    monkeypatch.setattr(ic, "_classify_with_ai",
                        lambda text, user_id=None: IntentResult(intent="out_of_scope", confidence=0.0))
    return fake


def diga(uid, texto) -> str:
    out = hi.handle_incoming(IncomingMessage(platform="whatsapp", user_id=uid, text=texto, message_id="1",
                                             attachments=[], external_id=str(uid), raw={}))
    assert out, texto
    return out[0].text


def _gastos(uid) -> list[float]:
    return [float(x["valor"]) for x in db.list_launches(uid, limit=10)]


def test_plus_gasta_e_depois_compara_com_o_mes_passado(ia):
    uid = usuario_pagante("plus")
    diga(uid, "gastei 50 no mercado")
    assert _gastos(uid) == [50.0] and ia.chamadas == 0
    assert REDIGIDO in diga(uid, "compara esse mês com o mês passado")
    [tool] = ia.tool_msgs
    assert tool["modo"] == "mes_aberto" and tool["atual"]["inicio"] == f"{today_tz():%Y-%m}-01"
    assert tool["despesa"]["total_atual"] == 50.0 and tool["despesa"]["total_anterior"] == 0.0
    assert tool["despesa"]["estado"] == "nova" and tool["despesa"]["pct"] is None   # sem mês passado: nada de +100%
    assert tool["motivo"] == "sem_base" and tool["base"]["cartao"] == "fatura" and tool["escolha"]
    assert _gastos(uid) == [50.0]                                    # a pergunta não virou lançamento


def test_essencial_recebe_pro_required_sem_nenhum_campo_de_comparacao(ia, monkeypatch):
    monkeypatch.setenv("PLANS_V2_ENABLED", "1")
    uid = usuario_pagante("essencial")
    assert plans.get_plan_tier(uid) == "essencial"
    diga(uid, "gastei 50 no mercado")
    assert REDIGIDO in diga(uid, "compara esse mês com o mês passado")
    [tool] = ia.tool_msgs
    assert tool["error"] == "pro_required" and set(tool) == {"error", "message"}
    assert _gastos(uid) == [50.0]
