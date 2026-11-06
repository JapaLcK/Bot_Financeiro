"""PR-B (b) (ADR 0006), rodadas 7 e 8: o "sim" S1 já visto que não gravou nada recebe o texto neutro (D-a), sempre, antes do modelo e sem
consumir pendência nenhuma, nem a nova (tabela `.time-dev/prb-chat/rodada7-tabela-sim.md`, linhas B1 a B9).

As janelas do Tester r6: S1 foi registrado no livro, mas a falha veio ANTES de vincular a ação (o CAS ou o
`livro.consome` levantam) ou DEPOIS (a gravação falhou). Em cada canal (WhatsApp, Discord, `/ai/chat`, stream) e nas
duas ordens, S1 reentregue dá o texto neutro sem chamar o modelo, e o reenvio do gasto grava exatamente uma vez. A queda real de
processo foi provada numa cópia, com `subprocess`; aqui fica a falha.
Controle negativo: sem a regra (o "sim" visto e não ligado volta ao fluxo), as janelas armam pelo modelo e dobram.
"""
from __future__ import annotations

import random
import uuid

import pytest

import db
from core.handlers import declaracao_banco_textos as tx
from db import declaracao_livro as lv
from tests._chat_ia_apoio import FRASE, banco, dc, ia, livro, pendencia_ia, q, uid_pro, valores, wa, wamid  # noqa: F401
from tests._ia_falsa_helpers import desliga_flag, liga_flag
from tests.test_bank_movements import _bank

CANAIS = ["wa", "dc", "web", "stream"]
BANCO = ("add_launch", {"tipo": "despesa", "valor": 50, "alvo": "mercado", "forma_pagamento": "banco"})


class Canal:
    """Manda pelo canal; o modelo falso SEMPRE pede a declaração de novo (o pior caso: se S1 chegar a ele, arma)."""

    def __init__(self, nome, monkeypatch, uid_pro):  # noqa: F811
        self.nome, self.mp = nome, monkeypatch
        monkeypatch.setenv("DECLARACAO_BANCO", "1")
        if nome in ("web", "stream"):
            from tests._ia_stream_helpers import conta, eventos_sse, openai_falso, rodada
            from tests.test_senha_obrigatoria import H
            self.uid, self.c = conta()
            self.sse, self.H = eventos_sse, H
            self.reg = openai_falso(monkeypatch, *[rodada(None, BANCO) for _ in range(6)])
        else:
            self.uid = uid_pro
            (desliga_flag if nome == "dc" else liga_flag)(monkeypatch)
            self.reg = ia(monkeypatch, *[banco() for _ in range(6)])
        _bank(self.uid)

    def ident(self):
        return {"wa": wamid, "dc": lambda: str(random.randrange(10**17, 10**19))}.get(self.nome, lambda: uuid.uuid4().hex)()

    def manda(self, texto, ident=None):
        ident = ident or self.ident()
        if self.nome == "wa":
            return wa(self.uid, texto, ident)
        if self.nome == "dc":
            return dc(self.uid, texto if texto == "sim" else "piggy " + texto, ident)
        rota = "/ai/chat" if self.nome == "web" else "/ai/chat/stream"
        r = self.c.post(rota, json={"message": texto, "cards": [], "client_message_id": ident}, headers=self.H)
        return r.json()["reply"] if self.nome == "web" else self.sse(r)[-1].get("reply", "")

    def chamadas(self):
        return len(self.reg.creates)


def _ja_recebido(r):
    return r.endswith(tx.SIM_JA_RECEBIDO.removeprefix("🐷 "))


def _nao_ja_recebido(r):
    return r.endswith(tx.NAO_JA_RECEBIDO.removeprefix("🐷 "))


def _confirma(r):
    return "Só confirmando" in r


def _s1_falha(canal, onde):
    """A 1ª entrega de S1: `antes` (o `livro.consome` levanta: S1 visto, não ligado) ou `depois` (a gravação levanta:
    S1 ligado a uma ação que não gravou)."""
    alvo = (lv, "consome") if onde == "antes" else (db, "add_launch_and_update_balance")
    original = getattr(*alvo)

    def cai(*_a, **_k):
        raise RuntimeError("DB caiu")
    s1 = canal.ident()
    canal.mp.setattr(*alvo, cai)
    try:
        canal.manda("sim", s1)
    except Exception:  # noqa: BLE001 - na web a exceção pode subir até o TestClient
        pass
    canal.mp.setattr(*alvo, original)
    assert valores(canal.uid) == []
    return s1


def _s1_volta_e_da_ja_recebido(canal, s1):
    n = canal.chamadas()
    assert _ja_recebido(canal.manda("sim", s1)) and canal.chamadas() == n  # sem modelo


