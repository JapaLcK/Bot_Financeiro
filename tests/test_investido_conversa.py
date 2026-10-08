"""A conversa do total investido pelo `handle_incoming`, com banco real (CLAUDE.md §3).

Mock só do LLM: o tier 3 do classificador (`_classify_with_ai`) e o cliente da OpenAI
da IA conversacional (molde de `tests/test_ai_chat_card_quota.py`). O runner, as tools
e o roteamento são os de verdade. Prova o CAMINHO e os NÚMEROS entregues à IA, não a
redação (que é do modelo real, só verificável em produção).
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
from core.types import IncomingMessage
from db import investido
from tests._patrimonio_helpers import conexao, investimento_manual, posicao

REDIGIDO = "RESPOSTA-REDIGIDA-PELO-MODELO"


class _IA:
    """OpenAI falso: no 1º round pede `self.tool`; com a tool respondida, redige."""

    def __init__(self):
        self.tool, self.tool_msgs, self.chamadas = "get_investment_summary", [], 0

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
                "function": {"name": self.tool, "arguments": "{}"}})
            msg = SimpleNamespace(content=None, tool_calls=[call])
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


@pytest.fixture
def ia(monkeypatch):
    import openai

    fake = _IA()
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: SimpleNamespace(chat=SimpleNamespace(completions=fake)))
    # Tier 3: a pergunta de valor vira out_of_scope (o que o prompt novo pede).
    monkeypatch.setattr(ic, "_classify_with_ai",
                        lambda text, user_id=None: IntentResult(intent="out_of_scope", confidence=0.0))
    return fake


def diga(uid, texto) -> str:
    out = hi.handle_incoming(IncomingMessage(platform="whatsapp", user_id=uid, text=texto, message_id="1",
                                             attachments=[], external_id=str(uid), raw={}))
    assert out, texto
    return out[0].text


def _serializado(uid):
    r = investido.ler(uid)
    txt = lambda v: None if v is None else str(v)  # noqa: E731
    return {"total_investido": txt(r["total"]),
            "por_tipo": [{**p, "valor": txt(p["valor"])} for p in r["por_tipo"]],
            "por_banco": [{**p, "valor": txt(p["valor"])} for p in r["por_banco"]],
            "motivos": r["motivos"]}


def _com_banco_e_manual():
    uid = usuario_pagante()
    posicao(conexao(uid, f"item-a-{uid}", banco="Nubank"), "inv-1", "40000", subtipo="CDB")
    posicao(conexao(uid, f"item-b-{uid}", banco="XP"), "inv-2", "17123.45", tipo="EQUITY", subtipo="STOCK")
    investimento_manual(uid, "Tesouro Manual", "321")
    return uid


def _gastos_30(uid) -> int:
    return [float(l["valor"]) for l in db.list_launches(uid, limit=10)].count(30.0)


def test_gasto_depois_quanto_tenho_investido_depois_meus_investimentos(ia):
    uid = _com_banco_e_manual()

    # Com banco conectado, o gasto pergunta a forma de pagamento (Q40) e só grava na resposta.
    assert "dinheiro vivo ou passou pelo banco" in diga(uid, "gastei 30 no mercado")
    diga(uid, "dinheiro")
    assert _gastos_30(uid) == 1  # fica a oferta de recategorizar aberta: a msg 2 passa por ela
    assert ia.chamadas == 0

    r2 = diga(uid, "quanto tenho investido?")
    assert REDIGIDO in r2, r2
    [tool] = ia.tool_msgs
    esperado = _serializado(uid)
    assert {k: tool[k] for k in esperado} == esperado
    assert tool["total_investido"] == "57123.45"
    assert "Tesouro Manual" not in json.dumps(tool) and "321" not in json.dumps(tool)
    assert _gastos_30(uid) == 1

    chamadas = ia.chamadas
    r3 = diga(uid, "meus investimentos")
    assert ia.chamadas == chamadas  # tier 1, sem LLM
    for trecho in ("R$ 57.123,45", "Renda fixa", "Ações", "Nubank", "XP"):
        assert trecho in r3, (trecho, r3)
    assert "Tesouro Manual" not in r3 and "321" not in r3 and "cadastrad" not in r3.lower()


def test_pergunta_de_pagamento_aberta_segura_a_pergunta_de_valor(ia):
    """Registro do comportamento de hoje (não muda neste PR): com a Q40 aberta, a
    pendência tem precedência sobre a IA e a pergunta se repete (com a dica
    "Responde *dinheiro* ou *banco*"); nada é gravado."""
    uid = _com_banco_e_manual()
    pergunta = diga(uid, "gastei 30 no mercado")
    assert diga(uid, "quanto tenho investido?").startswith(pergunta)
    assert ia.chamadas == 0 and _gastos_30(uid) == 0


def test_so_manual_ouve_ainda_nao_sei_no_whatsapp_e_na_ia(ia):
    uid = usuario_pagante()
    investimento_manual(uid, "Tesouro Manual", "321")
    r = diga(uid, "meus investimentos")
    assert "Ainda não sei quanto você tem investido" in r and "Tesouro Manual" not in r
    assert REDIGIDO in diga(uid, "quanto tenho investido?")
    assert ia.tool_msgs[-1]["total_investido"] is None
    assert ia.tool_msgs[-1]["motivos"] == ["sem_banco_conectado"]


def test_historico_velho_chamando_list_investments_nao_quebra(ia):
    uid = _com_banco_e_manual()
    ia.tool = "list_investments"
    assert REDIGIDO in diga(uid, "quanto tenho investido?")
    assert ia.tool_msgs == [{"error": "tool desconhecida: list_investments"}]
