"""Pedaço comparativo de multi-lançamento: não grava, mas AVISA (texto e áudio).

"gastei 30 no uber e gastei mais que devia 50 no bar": a main gravava 30 + 50;
o pulo do pedaço comparativo (para não gravar R$ 2.025 em "... e gastei mais em
2025 ou 2026?") descartava os 50 em silêncio. Decisão do dono: continua sem
gravar, e a resposta diz qual pedaço ficou de fora e como mandar de novo.
"""
from __future__ import annotations

import types

import pytest

import core.handle_incoming as hi
import core.handlers.launches as L
import db
import parsers
from core.types import IncomingMessage
from tests.test_handle_incoming_routing import _Audio, _msg, _valores

# (frase, valores gravados). O pedaço pulado é o que vem depois do 1º " e ".
_ACHADO = [
    ("gastei 30 no uber e gastei mais que devia 50 no bar", [30]),
    ("recebi 600 da mae e gastei mais que o normal 80 no shopping", [600]),
    ("paguei 100 de luz e paguei mais caro que o normal 250 na gasolina", [100]),
    ("gastei 30 no uber e paguei menos que o combinado 80 pro pedreiro", [30]),
    ("gastei 50 no mercado e gastei mais no uber 30 antes do almoço", [50]),
    ("gastei 50 no mercado e gastei muito no bar 80 que fui antes", [50]),
    ("gastei 50 no mercado e gastei muito no bar ontem, uns 80?", [50]),
    ("gastei 50 no mercado e gastei demais no bar 80?", [50]),
    ("gastei 30 no uber e gastei muito ou pouco 50 no mercado", [30]),
    # Todos os pedaços pulados: não cai no single com o texto inteiro (R$ 2.025).
    ("paguei e gastei mais em 2025 ou 2026?", []),
]


def _aviso(pedaco: str) -> str:
    return (f'ℹ️ Não registrei "{pedaco}" porque parece uma pergunta. '
            "Se era gasto, me manda só o valor e o lugar, tipo *gastei 50 no bar*.")


def _aviso_com_pergunta(pedaco: str) -> str:
    # Com a pergunta do valor de pé, mandar o gasto antes de responder gravaria
    # no item da fila (R$ 50 no aluguel) — o aviso manda responder primeiro.
    return (f'ℹ️ Não registrei "{pedaco}" porque parece uma pergunta. '
            "Se era gasto, depois de responder a pergunta acima, "
            "me manda só o valor e o lugar, tipo *gastei 50 no bar*.")


_COM_ALUGUEL = "gastei 30 no uber e paguei o aluguel e gastei mais que devia 50 no bar"
_PERGUNTA_ALUGUEL = "🐷 Faltou o valor de *aluguel*. Quanto foi? (só o número)"


@pytest.fixture
def add_sem_banco(monkeypatch):
    """`launches.add` com o registro e a pendência capturados (sem Postgres)."""
    gravados: list[float] = []
    monkeypatch.setattr(parsers, "infer_category",
                        lambda **k: types.SimpleNamespace(category="outros", reason="stub"))
    monkeypatch.setattr("core.handlers.credit.try_handle_natural_credit_purchase",
                        lambda uid, text: None)
    monkeypatch.setattr(L, "register_if_recurring", lambda *a: None)
    monkeypatch.setattr(L, "_register_parsed",
                        lambda uid, p, part, pl: gravados.append(float(p["valor"])) or "ok")
    monkeypatch.setattr(L.db, "set_pending_action", lambda *a, **k: None)
    return lambda text: (L.add(1, text, {}), gravados)


@pytest.mark.parametrize("text,valores", _ACHADO)
def test_multi_pula_pedaco_comparativo_e_avisa(add_sem_banco, text, valores):
    resposta, gravados = add_sem_banco(text)
    assert gravados == valores
    assert resposta.count(_aviso(text.split(" e ", 1)[1])) == 1


def test_multi_com_pergunta_de_valor_avisa_depois_dela(add_sem_banco):
    resposta, gravados = add_sem_banco(_COM_ALUGUEL)
    assert gravados == [30]
    aviso = _aviso_com_pergunta("gastei mais que devia 50 no bar")
    assert _PERGUNTA_ALUGUEL in resposta and aviso in resposta, resposta
    assert resposta.index(_PERGUNTA_ALUGUEL) < resposta.index(aviso)  # "acima"


