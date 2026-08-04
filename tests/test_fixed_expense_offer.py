"""
"Virar gasto fixo?": na 2ª vez que uma despesa se repete, o bot oferece
transformá-la em gasto fixo (recorrente). "sim" cria com padrões; "não" descarta;
outra coisa não prende o usuário. Só oferece pra Pro e uma vez por descrição.
"""
from __future__ import annotations

import pytest

import db
import core.handle_incoming as hi
from core.handlers import launches
from core.types import IncomingMessage
from db.recurring import list_recurring_expenses, create_recurring_expense


@pytest.fixture
def pro(monkeypatch):
    monkeypatch.setattr("core.services.plan_service.is_pro", lambda uid: True)


def _pending(uid):
    return db.get_pending_action(uid)


def _is_offer(uid):
    p = _pending(uid)
    return bool(p and p["action_type"] == "fixed_expense_offer")


# --- texto ----------------------------------------------------------------

def test_nao_oferece_na_primeira(user_id, pro):
    r = launches.add(user_id, "gastei 1500 no aluguel", {})
    assert "gasto fixo" not in r.lower()
    assert not _is_offer(user_id)


def test_oferece_na_segunda(user_id, pro):
    launches.add(user_id, "gastei 1500 no aluguel", {})
    r = launches.add(user_id, "gastei 1500 no aluguel", {})
    assert "gasto fixo" in r.lower()
    assert _is_offer(user_id)
    assert _pending(user_id)["payload"]["desc"] == "aluguel"


def test_sim_cria_gasto_fixo(user_id, pro):
    launches.add(user_id, "gastei 1500 no aluguel", {})
    launches.add(user_id, "gastei 1500 no aluguel", {})
    resp = launches.resolve_fixed_expense_offer(user_id, "sim", _pending(user_id))
    assert "gasto fixo criado" in resp.lower()
    recs = list_recurring_expenses(user_id)
    assert len(recs) == 1
    assert recs[0]["name"].lower() == "aluguel"
    assert recs[0]["amount"] == 1500.0
    assert recs[0]["payment_type"] == "account"
    assert recs[0]["frequency"] == "monthly"
    assert not _is_offer(user_id)


def test_nao_descarta_sem_criar(user_id, pro):
    launches.add(user_id, "gastei 1500 no aluguel", {})
    launches.add(user_id, "gastei 1500 no aluguel", {})
    resp = launches.resolve_fixed_expense_offer(user_id, "não", _pending(user_id))
    assert list_recurring_expenses(user_id) == []
    assert not _is_offer(user_id)
    assert "normal" in resp.lower()


def test_resposta_fora_de_contexto_nao_prende(user_id, pro):
    launches.add(user_id, "gastei 1500 no aluguel", {})
    launches.add(user_id, "gastei 1500 no aluguel", {})
    resp = launches.resolve_fixed_expense_offer(user_id, "qual meu saldo", _pending(user_id))
    assert resp is None                      # roteador segue normal
    assert list_recurring_expenses(user_id) == []
    assert not _is_offer(user_id)            # pendência abandonada


def test_nao_oferece_na_terceira(user_id, pro):
    launches.add(user_id, "gastei 1500 no aluguel", {})
    launches.add(user_id, "gastei 1500 no aluguel", {})
    r3 = launches.add(user_id, "gastei 1500 no aluguel", {})
    assert "gasto fixo" not in r3.lower()    # só oferece na 2ª exata


def test_nao_oferece_sem_pro(user_id, monkeypatch):
    monkeypatch.setattr("core.services.plan_service.is_pro", lambda uid: False)
    launches.add(user_id, "gastei 1500 no aluguel", {})
    r = launches.add(user_id, "gastei 1500 no aluguel", {})
    assert "gasto fixo" not in r.lower()


def test_nao_oferece_se_ja_e_gasto_fixo(user_id, pro):
    create_recurring_expense(user_id, "aluguel", 1500, "moradia", 10, "account")
    launches.add(user_id, "gastei 1500 no aluguel", {})
    r = launches.add(user_id, "gastei 1500 no aluguel", {})
    assert "gasto fixo" not in r.lower()


def test_receita_nao_oferece(user_id, pro):
    launches.add(user_id, "recebi 3000 de salario", {})
    r = launches.add(user_id, "recebi 3000 de salario", {})
    assert "gasto fixo" not in r.lower()


# --- áudio ----------------------------------------------------------------

class _Att:
    data = b"x"
    filename = "audio.ogg"
    content_type = "audio/ogg"


@pytest.fixture
def small_uid():
    import uuid as _uuid
    from conftest import _cleanup_user
    uid = int(_uuid.uuid4().int % 1_000_000_000)
    db.ensure_user(uid)
    yield uid
    _cleanup_user(uid)


@pytest.fixture
def audio(monkeypatch):
    monkeypatch.setattr("core.services.plan_service.is_pro", lambda uid: True)

    def _say(uid, phrase):
        monkeypatch.setattr(hi, "transcribe_audio", lambda data, fn: phrase)
        msg = IncomingMessage(platform="whatsapp", user_id=uid, text="",
                              message_id="m", attachments=[_Att()], external_id="e", raw={})
        out = hi._handle_audio(msg, "whatsapp")
        return out[0].text if out else ""

    return _say


def test_audio_oferece_na_segunda(small_uid, audio):
    audio(small_uid, "gastei 1500 no aluguel")
    r = audio(small_uid, "gastei 1500 no aluguel")
    assert "gasto fixo" in r.lower()
    # a oferta sobrevive (não foi sobrescrita pelo undo_audio)
    assert _is_offer(small_uid)
