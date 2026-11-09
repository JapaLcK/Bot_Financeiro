"""PR-B (b) (ADR 0006), M8 rodada 5: nenhum dado do modelo autoriza gravação, nem o payload IDÊNTICO ao confirmado.

Camadas, cada uma com o seu teste (os vermelhos de cada uma, isolada, estão na tabela de controles):
- a marca do turno: só o turno que consumiu a ação no CAS a grava (`decl.autoriza`/`autorizada`, no estado do turno);
- o hash do payload: o que se grava é o que foi congelado ao armar, campo por campo;
- o filtro de "_" do runner e a tentativa única ficam nos testes de `test_declaracao_b_chat_ia_payload.py`.
O cenário do Tester r4: o "sim" S1 consome a ação, o escritor E o `descarta` caem (a ação fica `consumida`), o usuário
manda o gasto de novo e confirma, e a Meta reentrega S1 com o modelo reenviando o payload idêntico do histórico.
"""
from __future__ import annotations

import json
import threading
import uuid
from decimal import Decimal

import pytest

import db
from core.handlers import declaracao_banco as decl
from core.handlers import declaracao_banco_textos as tx
from core.services.ai_chat.tools import declaracao_ia
from tests._chat_ia_apoio import (CONFIRMA, FRASE, T19, ana_ia, banco, declaracoes, ia, livro,  # noqa: F401
                                  pendencia_ia, q, uid_pro, valores, wa, wamid)
from tests._declaracao_b import fala
from tests._ia_falsa_helpers import chamada, com_tools, texto


def _cai(*_a, **_k):
    raise RuntimeError("DB caiu")


def _falha_dupla(uid, monkeypatch, onde, *depois):
    """A 1ª entrega do "sim" consome a ação; a gravação cai (escritor, ou T9 no `preparar`) E o `descarta` também."""
    from db import declaracao_livro as lv
    _falha_dupla.ia = ia(monkeypatch, banco(), *depois)  # o registro do modelo falso, para contar as chamadas
    assert wa(uid, FRASE) == CONFIRMA
    payload, s1 = pendencia_ia(uid)["tool_args"]["_declaracao"], wamid()
    alvo = "add_launch_and_update_balance" if onde == "escritor" else "has_open_finance_connections"
    o1, o2 = getattr(db, alvo), lv.descarta
    monkeypatch.setattr(db, alvo, _cai)
    monkeypatch.setattr(lv, "descarta", _cai)
    wa(uid, "sim", s1)
    monkeypatch.setattr(db, alvo, o1)
    monkeypatch.setattr(lv, "descarta", o2)
    assert [r["estado"] for r in livro(uid) if r["papel"] == "acao"] == ["consumida"] and declaracoes(uid) == []
    return payload, s1


def _reenvia_identico(messages):
    for m in messages:
        if m.get("role") == "tool" and "_declaracao" in (m.get("content") or ""):
            p = json.loads(m["content"])["args"]["_declaracao"]
            return com_tools(chamada("add_launch", {"tipo": "despesa", "alvo": "x", "forma_pagamento": "dinheiro",
                                                    "_declaracao": p}, "c9"))
    return texto("nada")


@pytest.mark.parametrize("onde", ["escritor", "t9"])
def test_reenvio_do_usuario_grava_uma_vez_e_a_reentrega_do_sim_com_o_identico_nao(ana_ia, monkeypatch, onde):
    """O caminho real: a reentrega do S1 nem chega ao modelo (a regra do "sim" visto, A1 e rodada 7); sem ela e sem o
    filtro, a marca do turno ainda recusaria o payload idêntico que o modelo reenviaria."""
    _, s1 = _falha_dupla(ana_ia, monkeypatch, onde, banco(), _reenvia_identico)
    assert wa(ana_ia, FRASE) == CONFIRMA
    assert "registrada pelo banco" in wa(ana_ia, "sim")
    wa(ana_ia, "sim", s1)
    assert valores(ana_ia) == [50.0]


