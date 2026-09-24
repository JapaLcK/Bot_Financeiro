"""Saudação não é o help genérico — o passo 6b do `handle_incoming` a deixa passar.

O 6b troca a resposta do bot pela do agente de IA quando ela parece o help
genérico (`_looks_like_help_fallback`, por marcador textual como "Posso te ajudar
com"). A saudação tem dois caminhos, e os dois podiam conter o marcador:

  • a frase fixa sorteada — uma das 4 de "olá" dizia "Posso te ajudar com
    gastos…", então 1 em cada 4 "ola/opa/e aí/hey" de quem tem IA ia para o
    agente, gastando cota e engolindo o aviso "Cancelei a pergunta anterior";
  • a saudação gerada pela IA do Pro, cuja instrução manda "dizer que pode
    ajudar com gastos, saldo, cartões".

Por isso o conserto está no 6b (intenção `greeting` não passa pelo desvio), e
não no texto: trocar a frase fechava só o primeiro caminho.
"""
import core.handlers.greeting as greeting
from _pendencia_credito_helpers import diga, novo_uid
from tests.conftest import promote_to_pro


def _pagante_com_ia(monkeypatch):
    uid = promote_to_pro(novo_uid())
    chamadas = []
    monkeypatch.setattr("core.services.plan_service.ai_chat_allowed", lambda _uid: True)
    monkeypatch.setattr("core.services.ai_chat.chat",
                        lambda *a, **k: chamadas.append(a[1]) or "resposta do agente")
    return uid, chamadas


def test_frase_fixa_de_ola_nao_vai_para_o_agente(monkeypatch):
    uid, chamadas = _pagante_com_ia(monkeypatch)
    monkeypatch.setattr(greeting, "_greeting_with_ai", lambda *a, **k: None)
    # Sorteio fixo em CADA variante: a que tinha o marcador era uma em quatro.
    for i in range(len(greeting._FALLBACK_OLA)):
        monkeypatch.setattr(greeting.random, "choice", lambda seq, i=i: seq[i % len(seq)])
        assert diga(uid, "ola") == greeting._FALLBACK_OLA[i]
    assert chamadas == []


def test_saudacao_gerada_pela_ia_nao_vai_para_o_agente(monkeypatch):
    uid, chamadas = _pagante_com_ia(monkeypatch)
    gerada = "👋 Olá! Posso te ajudar com seus gastos, saldo e cartões!"
    monkeypatch.setattr(greeting, "_greeting_with_ai", lambda *a, **k: gerada)

    assert diga(uid, "ola") == gerada
    assert chamadas == []


def test_help_generico_de_verdade_continua_indo_para_o_agente(monkeypatch):
    """Positivo: sem ele, um 6b desligado para tudo passaria nos dois de cima."""
    uid, chamadas = _pagante_com_ia(monkeypatch)

    assert diga(uid, "cartao principal") == "resposta do agente"
    assert chamadas == ["cartao principal"]


def test_saudacao_com_pedido_de_ajuda_continua_indo_para_o_agente(monkeypatch):
    """"oi, como usar relatório" é `greeting` no classificador, mas o `route()`
    responde a AJUDA inferida — e essa ajuda genérica é o que o 6b escala."""
    uid, chamadas = _pagante_com_ia(monkeypatch)

    assert diga(uid, "oi como usar relatório") == "resposta do agente"
    assert chamadas == ["oi como usar relatório"]
