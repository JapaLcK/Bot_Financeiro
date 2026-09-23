"""Saudação não é o help genérico — e o passo 6b do `handle_incoming` decide pelo TEXTO.

O 6b troca a resposta do bot pela da IA quando ela parece o help genérico
(`_looks_like_help_fallback`, por marcador textual). Uma das saudações de "olá"
dizia "Posso te ajudar com gastos, saldo…", que é justamente um dos marcadores:
para quem tem IA, 1 em cada 4 "ola/opa/e aí/hey" ia para a IA — gastando cota —
e, quando a saudação abandonava uma pendência, o aviso "Cancelei a pergunta
anterior" sumia junto com a resposta trocada.

A classe é "resposta de saudação com cara de help", então o primeiro teste
varre TODAS as variantes de TODAS as saudações; o segundo roda a conversa.
"""
import core.handlers.greeting as greeting
import core.handle_incoming as hi
from _pendencia_credito_helpers import diga, novo_uid
from tests.conftest import promote_to_pro


def _todas_as_saudacoes() -> list[str]:
    pools = [v for k, v in vars(greeting).items() if k.startswith("_FALLBACK_")]
    assert pools, "os pools de saudação mudaram de nome — o teste ficou cego"
    return [texto for pool in pools for texto in pool]


def test_nenhuma_saudacao_parece_o_help_generico():
    com_cara_de_help = [t for t in _todas_as_saudacoes() if hi._looks_like_help_fallback(t)]
    assert com_cara_de_help == []

    # Positivo: o detector continua pegando o help de verdade.
    assert hi._looks_like_help_fallback("🧾 Posso te ajudar com lançamentos assim:\n• gastei 50")


def test_ola_de_quem_tem_ia_responde_a_saudacao_sem_chamar_a_ia(monkeypatch):
    uid = promote_to_pro(novo_uid())
    chamadas = []
    monkeypatch.setattr("core.services.plan_service.ai_chat_allowed", lambda _uid: True)
    monkeypatch.setattr("core.services.ai_chat.chat",
                        lambda *a, **k: chamadas.append(a) or "resposta da IA")
    # Sorteio fixo em CADA variante: a antiga ofensora era uma em quatro.
    for i in range(len(greeting._FALLBACK_OLA)):
        monkeypatch.setattr(greeting.random, "choice", lambda seq, i=i: seq[i % len(seq)])
        resposta = diga(uid, "ola")
        assert resposta != "resposta da IA", f"variante {i} foi para a IA"
    assert chamadas == []