def _reenvio(canal):
    assert _confirma(canal.manda(FRASE))
    assert "registrada pelo banco" in canal.manda("sim")


@pytest.mark.parametrize("onde", ["antes", "depois"])
@pytest.mark.parametrize("ordem", ["reentrega_antes", "reenvio_antes"])
@pytest.mark.parametrize("nome", CANAIS)
def test_s1_visto_que_nao_gravou_e_o_reenvio_grava_uma_vez(uid_pro, monkeypatch, nome, ordem, onde):  # noqa: F811
    canal = Canal(nome, monkeypatch, uid_pro)
    assert _confirma(canal.manda(FRASE))
    s1 = _s1_falha(canal, onde)
    if ordem == "reenvio_antes":
        _reenvio(canal)
    _s1_volta_e_da_ja_recebido(canal, s1)
    if ordem == "reentrega_antes":
        _reenvio(canal)
    _s1_volta_e_da_ja_recebido(canal, s1)  # a retentativa da Meta, depois de tudo
    assert valores(canal.uid) == [50.0]


@pytest.mark.parametrize("onde", ["antes", "depois"])
@pytest.mark.parametrize("nome", CANAIS)
def test_s1_visto_com_a_pendencia_nova_viva_nao_a_toca(uid_pro, monkeypatch, nome, onde):  # noqa: F811
    """Complemento do dono: com a pendência NOVA viva, S1 dá o texto neutro e ela fica intacta (no banco); o "sim" novo grava 1."""
    canal = Canal(nome, monkeypatch, uid_pro)
    assert _confirma(canal.manda(FRASE))
    s1 = _s1_falha(canal, onde)
    assert _confirma(canal.manda(FRASE))  # o reenvio arma W2
    antes = pendencia_ia(canal.uid)
    acao_w2 = q("select chave, estado, payload_hash from ia_declaracao_livro where user_id=%s and papel='acao' "
                "order by id desc limit 1", (canal.uid,))
    _s1_volta_e_da_ja_recebido(canal, s1)
    depois = pendencia_ia(canal.uid)
    assert (depois["created_at"], depois["tool_args"]) == (antes["created_at"], antes["tool_args"])
    assert q("select chave, estado, payload_hash from ia_declaracao_livro where user_id=%s and chave=%s",
             (canal.uid, acao_w2["chave"])) == {**acao_w2, "estado": "armada"}
    assert "registrada pelo banco" in canal.manda("sim")
    assert valores(canal.uid) == [50.0]


def test_s1_visto_com_a_propria_pendencia_viva_ou_expirada_da_ja_recebido(uid_pro, monkeypatch):  # noqa: F811
    """O CAS levanta: S1 visto e a pendência de W1 viva (B1); depois ela expira (B4). Nos dois, o texto neutro sem modelo."""
    canal = Canal("wa", monkeypatch, uid_pro)
    assert _confirma(canal.manda(FRASE))
    original, s1 = db.ai_consume_pending_action, canal.ident()

    def cai(*_a, **_k):
        raise RuntimeError("DB caiu")
    monkeypatch.setattr(db, "ai_consume_pending_action", cai)
    canal.manda("sim", s1)
    monkeypatch.setattr(db, "ai_consume_pending_action", original)
    viva = pendencia_ia(canal.uid)["created_at"]
    _s1_volta_e_da_ja_recebido(canal, s1)
    assert pendencia_ia(canal.uid)["created_at"] == viva
    q("update ai_pending_actions set created_at = now() - interval '2 days' where user_id=%s returning 1", (canal.uid,))
    _s1_volta_e_da_ja_recebido(canal, s1)
    _reenvio(canal)
    assert valores(canal.uid) == [50.0] and [r["estado"] for r in livro(canal.uid) if r["papel"] == "acao"][-1] == "confirmada"


# ── C11 (rodada 9, decisão do dono): a reentrega decide pelo id já registrado, não pelo texto de agora ────────────

OUTROS_TEXTOS = ["sim, gastei 50 no mercado no pix", "Sim, pode.", "sim sim", "pode lançar", "gastei 50"]


