"""
Valor recorrente: num lançamento múltiplo, um pedaço sem valor cuja descrição
já se repetiu no histórico com o MESMO valor (ex: "aluguel" sempre R$1500) é
lançado sozinho — em vez de perguntar. Se o valor não é conhecido/estável,
continua perguntando.

Regra (escolha do usuário): só preenche sozinho quando o mesmo valor apareceu em
2+ lançamentos anteriores da descrição (mesmo tipo) e é o dominante (sem empate).
"""
from __future__ import annotations

import pytest

import db
import core.handle_incoming as hi
from core.handlers import launches
from core.types import IncomingMessage


# --- helpers --------------------------------------------------------------

def _valores(uid, limit=20):
    rows = sorted(db.list_launches(uid, limit=limit), key=lambda r: r["id"])
    return [(r["tipo"], float(r["valor"])) for r in rows]


def _seed(uid, tipo, valor, desc, n=1):
    """Cria n lançamentos históricos de `desc` com `valor`."""
    for _ in range(n):
        launches.add_from_entities(uid, tipo=tipo, valor=float(valor),
                                   alvo=desc, nota=desc, platform="whatsapp")


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


# --- unit: infer_recurring_value ------------------------------------------

def test_infer_none_sem_historico(small_uid):
    assert launches.infer_recurring_value(small_uid, "despesa", "aluguel") is None


def test_infer_none_com_apenas_uma_ocorrencia(small_uid):
    _seed(small_uid, "despesa", 1500, "aluguel", n=1)
    assert launches.infer_recurring_value(small_uid, "despesa", "aluguel") is None


def test_infer_devolve_valor_com_duas_ocorrencias(small_uid):
    _seed(small_uid, "despesa", 1500, "aluguel", n=2)
    assert launches.infer_recurring_value(small_uid, "despesa", "aluguel") == 1500.0


def test_infer_none_quando_valores_empatam(small_uid):
    _seed(small_uid, "despesa", 1500, "aluguel", n=2)
    _seed(small_uid, "despesa", 1600, "aluguel", n=2)
    # 2x 1500 e 2x 1600 → empate → ambíguo → pergunta
    assert launches.infer_recurring_value(small_uid, "despesa", "aluguel") is None


def test_infer_desempata_pelo_dominante(small_uid):
    _seed(small_uid, "despesa", 1500, "aluguel", n=3)
    _seed(small_uid, "despesa", 1600, "aluguel", n=1)
    assert launches.infer_recurring_value(small_uid, "despesa", "aluguel") == 1500.0


def test_infer_respeita_tipo(small_uid):
    _seed(small_uid, "despesa", 1500, "aluguel", n=2)
    # mesma descrição mas tipo receita não tem histórico → None
    assert launches.infer_recurring_value(small_uid, "receita", "aluguel") is None


# --- áudio: auto-lança o recorrente ---------------------------------------

def test_audio_auto_lanca_valor_recorrente(small_uid, audio):
    _seed(small_uid, "despesa", 1500, "aluguel", n=2)
    r = audio(small_uid, "gastei 500 no ifood e paguei o aluguel")

    # lançou ifood E aluguel (com o recorrente) — sem perguntar
    assert _valores(small_uid) == [
        ("despesa", 1500.0), ("despesa", 1500.0),  # histórico
        ("despesa", 500.0),                          # ifood
        ("despesa", 1500.0),                         # aluguel auto
    ]
    assert "recorrente" in r.lower()
    assert "quanto" not in r.lower()  # não perguntou
    p = db.get_pending_action(small_uid)
    assert p is None or p["action_type"] != "multi_launch_values"


def test_audio_pergunta_quando_nao_recorrente(small_uid, audio):
    # só 1 ocorrência anterior → não é recorrente ainda → pergunta
    _seed(small_uid, "despesa", 1500, "aluguel", n=1)
    r = audio(small_uid, "gastei 500 no ifood e paguei o aluguel")
    assert "quanto" in r.lower()
    p = db.get_pending_action(small_uid)
    assert p and p["action_type"] == "multi_launch_values"


# --- texto: mesmo comportamento -------------------------------------------

def test_texto_auto_lanca_valor_recorrente(small_uid):
    _seed(small_uid, "receita", 3000, "salario", n=2)
    r = launches.add(small_uid, "gastei 50 no mercado e recebi o salario", {})
    assert "recorrente" in r.lower()
    assert "quanto" not in r.lower()
    assert ("receita", 3000.0) in _valores(small_uid)
    p = db.get_pending_action(small_uid)
    assert p is None or p["action_type"] != "multi_launch_values"
