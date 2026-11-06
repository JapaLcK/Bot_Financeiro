"""PR-B (b) (ADR 0006): a declaração pelo chat IA na web (P5 `/ai/chat`, widget do /dashboard; P6 `/ai/chat/stream`,
/painel e app). O id da mensagem é o `client_message_id` que o cliente manda; sem ele, T18 (D6=A). Pelo `TestClient`,
com sessão, banco e runner reais; só o modelo é falso.
Controles negativos: os da `reentrega` (passo 1 e 2), e `chave_da_mensagem` caindo num id do servidor (T8: sem id
passaria a armar).
"""
from __future__ import annotations

import uuid

import pytest

from core.handlers import declaracao_banco_textos as tx
from tests._chat_ia_apoio import CONFIRMA, FRASE, declaracoes, livro, pendencia_ia, t3, valores, wa, wamid
from tests._ia_stream_helpers import conta, eventos_sse, openai_falso, rodada
from tests.test_bank_movements import _bank
from tests.test_ai_chat_metas_escrita_ambiguo import VIAGEM, k  # noqa: F401 (fixture)
from tests.test_senha_obrigatoria import H
from tests.test_wa_minhas_metas import chave  # noqa: F401 (fixture)

# D 1A: no `/ai/chat` (widget) o negrito é **x**; o stream (/painel e app) e o WhatsApp seguem com *x*.
CONFIRMA_WEB = "🐷 Só confirmando: registrar **despesa de R$ 50,00 em mercado, hoje, pelo banco**? Responde **sim** ou **não**."
SEM_ID_WEB = tx.SEM_ID.replace("*em dinheiro*", "**em dinheiro**")
BANCO = ("add_launch", {"tipo": "despesa", "valor": 50, "alvo": "mercado", "forma_pagamento": "banco"})
PORCO = "🐷 "


@pytest.fixture
def web(monkeypatch):
    uid, c = conta()
    _bank(uid)
    monkeypatch.setenv("DECLARACAO_BANCO", "1")
    return uid, c


def mid() -> str:
    return uuid.uuid4().hex


def chat(c, texto, ident=None):
    corpo = {"message": texto, **({"client_message_id": ident} if ident else {})}
    r = c.post("/ai/chat", json=corpo, headers=H)
    assert r.status_code == 200, r.text
    return r.json()["reply"]


def stream(c, texto, ident=None):
    corpo = {"message": texto, "cards": [], **({"client_message_id": ident} if ident else {})}
    return eventos_sse(c.post("/ai/chat/stream", json=corpo, headers=H))[-1]["reply"]


@pytest.mark.parametrize("canal", [chat, stream])
def test_arma_reenvia_confirma_e_repete(web, monkeypatch, canal):
    uid, c = web
    reg = openai_falso(monkeypatch, rodada(None, BANCO))
    m1, s1 = mid(), mid()
    confirma = CONFIRMA_WEB if canal is chat else CONFIRMA.removeprefix(PORCO)
    assert canal(c, FRASE, m1) == confirma
    assert canal(c, FRASE, m1) == confirma  # re-POST da mesma mensagem antes do "sim"
    assert "registrada pelo banco" in canal(c, "sim", s1)
    assert canal(c, "sim", s1) == t3(1).removeprefix("" if canal is chat else PORCO)  # o mesmo "sim" de novo
    assert canal(c, FRASE, m1).endswith(t3(1).removeprefix(PORCO))  # a mesma mensagem depois do "sim"
    assert valores(uid) == [50.0] and len(reg.creates) == 1


@pytest.mark.parametrize("canal", [chat, stream])
def test_sem_client_message_id_e_t18_e_nada_arma(web, monkeypatch, canal):
    uid, c = web
    openai_falso(monkeypatch, rodada(None, BANCO))
    assert canal(c, FRASE).endswith((SEM_ID_WEB if canal is chat else tx.SEM_ID).removeprefix(PORCO))
    assert pendencia_ia(uid) is None and livro(uid) == [] and declaracoes(uid) == []


