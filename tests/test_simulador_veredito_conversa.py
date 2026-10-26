"""PR 3b pelo `handle_incoming` com banco real (CLAUDE.md §3, "rode a conversa"): o veredito lê o estado que
OUTRO fluxo deixou. Mock só do LLM (molde de `tests/test_investido_conversa.py`); runner, tool e roteamento são
os reais. Prova o CAMINHO e a fala entregue ao modelo, não a redação dele.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import core.handle_incoming as hi
import core.intent_classifier as ic
import db
from core.intent_classifier import IntentResult
from core.types import IncomingMessage
from tests.test_simulador_veredito import gasto, novo  # noqa: F401 (novo = fixture)

REDIGIDO = "RESPOSTA-REDIGIDA-PELO-MODELO"
TV = "TV: não achei risco nos compromissos que conheço para os próximos 90 dias."


class _IA:
    """OpenAI falso: pede a tool da vez (`self.pedidos`, por texto do usuário) e, respondida, redige."""

    def __init__(self):
        self.tool_msgs, self.chamadas = [], 0

    def create(self, **kw):
        assert kw.get("tools")
        self.chamadas += 1
        ultima = kw["messages"][-1]
        if ultima["role"] == "tool":
            self.tool_msgs.append(json.loads(ultima["content"]))
            msg = SimpleNamespace(content=REDIGIDO, tool_calls=[])
        else:
            nome, args = (("delete_all_launches", {}) if "apaga" in ultima["content"].lower()
                          else ("simulate_purchase", {"cenarios": [{"nome": "TV", "preco": 500}]}))
            call = SimpleNamespace(model_dump=lambda: {
                "id": f"c{self.chamadas}", "type": "function",
                "function": {"name": nome, "arguments": json.dumps(args)}})
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


def _hoje(dias):
    from tests.test_simulador_veredito import hoje, timedelta
    return f"{hoje() + timedelta(days=dias):%d/%m}"


def _ultima_fala(ia):
    return ia.tool_msgs[-1]["veredito_fala"]


def test_c1_lancamento_de_outro_fluxo_muda_o_veredito(novo, ia):
    u = novo(1000)
    gasto(u, "Aluguel", 300, 5)

    assert REDIGIDO not in diga(u, "gastei 300 no mercado")  # caminho determinístico real, sem IA
    assert ia.chamadas == 0
    assert any(float(l["valor"]) == 300.0 for l in db.list_launches(u, limit=10))

    assert REDIGIDO in diga(u, "e se eu comprar uma TV de 500?")
    assert _ultima_fala(ia) == [
        f"TV: não recomendo. Contando só o que é certo, no dia {_hoje(5)} seu saldo fica em R$ -100,00."]

    # controle do grupo: sem o lançamento, a mesma pergunta não é risco (a tool leu o estado do outro fluxo)
    outro = novo(1000)
    gasto(outro, "Aluguel", 300, 5)
    assert REDIGIDO in diga(outro, "e se eu comprar uma TV de 500?")
    assert _ultima_fala(ia) == [TV]


def test_c2_pergunta_de_compra_cancela_a_pendencia_de_outro_fluxo_antes_da_tool(novo, ia):
    """Medido: a 2ª mensagem (assunto novo) consome a pendência no runner ANTES de a tool rodar, então a fala
    não traz o `acao_financeira_pendente` e nada fica armado para um "sim" posterior. O bloqueio
    `acao_financeira_pendente` não foi alcançado por conversa; só o veredito (função pura) o cobre."""
    u = novo(1000)
    gasto(u, "Aluguel", 300, 5)
    assert REDIGIDO in diga(u, "apaga tudo")
    assert db.ai_get_pending_action(u)["tool_name"] == "delete_all_launches"
    assert REDIGIDO in diga(u, "e se eu comprar uma TV de 500?")
    assert _ultima_fala(ia) == [TV]
    assert db.ai_get_pending_action(u) is None
    assert any(float(l["valor"]) == 1000.0 for l in db.list_launches(u, limit=10))  # nada foi apagado
