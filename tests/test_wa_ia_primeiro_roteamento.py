"""WA_IA_PRIMEIRO — quem atende a mensagem do WhatsApp (5a do `handle_incoming`).

A IA é trocada por um falso (`core.services.ai_chat.chat`) que registra se foi
chamada com `ia_primeiro=True`; o resto é a conversa real pelo `handle_incoming`,
com banco real.
"""
from __future__ import annotations

import uuid

import pytest

import core.handle_incoming as hi
import db
from core.types import IncomingMessage
from tests._fusao_of_helpers import uid_pro  # noqa: F401 (fixture)
from tests._ia_falsa_helpers import desliga_flag, lancamentos, liga_flag
from tests._pendencia_credito_helpers import diga


@pytest.fixture
def ia(monkeypatch):
    """`resposta` é o que a IA falsa devolve: str, None ou Exception."""
    import core.services.ai_chat as ai_chat_mod
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    estado = {"resposta": "🐷 resposta da IA", "chamadas": []}

    def _fake(user_id, text, **kw):
        estado["chamadas"].append((text, kw.get("ia_primeiro", False)))
        r = estado["resposta"]
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(ai_chat_mod, "chat", _fake)
    return estado


def _primeiro(ia):
    return [t for t, primeiro in ia["chamadas"] if primeiro]


def test_r1_flag_desligada_nao_chama_ia_primeiro(uid_pro, ia, monkeypatch):
    desliga_flag(monkeypatch)
    diga(uid_pro, "gastei 50 no mercado")
    diga(uid_pro, "quanto gastei esse mês?")
    assert _primeiro(ia) == []
    assert len(lancamentos(uid_pro)) == 1


def test_r2_flag_ligada_dois_assuntos_vao_a_ia(uid_pro, ia, monkeypatch):
    liga_flag(monkeypatch)
    r1 = diga(uid_pro, "gastei 50 no mercado")
    r2 = diga(uid_pro, "quanto gastei esse mês?")
    assert _primeiro(ia) == ["gastei 50 no mercado", "quanto gastei esse mês?"]
    assert "resposta da IA" in r1 and "resposta da IA" in r2
    assert lancamentos(uid_pro) == []


def test_r3_id_fora_da_lista_fica_no_roteador(uid_pro, ia, monkeypatch):
    liga_flag(monkeypatch, ids=f"{uid_pro + 1}, 42")
    diga(uid_pro, "gastei 50 no mercado")
    assert _primeiro(ia) == []
    assert len(lancamentos(uid_pro)) == 1


def test_r3b_id_na_lista_vai_a_ia(uid_pro, ia, monkeypatch):
    liga_flag(monkeypatch, ids=f"42,{uid_pro}")
    diga(uid_pro, "gastei 50 no mercado")
    assert _primeiro(ia) == ["gastei 50 no mercado"]


@pytest.mark.parametrize("falha", [None, RuntimeError("caiu")], ids=["none", "levanta"])
def test_r4_r5_ia_desiste_roteador_atende_sem_segunda_ida(uid_pro, ia, monkeypatch, falha):
    liga_flag(monkeypatch)
    ia["resposta"] = falha
    diga(uid_pro, "gastei 50 no mercado")
    # out_of_scope: sem a guarda, o 5b chamaria a IA de novo.
    diga(uid_pro, "qto sobrou esse mes")
    assert len(ia["chamadas"]) == 2, ia["chamadas"]
    assert _primeiro(ia) == ["gastei 50 no mercado", "qto sobrou esse mes"]
    assert len(lancamentos(uid_pro)) == 1


def test_r6_sem_ia_no_plano_nao_chama(uid_pro, ia, monkeypatch):
    import core.services.plan_service as ps
    liga_flag(monkeypatch)
    monkeypatch.setattr(ps, "ai_chat_allowed", lambda uid: False)
    diga(uid_pro, "gastei 50 no mercado")
    assert ia["chamadas"] == []
    assert len(lancamentos(uid_pro)) == 1


def test_r7_pendencia_viva_vence_a_ia(uid_pro, ia, monkeypatch):
    desliga_flag(monkeypatch)
    r = diga(uid_pro, "gastei 50")
    pend = db.get_pending_action(uid_pro)
    assert pend and not db.eh_oferta_de_conveniencia(pend["action_type"]), (r, pend)
    liga_flag(monkeypatch)
    diga(uid_pro, "cinema")
    assert _primeiro(ia) == []
    assert len(lancamentos(uid_pro)) == 1


def test_r8_botao_nao_vai_a_ia(uid_pro, ia, monkeypatch):
    liga_flag(monkeypatch)
    hi.handle_incoming(IncomingMessage(
        platform="whatsapp", user_id=uid_pro, text="gastei 50 no mercado",
        message_id=uuid.uuid4().hex, attachments=[], external_id="", raw={},
    ), de_botao=True)
    assert _primeiro(ia) == []


@pytest.mark.parametrize("texto", [
    "oi", "aluguel 1500 todo mês", "gastei 50 no ifood e 30 no uber",
])
def test_r9_reservados_ficam_no_roteador(uid_pro, ia, monkeypatch, texto):
    liga_flag(monkeypatch)
    diga(uid_pro, texto)
    assert _primeiro(ia) == []


def test_r9_positivo_giria_vai_a_ia(uid_pro, ia, monkeypatch):
    liga_flag(monkeypatch)
    diga(uid_pro, "qto sobrou esse mes")
    assert _primeiro(ia) == ["qto sobrou esse mes"]


def test_r10_sim_com_pendencia_da_ia_segue_o_caminho_de_sempre(uid_pro, ia, monkeypatch):
    liga_flag(monkeypatch)
    db.ai_set_pending_action(uid_pro, "delete_launch", {"launch_id": "#1"}, "apagar o lançamento #1")
    diga(uid_pro, "sim")
    assert ia["chamadas"] == [("sim", False)]


def test_r11_discord_nao_chama_ia_primeiro(uid_pro, ia, monkeypatch):
    liga_flag(monkeypatch)
    hi.handle_incoming(IncomingMessage(
        platform="discord", user_id=uid_pro, text="gastei 50 no mercado",
        message_id=uuid.uuid4().hex, attachments=[], external_id="", raw={},
    ))
    assert _primeiro(ia) == []


_AUSENTE = object()


@pytest.mark.parametrize("ids,uid,esperado", [
    (",", 123, False),
    ("  ", 123, False),
    ("abc", 123, False),
    ("123", 123, True),
    ("123", 456, False),
    ("", 123, True),
    ("", 456, True),
    (_AUSENTE, 123, True),
], ids=["virgula", "espacos", "abc", "123-na-lista", "456-fora", "vazia-123", "vazia-456", "ausente"])
def test_ativo_lista_de_ids_falha_fechada(monkeypatch, ids, uid, esperado):
    from core.services.wa_ia_primeiro import ativo
    monkeypatch.setenv("WA_IA_PRIMEIRO", "1")
    if ids is _AUSENTE:
        monkeypatch.delenv("WA_IA_PRIMEIRO_USER_IDS", raising=False)
    else:
        monkeypatch.setenv("WA_IA_PRIMEIRO_USER_IDS", ids)
    assert ativo(uid) is esperado