@pytest.mark.parametrize("ordem", ["reenvio_antes", "reentrega_antes"])
def test_a1_o_sim_ja_consumido_que_volta_nao_vai_ao_modelo(ana_ia, monkeypatch, ordem):
    """Decisão do dono (A1): o S1 que já consumiu uma ação que não gravou volta (queda, falha dupla) e recebe o texto neutro
    (D-a) antes do modelo, que armaria outra confirmação com a chave dele; o gasto mandado de novo grava uma vez, nas duas ordens."""
    _, s1 = _falha_dupla(ana_ia, monkeypatch, "escritor", banco())
    reg = _falha_dupla.ia

    def reenvio():
        assert wa(ana_ia, FRASE) == CONFIRMA
        assert "registrada pelo banco" in wa(ana_ia, "sim")  # positivo: "sim" novo, ação nova, grava
    if ordem == "reenvio_antes":
        reenvio()
    chamadas = len(reg.creates)
    assert wa(ana_ia, "sim", s1) == tx.SIM_JA_RECEBIDO and len(reg.creates) == chamadas
    if ordem == "reentrega_antes":
        reenvio()
    assert valores(ana_ia) == [50.0]


@pytest.mark.parametrize("rota", ["/ai/chat", "/ai/chat/stream"])
def test_a1_na_web_o_sim_ja_consumido_que_volta(monkeypatch, rota):
    from tests._ia_stream_helpers import conta, eventos_sse, openai_falso, rodada
    from tests.test_bank_movements import _bank
    from tests.test_senha_obrigatoria import H
    from db import declaracao_livro as lv
    monkeypatch.setenv("DECLARACAO_BANCO", "1")
    uid, c = conta()
    _bank(uid)
    reg = openai_falso(monkeypatch, rodada(None, ("add_launch", {"tipo": "despesa", "valor": 50, "alvo": "mercado",
                                                                 "forma_pagamento": "banco"})))

    def manda(t, ident):
        r = c.post(rota, json={"message": t, "cards": [], "client_message_id": ident}, headers=H)
        return r.json()["reply"] if rota == "/ai/chat" else eventos_sse(r)[-1]["reply"]
    manda(FRASE, uuid.uuid4().hex)
    s1, o1, o2 = uuid.uuid4().hex, db.add_launch_and_update_balance, lv.descarta
    monkeypatch.setattr(db, "add_launch_and_update_balance", _cai)
    monkeypatch.setattr(lv, "descarta", _cai)
    manda("sim", s1)
    monkeypatch.setattr(db, "add_launch_and_update_balance", o1)
    monkeypatch.setattr(lv, "descarta", o2)
    assert manda("sim", s1).endswith(tx.SIM_JA_RECEBIDO.removeprefix("🐷 ")) and len(reg.creates) == 1
    assert valores(uid) == []


def test_o_payload_identico_em_outro_turno_nao_grava(ana_ia, monkeypatch):
    """A marca isolada: a chamada direta pula o filtro; a ação está consumida, o "sim" é o dela e o hash bate."""
    payload, s1 = _falha_dupla(ana_ia, monkeypatch, "escritor")
    with decl.turno("wa", s1):
        decl.registrar_mensagem(ana_ia, "sim")
        assert declaracao_ia.executar(ana_ia, {"_declaracao": payload}) == tx.ACAO_ENCERRADA
    assert valores(ana_ia) == []


def test_a_marca_nao_vaza_entre_turnos_threads_usuarios_nem_com_excecao():
    with decl.turno("web", "a" * 32):
        decl.autoriza(1, "web:k:0")
        assert decl.autorizada(1, "web:k:0") and not decl.autorizada(2, "web:k:0")
        visto = []
        t = threading.Thread(target=lambda: visto.append(decl.autorizada(1, "web:k:0")))
        t.start()
        t.join()
        assert visto == [False]
    assert not decl.autorizada(1, "web:k:0")
    with pytest.raises(RuntimeError):
        with decl.turno("web", "b" * 32):
            decl.autoriza(1, "web:k:0")
            raise RuntimeError("o turno levantou")
    assert decl.ACAO_DO_TURNO.get() is None and not decl.autorizada(1, "web:k:0")