@pytest.fixture
def audio_sem_banco(monkeypatch):
    """`_handle_audio` com banco e rota de cada pedaço trocados por dublês."""
    monkeypatch.setattr("core.services.plan_service.feature_enabled", lambda *a: True)
    monkeypatch.setattr(L, "register_if_recurring", lambda *a: None)
    monkeypatch.setattr(hi, "db", types.SimpleNamespace(
        ensure_user=lambda u: None, update_last_activity=lambda u: None,
        latest_launch_id=lambda u: 1, set_pending_action=lambda *a: None,
        get_pending_action=lambda u: None))
    # Pedaço que não vira lançamento ("paguei") devolve o help genérico.
    monkeypatch.setattr(hi, "_process_audio_transaction",
                        lambda uid, part, msg, pl: "🤔 Não entendi exatamente. Posso te ajudar com…")

    def ouvir(fala):
        monkeypatch.setattr(hi, "transcribe_audio", lambda data, fn: fala)
        msg = IncomingMessage(platform="whatsapp", user_id=5, text="", message_id="m",
                              attachments=[_Audio()], external_id="e", raw={})
        return hi._handle_audio(msg, "whatsapp")[0].text
    return ouvir


def test_audio_so_com_aviso_nao_diz_nao_entendi(audio_sem_banco):
    texto = audio_sem_banco("paguei e gastei mais em 2025 ou 2026")
    assert _aviso("gastei mais em 2025 ou 2026") in texto
    assert "Não entendi" not in texto and "não entendi o que registrar" not in texto, texto


def test_conversa_multi_grava_o_legitimo_e_avisa(pro_small_uid):
    uid = pro_small_uid
    out = hi.handle_incoming(_msg(uid, "gastei 30 no uber e gastei mais que devia 50 no bar"))
    assert _valores(uid) == [30]
    assert _aviso("gastei mais que devia 50 no bar") in "\n".join(o.text for o in out)


def test_conversa_aviso_com_pergunta_e_gasto_depois_vai_pro_bar(pro_small_uid):
    uid = pro_small_uid
    out = hi.handle_incoming(_msg(uid, _COM_ALUGUEL))
    texto = "\n".join(o.text for o in out)
    assert _valores(uid) == [30]
    assert "Faltou o valor de *aluguel*" in texto
    assert _aviso_com_pergunta("gastei mais que devia 50 no bar") in texto
    hi.handle_incoming(_msg(uid, "1200"))
    assert _valores(uid) == [30, 1200]
    hi.handle_incoming(_msg(uid, "gastei 50 no bar"))  # ia pro aluguel na fila
    assert _valores(uid) == [30, 50, 1200]
    por_valor = {float(r["valor"]): r["alvo"].lower() for r in db.list_launches(uid, limit=5)}
    assert "aluguel" in por_valor[1200] and "bar" in por_valor[50], por_valor


def test_conversa_multi_so_pergunta_nao_grava_e_avisa(pro_small_uid):
    uid = pro_small_uid
    out = hi.handle_incoming(_msg(uid, "paguei e gastei mais em 2025 ou 2026?"))
    assert db.list_launches(uid, limit=5) == []
    assert _aviso("gastei mais em 2025 ou 2026?") in "\n".join(o.text for o in out)


def test_audio_multi_grava_o_legitimo_e_avisa(pro_small_uid, monkeypatch):
    # Pagante: o áudio já é liberado no plano, sem mock do gate.
    monkeypatch.setattr(hi, "transcribe_audio",
                        lambda data, fn: "gastei 30 no uber e gastei mais que devia 50 no bar")
    msg = IncomingMessage(platform="whatsapp", user_id=pro_small_uid, text="",
                          message_id="m", attachments=[_Audio()], external_id="e", raw={})
    out = hi._handle_audio(msg, "whatsapp")
    assert _valores(pro_small_uid) == [30]
    assert _aviso("gastei mais que devia 50 no bar") in "\n".join(o.text for o in out)


def test_audio_com_pergunta_de_valor_avisa_depois_dela(audio_sem_banco):
    texto = audio_sem_banco(_COM_ALUGUEL)
    aviso = _aviso_com_pergunta("gastei mais que devia 50 no bar")
    assert _PERGUNTA_ALUGUEL in texto and aviso in texto, texto
    assert texto.index(_PERGUNTA_ALUGUEL) < texto.index(aviso)


@pytest.mark.parametrize("fila,depois", [
    ([], ""),
    ([{"desc": "aluguel"}], "depois de responder a pergunta acima, "),
    ([{"desc": "aluguel"}, {"desc": "luz"}], "depois de me passar o valor de *aluguel* e *luz*, "),
    ([{"desc": "a"}, {"desc": "b"}, {"desc": "c"}], "depois de me passar o valor de *a*, *b* e *c*, "),
])
def test_aviso_cita_a_fila(fila, depois):
    assert L._aviso_pergunta_pulada(" x? ", fila) == (
        'ℹ️ Não registrei "x?" porque parece uma pergunta. '
        f"Se era gasto, {depois}me manda só o valor e o lugar, tipo *gastei 50 no bar*.")
