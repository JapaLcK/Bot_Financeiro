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


# ── Depois que a IA desistiu, o roteador não volta ao LLM no mesmo turno ────

@pytest.fixture
def llm_do_roteador(monkeypatch):
    """Conta as idas do roteador ao LLM: tier 3 do classificador e categoria
    por GPT. Nenhuma bate em rede."""
    import ai_router
    import core.intent_classifier as ic
    from core.intent_classifier import IntentResult
    idas = {"tier3": 0, "categoria": 0}

    def tier3(text, user_id=None):
        idas["tier3"] += 1
        return IntentResult(intent="out_of_scope", confidence=0.0)

    def categoria(descricao, *, user_id=None, source="unknown"):
        idas["categoria"] += 1
        return "lazer"

    monkeypatch.setattr(ic, "_classify_with_ai", tier3)
    monkeypatch.setattr(ai_router, "classify_category_with_gpt", categoria)
    return idas


@pytest.mark.parametrize("flag", [True, False], ids=["ia-desistiu", "flag-desligada"])
def test_tier3_do_classificador_so_sem_ia_tentada(uid_pro, ia, llm_do_roteador, monkeypatch, flag):
    (liga_flag if flag else desliga_flag)(monkeypatch)
    ia["resposta"] = None if flag else "🐷 resposta da IA"
    r = diga(uid_pro, "qto sobrou esse mes")
    assert llm_do_roteador["tier3"] == (0 if flag else 1)
    assert r, r                                   # o roteador respondeu algo


@pytest.mark.parametrize("flag", [True, False], ids=["ia-desistiu", "flag-desligada"])
def test_categoria_por_gpt_so_sem_ia_tentada(uid_pro, ia, llm_do_roteador, monkeypatch, flag):
    (liga_flag if flag else desliga_flag)(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")   # habilita o passo de GPT
    ia["resposta"] = None
    diga(uid_pro, "gastei 40 no zé")
    assert llm_do_roteador["categoria"] == (0 if flag else 1)
    assert len(lancamentos(uid_pro)) == 1


def test_sem_llm_nao_vaza_para_o_turno_seguinte(uid_pro, ia, llm_do_roteador, monkeypatch):
    liga_flag(monkeypatch)
    ia["resposta"] = None
    diga(uid_pro, "qto sobrou esse mes")
    desliga_flag(monkeypatch)
    ia["resposta"] = "🐷 resposta da IA"
    diga(uid_pro, "qto sobrou esse mes")
    assert llm_do_roteador["tier3"] == 1


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


_OFERTA = "Você gastou bastante com delivery. Se quiser, posso ajudar a analisar ou a ver onde dá pra cortar!"


@pytest.mark.parametrize("texto", ["Sim", "sim", "pode", "Pode sim", "não"])
def test_r12_sim_a_oferta_sem_pergunta_vai_a_ia(uid_pro, ia, monkeypatch, texto):
    liga_flag(monkeypatch)
    db.ai_append_message(uid_pro, "user", "Onde foi que eu gastei tanto?")
    db.ai_append_message(uid_pro, "assistant", _OFERTA)
    r = diga(uid_pro, texto)
    assert _primeiro(ia) == [texto]
    assert "resposta da IA" in r


@pytest.mark.parametrize("texto", ["sim", "não"])
def test_r13_sim_com_oferta_de_conveniencia_armada_vai_a_ia(uid_pro, ia, monkeypatch, texto):
    liga_flag(monkeypatch)
    db.set_pending_action(uid_pro, "undo_audio", {})  # oferta é consumida no mesmo turno por _send_reply_with_optional_buttons; aqui ela sobrou de pé
    db.ai_append_message(uid_pro, "user", "Onde foi que eu gastei tanto?")
    db.ai_append_message(uid_pro, "assistant", _OFERTA)
    r = diga(uid_pro, texto)
    assert _primeiro(ia) == [texto]
    assert "resposta da IA" in r


def test_r14_sim_com_pendencia_que_nao_e_oferta_nao_chama_a_ia(uid_pro, ia, monkeypatch):
    liga_flag(monkeypatch)
    db.set_pending_action(uid_pro, "credit_card_set_primary", {"card_id": 1})
    diga(uid_pro, "sim")
    assert _primeiro(ia) == []
