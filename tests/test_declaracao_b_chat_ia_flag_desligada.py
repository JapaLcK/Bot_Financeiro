"""PR-B (b) (ADR 0006): com `DECLARACAO_BANCO` desligada nada muda (o par versionado da sonda `main` x branch do
plano, seção 5). Os mesmos roteiros do chat IA, pelo `handle_incoming` e pelo `/ai/chat`: a resposta é a de hoje
("chegam sozinhos"), nada é armado nem gravado, e o livro fica vazio, inclusive com `client_message_id` e com "sim".
Controle: o último teste liga a flag no mesmo roteiro e o livro deixa de ficar vazio.
"""
from __future__ import annotations

import uuid

import pytest

from tests._chat_ia_apoio import FRASE, banco, declaracoes, ia, livro, pendencia_ia, uid_pro, wa  # noqa: F401
from tests._declaracao_b import q
from tests._ia_falsa_helpers import liga_flag
from tests._ia_stream_helpers import conta, openai_falso, rodada
from tests.test_bank_movements import _bank
from tests.test_senha_obrigatoria import H

HOJE = "🐷 Não registrei: com banco conectado, Pix, cartão e débito chegam sozinhos pelo Open Finance."


@pytest.fixture
def ana_off(uid_pro, monkeypatch):  # noqa: F811
    _bank(uid_pro)
    q("update accounts set balance=200 where user_id=%s returning 1", (uid_pro,))
    monkeypatch.delenv("DECLARACAO_BANCO", raising=False)
    liga_flag(monkeypatch)
    return uid_pro


def test_whatsapp_com_a_flag_desligada_responde_como_hoje_e_nao_anota(ana_off, monkeypatch):
    reg = ia(monkeypatch, banco(), banco())
    assert wa(ana_off, FRASE).startswith(HOJE)
    assert wa(ana_off, "sim")  # sem pendência: o "sim" segue o caminho de sempre e não vai ao livro
    assert wa(ana_off, FRASE).startswith(HOJE)
    assert pendencia_ia(ana_off) is None and declaracoes(ana_off) == [] and livro(ana_off) == []
    assert len(reg.creates) >= 2  # a reentrega chama a IA de novo, como hoje


def test_web_com_client_message_id_e_a_flag_desligada_ignora_o_id(monkeypatch):
    monkeypatch.delenv("DECLARACAO_BANCO", raising=False)
    uid, c = conta()
    _bank(uid)
    openai_falso(monkeypatch, rodada(None, ("add_launch", {"tipo": "despesa", "valor": 50, "alvo": "mercado",
                                                           "forma_pagamento": "banco"})))
    r = c.post("/ai/chat", json={"message": FRASE, "client_message_id": uuid.uuid4().hex}, headers=H)
    assert r.status_code == 200 and r.json()["reply"].startswith(HOJE)
    assert pendencia_ia(uid) is None and livro(uid) == []


def test_controle_com_a_flag_ligada_o_mesmo_roteiro_anota(ana_off, monkeypatch):
    monkeypatch.setenv("DECLARACAO_BANCO", "1")
    ia(monkeypatch, banco())
    assert not wa(ana_off, FRASE).startswith(HOJE)
    assert livro(ana_off) != []