def test_positivo_o_sim_falado_com_pergunta_aberta_da_ia_grava_uma_vez(ana_ia, monkeypatch):
    """O "sim" falado só chega à IA quando há uma PERGUNTA ABERTA da IA (mensagem dela terminando em "?"), não pela
    confirmação da declaração, que não termina em "?": o teste injeta essa pergunta à mão, estado que o fluxo da
    declaração sozinho não produz. Sem ela, o "sim" falado vai ao roteador e nunca confirma (L-b15 do ADR 0006)."""
    ia(monkeypatch, banco())
    assert wa(ana_ia, FRASE) == CONFIRMA
    db.ai_append_message(ana_ia, "assistant", "Confirma esse gasto?")  # a pergunta aberta (injetada): o áudio vai à IA
    assert "registrada pelo banco" in "\n".join(fala(monkeypatch, ana_ia, "sim"))
    assert valores(ana_ia) == [50.0]


# ── o hash, campo por campo: a pendência adulterada entre armar e o "sim" não grava ─────────────────────────────

TROCA = {"valor": 5000, "alvo": "forjado", "nota": "forjada", "data": "2001-01-01", "categoria": "lazer",
         "category_reason": "explicit", "tipo": "receita"}


@pytest.mark.parametrize("campo", list(TROCA))
def test_um_campo_do_payload_trocado_na_pendencia_nao_grava(ana_ia, monkeypatch, campo):
    ia(monkeypatch, banco())
    wa(ana_ia, FRASE)
    q("""update ai_pending_actions set tool_args = jsonb_set(tool_args, %s, %s::jsonb) where user_id=%s returning 1""",
      ("{_declaracao,%s}" % campo, json.dumps(TROCA[campo]), ana_ia))
    assert wa(ana_ia, "sim") == T19
    assert q("select count(*) n from launches where user_id=%s", (ana_ia,))["n"] == 0


def test_a_forma_canonica_do_valor():
    p = {"acao": "wa:k:0", "tipo": "despesa", "alvo": "mercado", "nota": None, "data": "2026-11-03",
         "categoria": "mercado", "category_reason": "local_rule"}
    h = declaracao_ia.hash_do_payload
    assert h({**p, "valor": 12.9}) == h({**p, "valor": Decimal("12.90")}) == h({**p, "valor": 12.90})
    assert h({**p, "valor": 1500.0}) == h({**p, "valor": 1500})
    assert h({**p, "valor": 12.9}) != h({**p, "valor": 12.91})


# ── o argumento sem a ação ──────────────────────────────────────────────────────────────────────────────────────

def test_confirmacao_sem_a_acao_e_recusada_antes_de_gravar(ana_ia):
    with pytest.raises(ValueError, match="DECLARACAO_ARGS_INCOMPATIVEIS"):
        db.add_launch_and_update_balance(ana_ia, "despesa", 50, "mercado", "mercado", "mercado",
                                         declaracao_banco=True, confirmacao="web:" + uuid.uuid4().hex, payload_hash="h")
    assert q("select count(*) n from launches where user_id=%s", (ana_ia,))["n"] == 0


def test_d_e_o_banco_some_entre_o_executar_e_a_gravacao_nao_vira_carteira(ana_ia, monkeypatch):
    """D-e do dono: o último banco desconecta entre a conferência do `executar` e a do `add_from_entities`. A confirmação
    "pelo banco" não autoriza gravar na Carteira: T9 e nada gravado (nem declaração, nem saldo)."""
    import core.handlers.forma_pagamento as fp
    ia(monkeypatch, banco())
    assert wa(ana_ia, FRASE) == CONFIRMA
    respostas = iter([True])  # a 1ª consulta (no `executar`) ainda vê o banco; as seguintes, não
    monkeypatch.setattr(fp, "regra_ativa", lambda _uid: next(respostas, False))
    assert wa(ana_ia, "sim") == tx.SEM_GARANTIA
    assert q("select count(*) n from launches where user_id=%s", (ana_ia,))["n"] == 0
    assert q("select balance from accounts where user_id=%s", (ana_ia,))["balance"] == 200