def test_sim_sem_id_nao_consome_a_declaracao_armada_com_id(web, monkeypatch):
    uid, c = web
    openai_falso(monkeypatch, rodada(None, BANCO))
    chat(c, FRASE, mid())
    antes = pendencia_ia(uid)["created_at"]
    assert chat(c, "sim") == SEM_ID_WEB
    assert pendencia_ia(uid)["created_at"] == antes and declaracoes(uid) == []
    assert "registrada pelo banco" in chat(c, "sim", mid())


def test_r5_nao_sem_id_nao_consome_a_declaracao_armada_com_id(web, monkeypatch):
    """Decisão do dono (R5, A): o "não" sem id recebe T18 e a pendência fica intacta; o "sim" com id grava uma vez."""
    uid, c = web
    openai_falso(monkeypatch, rodada(None, BANCO))
    chat(c, FRASE, mid())
    antes = pendencia_ia(uid)["created_at"]
    assert chat(c, "não") == SEM_ID_WEB
    assert pendencia_ia(uid)["created_at"] == antes and declaracoes(uid) == []
    assert "registrada pelo banco" in chat(c, "sim", mid())
    assert valores(uid) == [50.0]


RUINS = ["curto", "x" * 65, "com espaço!", "../../etc", 12345678, {"a": 1}]


@pytest.mark.parametrize("canal", [chat, stream])
@pytest.mark.parametrize("ruim", RUINS)
def test_id_fora_do_formato_vale_como_ausente_com_a_flag_ligada(web, monkeypatch, canal, ruim):
    """Nunca 422: o id fora do formato é o id ausente (T18, nada arma)."""
    uid, c = web
    openai_falso(monkeypatch, rodada(None, BANCO))
    assert canal(c, FRASE, ruim).endswith((SEM_ID_WEB if canal is chat else tx.SEM_ID).removeprefix(PORCO))
    assert pendencia_ia(uid) is None and livro(uid) == []


@pytest.mark.parametrize("canal", [chat, stream])
@pytest.mark.parametrize("ruim", RUINS)
def test_id_fora_do_formato_com_a_flag_desligada_e_a_resposta_de_sempre(monkeypatch, canal, ruim):
    """A regra do dono: com a flag desligada, nada muda (a `main` não conhece o campo e responde 200)."""
    monkeypatch.delenv("DECLARACAO_BANCO", raising=False)
    uid, c = conta()
    _bank(uid)
    openai_falso(monkeypatch, rodada(None, BANCO))
    assert "Pix, cartão e débito chegam sozinhos pelo Open Finance." in canal(c, FRASE, ruim)
    assert pendencia_ia(uid) is None and livro(uid) == []


def test_na_web_so_os_textos_da_declaracao_trocam_o_negrito(web, monkeypatch):
    """D 1A, positivo: resposta livre do modelo com *x* sai como veio; no stream e no WhatsApp a confirmação segue com *x*."""
    uid, c = web
    openai_falso(monkeypatch, rodada("Seu *saldo* está ok."), rodada(None, BANCO))
    assert chat(c, "oi", mid()) == "Seu *saldo* está ok."
    assert stream(c, FRASE, mid()) == CONFIRMA.removeprefix(PORCO)  # o /painel e o app desenham o *x*
    monkeypatch.setenv("WA_IA_PRIMEIRO", "1")
    openai_falso(monkeypatch, rodada(None, BANCO))
    assert wa(uid, FRASE) == CONFIRMA


def test_t4_e_t6_na_web_com_negrito_da_web(web, monkeypatch):
    uid, c = web
    openai_falso(monkeypatch, rodada(None, ("add_launch", {**BANCO[1], "valor": 50.123})),
                 rodada(None, ("add_launch", {**BANCO[1], "categoria": "investimento"})))
    assert chat(c, "gastei 50,123 no mercado no pix", mid()) == tx.valor_casas(50.123).replace(
        "*gastei 50,12 no mercado pelo banco*", "**gastei 50,12 no mercado pelo banco**")
    chat(c, "gastei 50 no mercado #investimento no pix", mid())
    assert chat(c, "sim", mid()) == tx.INTERNA.replace("*em dinheiro*", "**em dinheiro**")


