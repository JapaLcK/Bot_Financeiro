"""
Áudio + clarification: quando um áudio deixa uma PERGUNTA pendente (ex.:
"adicione 10 mil" → "onde adicionar?"), o pipeline de áudio NÃO pode
sobrescrever esse pending com o "undo_audio" — senão a resposta seguinte perde
o contexto e a pergunta é esquecida.

Regressão do bug real: áudio "Adicione 10.000" perguntava onde, mas a resposta
"Caixinha Reserva de Emergência" só mostrava a caixinha em vez de depositar.
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


def test_pergunta_do_audio_sobrevive_e_resolve_no_saldo(small_uid, audio):
    r1 = audio(small_uid, "adicione 10 mil")
    assert "onde" in r1.lower()
    # a pergunta NÃO foi sobrescrita pelo undo_audio
    p = db.get_pending_action(small_uid)
    assert p and p["action_type"] == "clarification"

    # responde por áudio → completa como receita no saldo
    r2 = audio(small_uid, "saldo")
    assert "Receita registrada" in r2
    assert db.get_balance(small_uid) == Decimal("10000")


def test_pergunta_do_audio_resolve_na_caixinha(small_uid, audio):
    db.add_launch_and_update_balance(small_uid, "receita", 5000, None, "seed")
    db.create_pocket(small_uid, "viagem")

    audio(small_uid, "adicione 300")             # pergunta onde
    r = audio(small_uid, "caixinha viagem")      # responde o destino
    assert "viagem" in r.lower()
    assert db.get_balance(small_uid) == Decimal("4700")  # 5000 - 300 movido pra caixinha


def test_lancamento_normal_ainda_arma_desfazer(small_uid, audio):
    # regressão: áudio que registra de fato ainda deve armar o "desfazer"
    audio(small_uid, "gastei 50 no mercado")
    p = db.get_pending_action(small_uid)
    assert p and p["action_type"] == "undo_audio"


def test_multiplo_lancamento_arma_desfazer(small_uid, audio):
    audio(small_uid, "gastei 500 no ifood e mais 800 no mercado")
    p = db.get_pending_action(small_uid)
    assert p and p["action_type"] == "undo_audio"
    assert db.get_balance(small_uid) == Decimal("-1300")