@pytest.mark.parametrize("texto", OUTROS_TEXTOS)
@pytest.mark.parametrize("nome", CANAIS)
def test_c11_o_mesmo_id_de_sim_com_outro_texto_e_o_neutro(uid_pro, monkeypatch, nome, texto):  # noqa: F811
    """O cliente (ou a transcrição) reenvia o id de um "sim" já visto com outro texto: o neutro, sem modelo nem roteador;
    o reenvio legítimo grava 1, e uma mensagem NOVA com o gasto continua declarando como sempre."""
    canal = Canal(nome, monkeypatch, uid_pro)
    assert _confirma(canal.manda(FRASE))
    s1 = _s1_falha(canal, "antes")
    _reenvio(canal)
    n = canal.chamadas()
    assert _ja_recebido(canal.manda(texto, s1)) and canal.chamadas() == n
    assert valores(canal.uid) == [50.0]
    assert _confirma(canal.manda(FRASE))  # positivo: id novo, o mesmo gasto, a confirmação de sempre
    assert "registrada pelo banco" in canal.manda("sim") and valores(canal.uid) == [50.0, 50.0]


@pytest.mark.parametrize("texto", OUTROS_TEXTOS)
def test_c11_o_audio_de_sim_retranscrito_e_o_neutro(uid_pro, monkeypatch, texto):  # noqa: F811
    """O áudio S1 "sim" (visto, não ligado: foi ao roteador) reentregue e transcrito de outro jeito: o neutro, sem
    transcrever de novo nem ir ao roteador (que gravaria "sim, gastei 50 no mercado no pix" com a chave do áudio)."""
    from tests._declaracao_b import fala
    canal = Canal("wa", monkeypatch, uid_pro)
    assert _confirma(canal.manda(FRASE))
    s1 = canal.ident()
    fala(monkeypatch, canal.uid, "sim", s1)  # a 1ª transcrição: "sim" (vai ao roteador; fica visto no livro)
    _reenvio(canal)
    n = canal.chamadas()
    assert tx.SIM_JA_RECEBIDO in "\n".join(fala(monkeypatch, canal.uid, texto, s1)) and canal.chamadas() == n
    assert valores(canal.uid) == [50.0]


@pytest.mark.parametrize("nome", CANAIS)
def test_c11_o_id_de_nao_reenviado_como_gasto_nao_grava(uid_pro, monkeypatch, nome):  # noqa: F811
    """O id de um "não" já visto volta como frase de gasto: com a declaração viva, o 4A (a confirmação de novo, sem
    consumir); sem ela, só o aviso. Nunca chega ao roteador nem grava."""
    canal = Canal(nome, monkeypatch, uid_pro)
    assert _confirma(canal.manda(FRASE))
    n1 = canal.ident()
    assert "não fiz nada" in canal.manda("não", n1)
    assert _nao_ja_recebido(canal.manda(FRASE, n1)) and valores(canal.uid) == []  # sem pendência: o aviso
    assert _confirma(canal.manda(FRASE))  # W2 viva
    viva = pendencia_ia(canal.uid)["created_at"]
    assert _confirma(canal.manda(FRASE, n1)) and pendencia_ia(canal.uid)["created_at"] == viva  # 4A
    assert "registrada pelo banco" in canal.manda("sim") and valores(canal.uid) == [50.0]


@pytest.mark.parametrize("texto", ["não", "gastei 50 no mercado no pix"])
@pytest.mark.parametrize("nome", CANAIS)
def test_nao_visto_sem_declaracao_viva_so_avisa(uid_pro, monkeypatch, nome, texto):  # noqa: F811
    """Achado 4 = B (rodada 10): sem declaração viva, o "não" reentregue recebe só o aviso, sem a orientação de conferir o
    extrato (nada foi registrado) e sem ir ao modelo nem ao roteador, com o texto igual ou trocado."""
    canal = Canal(nome, monkeypatch, uid_pro)
    assert _confirma(canal.manda(FRASE))
    n1 = canal.ident()
    assert "não fiz nada" in canal.manda("não", n1)
    n = canal.chamadas()
    r = canal.manda(texto, n1)
    assert _nao_ja_recebido(r) and "extrato" not in r and canal.chamadas() == n
    assert valores(canal.uid) == []


@pytest.mark.parametrize("texto", ["não", "gastei 50 no mercado no pix"])
def test_nao_visto_em_audio_sem_declaracao_viva_so_avisa(uid_pro, monkeypatch, texto):  # noqa: F811
    """O áudio "não" reentregue, transcrito igual ou como gasto: só o aviso; sem transcrever de novo e sem gravar."""
    from tests._declaracao_b import fala
    canal = Canal("wa", monkeypatch, uid_pro)
    assert _confirma(canal.manda(FRASE))
    n1 = canal.ident()
    fala(monkeypatch, canal.uid, "não", n1)  # a 1ª transcrição vai ao roteador (não cancela) e fica vista no livro
    assert "não fiz nada" in canal.manda("não")  # quem cancela é o texto
    n = canal.chamadas()
    assert fala(monkeypatch, canal.uid, texto, n1) == [tx.NAO_JA_RECEBIDO] and canal.chamadas() == n
    assert valores(canal.uid) == []