def test_resposta_cartao_de_debito_e_t8_antes_da_confirmacao(web, monkeypatch):
    uid, c = web
    openai_falso(monkeypatch, rodada("Foi em dinheiro vivo ou pelo banco?"), rodada(None, BANCO))
    chat(c, "gastei 50 no mercado", mid())
    assert chat(c, "cartão de débito", mid()).startswith(tx.CARTAO)
    assert pendencia_ia(uid) is None and livro(uid) == []


def test_t16_armada_no_whatsapp_e_confirmada_na_web(web, monkeypatch):
    uid, c = web
    monkeypatch.setenv("WA_IA_PRIMEIRO", "1")
    openai_falso(monkeypatch, rodada(None, BANCO))
    w1 = wamid()
    assert wa(uid, FRASE, w1) == CONFIRMA
    assert "registrada pelo banco" in chat(c, "sim", mid())
    assert wa(uid, FRASE, w1) == t3(1)
    assert valores(uid) == [50.0]


def test_o_mesmo_id_em_dois_usuarios_sao_duas_acoes(web, monkeypatch):
    """O livro é por usuário: o mesmo `client_message_id` vindo de outra conta não colide."""
    uid, c = web
    outro, c2 = conta()
    _bank(outro)
    openai_falso(monkeypatch, rodada(None, BANCO), rodada(None, BANCO))
    m, s = mid(), mid()
    for cliente in (c, c2):
        assert chat(cliente, FRASE, m) == CONFIRMA_WEB
        assert "registrada pelo banco" in chat(cliente, "sim", s)
    assert valores(uid) == [50.0] and valores(outro) == [50.0]



@pytest.mark.parametrize("alvo,nota", [("mercado", "2*3 caixas"), ("*mercado*", None)])
def test_asterisco_do_usuario_sai_como_foi_digitado(web, monkeypatch, alvo, nota):
    """O negrito entra no molde, antes dos campos: o `*` do usuário sai intacto e o negrito do molde fica íntegro."""
    uid, c = web
    args = {**BANCO[1], "alvo": alvo, **({"nota": nota} if nota else {})}
    openai_falso(monkeypatch, rodada(None, ("add_launch", args)))
    r = chat(c, f"gastei 50 no {alvo} {nota or ''} no pix", mid())
    resumo = pendencia_ia(uid)["summary"]
    assert (nota or alvo) in resumo
    assert r == f"🐷 Só confirmando: registrar **{resumo}**? Responde **sim** ou **não**."


@pytest.mark.parametrize("flag", [None, "1"])
def test_confirmacao_de_outra_escrita_da_ia_fica_igual_a_main(k, monkeypatch, flag):
    """A troca é pela origem (payload da declaração), não pelo texto: a meta sai byte a byte como na `main`, com a
    flag desligada e ligada, inclusive com `*` no nome."""
    from core.services.ai_chat import runner
    from tests._ia_falsa_helpers import chamada, com_tools, openai_falso as falso
    if flag:
        monkeypatch.setenv("DECLARACAO_BANCO", flag)
    else:
        monkeypatch.delenv("DECLARACAO_BANCO", raising=False)
    falso(monkeypatch, com_tools(chamada("create_meta", {**VIAGEM, "nome": "*Viagem* 2*3"})))
    r = runner.chat(k, "queria uma meta de 5000 pra viagem", monthly_limit=50, platform="dashboard")
    p = pendencia_ia(k)
    assert p is not None and "*Viagem* 2*3" in p["summary"], r
    assert r == runner._CONFIRMA.format(resumo=p["summary"])
