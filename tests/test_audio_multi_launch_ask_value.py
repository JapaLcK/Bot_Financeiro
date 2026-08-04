"""
Áudio + valor faltante: quando um áudio traz vários lançamentos e um deles vem
SEM valor ("gastei 500 no ifood e paguei o aluguel"), o bot registra o que deu
e PERGUNTA o valor do que faltou — em vez de soltar "não consegui identificar o
valor". Paridade com o texto digitado (test_multi_launch_ask_value.py).

O pipeline de áudio pré-divide a transcrição ANTES do add(), então o ramo de
multi-lançamento do add() nunca dispara; a detecção/enfileiramento precisa
acontecer no próprio _handle_audio.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

import db
import core.handle_incoming as hi
from core.types import IncomingMessage


class _Att:
    data = b"x"
    filename = "audio.ogg"
    content_type = "audio/ogg"


@pytest.fixture
def small_uid():
    """uid < 2bi — `_handle_audio` re-normaliza ids > 2bi (via _internal_user_id),
    então um id pequeno é estável e os asserts leem o MESMO id que o handler grava.
    """
    import uuid as _uuid
    from conftest import _cleanup_user
    uid = int(_uuid.uuid4().int % 1_000_000_000)
    db.ensure_user(uid)
    yield uid
    _cleanup_user(uid)


@pytest.fixture
def audio(monkeypatch):
    """Simula o envio de um áudio: mocka o Whisper (transcrição) e o gate Pro."""
    monkeypatch.setattr("core.services.plan_service.is_pro", lambda uid: True)

    def _say(uid, phrase):
        monkeypatch.setattr(hi, "transcribe_audio", lambda data, fn: phrase)
        msg = IncomingMessage(platform="whatsapp", user_id=uid, text="",
                              message_id="m", attachments=[_Att()], external_id="e", raw={})
        out = hi._handle_audio(msg, "whatsapp")
        return out[0].text if out else ""

    return _say


def _valores(uid, limit=10):
    rows = sorted(db.list_launches(uid, limit=limit), key=lambda r: r["id"])
    return [(r["tipo"], float(r["valor"])) for r in rows]


# --- fluxo principal ------------------------------------------------------

def test_audio_pergunta_valor_do_trecho_sem_numero(small_uid, audio):
    r = audio(small_uid, "gastei 500 no ifood e paguei o aluguel")

    # registrou o ifood
    assert _valores(small_uid) == [("despesa", 500.0)]
    # perguntou o valor do aluguel (não "não consegui identificar o valor")
    assert "aluguel" in r.lower()
    assert "quanto" in r.lower()
    assert "não consegui identificar o valor" not in r.lower()
    # armou o pending certo (sobrevive ao undo_audio)
    p = db.get_pending_action(small_uid)
    assert p and p["action_type"] == "multi_launch_values"
    assert p["payload"]["queue"] == [{"tipo": "despesa", "desc": "aluguel"}]


def test_audio_resposta_por_audio_completa_o_lancamento(small_uid, audio):
    audio(small_uid, "gastei 500 no ifood e paguei o aluguel")
    # responde por ÁUDIO com o número → completa o aluguel herdando tipo/desc
    r = audio(small_uid, "1500")

    assert _valores(small_uid) == [("despesa", 500.0), ("despesa", 1500.0)]
    assert db.get_balance(small_uid) == Decimal("-2000")
    p = db.get_pending_action(small_uid)
    assert p is None or p["action_type"] != "multi_launch_values"


def test_audio_resposta_por_texto_completa_o_lancamento(small_uid, audio):
    from core.intent_classifier import classify
    from core.intent_router import route
    from core.types import IncomingMessage as _Msg

    audio(small_uid, "gastei 500 no ifood e paguei o aluguel")
    # responde por TEXTO → resolve pelo topo do route()
    m = _Msg(platform="whatsapp", user_id=small_uid, text="1500",
             message_id="m2", attachments=[], external_id="e", raw={})
    route(classify("1500", user_id=small_uid), m)

    assert _valores(small_uid) == [("despesa", 500.0), ("despesa", 1500.0)]


def test_audio_receita_herda_tipo(small_uid, audio):
    r = audio(small_uid, "gastei 500 no ifood e recebi de freela")
    assert "freela" in r.lower()
    audio(small_uid, "800")
    assert _valores(small_uid) == [("despesa", 500.0), ("receita", 800.0)]


def test_audio_dois_sem_valor_pergunta_um_a_um(small_uid, audio):
    r = audio(small_uid, "recebi 100 de x e paguei o aluguel e paguei a luz")
    assert _valores(small_uid) == [("receita", 100.0)]
    p = db.get_pending_action(small_uid)
    assert [i["desc"] for i in p["payload"]["queue"]] == ["aluguel", "luz"]

    r1 = audio(small_uid, "1500")             # responde aluguel
    assert "luz" in r1.lower()                # já pergunta o próximo
    audio(small_uid, "200")                   # responde luz
    assert _valores(small_uid) == [
        ("receita", 100.0),
        ("despesa", 1500.0),
        ("despesa", 200.0),
    ]


def test_audio_cancelar_descarta_pendentes(small_uid, audio):
    audio(small_uid, "gastei 500 no ifood e paguei o aluguel")
    r = audio(small_uid, "cancelar")
    assert "aluguel" in r.lower()
    assert _valores(small_uid) == [("despesa", 500.0)]  # nada novo
    p = db.get_pending_action(small_uid)
    assert p is None or p["action_type"] != "multi_launch_values"


def test_audio_resposta_fora_de_contexto_nao_prende(small_uid, audio):
    audio(small_uid, "gastei 500 no ifood e paguei o aluguel")
    # muda de assunto — roteia normal, não fica preso na pergunta
    r = audio(small_uid, "qual meu saldo")
    assert _valores(small_uid) == [("despesa", 500.0)]  # nada registrado a mais
    p = db.get_pending_action(small_uid)
    assert p is None or p["action_type"] != "multi_launch_values"


def test_audio_todos_com_valor_nao_arma_pergunta(small_uid, audio):
    audio(small_uid, "gastei 500 no ifood e mais 800 no mercado")
    assert _valores(small_uid) == [("despesa", 500.0), ("despesa", 800.0)]
    p = db.get_pending_action(small_uid)
    # comportamento atual do áudio: arma o undo, não a pergunta de valor
    assert p is None or p["action_type"] != "multi_launch_values"
