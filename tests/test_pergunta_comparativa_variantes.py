"""#569: variantes da pergunta comparativa que escapavam da heurística e gravavam dinheiro.

Com "Faltou o valor de *aluguel*" de pé, "eu gastei mais em 2025 ou 2026?" gravava
R$ 2.025 no aluguel; numa conversa nova, "gastei mais nos ultimos 3 meses" gravava
R$ 3. Conversa pelo `handle_incoming` com Postgres real.
"""
from __future__ import annotations

import pytest

import core.handle_incoming as hi
import db
from core.intent_classifier import contains_comparative_question, sem_perguntas_comparativas
from core.types import IncomingMessage
from tests._fusao_of_helpers import ia_fora, manda  # noqa: F401 (fixture)
from tests.test_handle_incoming_routing import _Audio, _msg, _valores, free_small_uid  # noqa: F401 (fixture)
from tests.test_pergunta_comparativa_conta_cartao import (  # noqa: F401 (fixtures)
    _AVISO, contas_pagas, despesas, luz, nubank, uid)
from utils_date import today_tz


@pytest.fixture(autouse=True)
def _sem_banco_conectado(monkeypatch):
    monkeypatch.setattr("core.handlers.forma_pagamento.regra_ativa", lambda u: False)  # Q40


@pytest.mark.parametrize("text,esperado", [
    ("gastei 30 no uber. gastei mais em 2025 ou 2026?", True),
    ("gastei 30 no uber; gastei mais em 2025 ou 2026?", True),
    ("gastei 30 no uber tb gastei mais em 2025 ou 2026?", True),
    ("gastei 30 no uber mas gastei mais em 2025 ou 2026?", True),
    ("gastei 30 no uber gastei mais em 2025 ou 2026", True),
    ("1200. gastei mais que o normal", True),
    # Só o split pega: o pedaço sem verbo herda o do anterior ("gastei mais que o normal...").
    ("gastei 10 no pao, 20 no uber e mais que o normal no bar?", True),
    ("paguei 50 no mercado e 30 a mais que o normal na luz?", True),
    # O "?" e o marcador valem só DEPOIS do verbo.
    ("e ai, tudo certo? gastei muito no bar ontem 80", False),
    ("o normal eh 1200. gastei muito no bar 80", False),
    ("gastei 30 no uber. paguei 50 no mercado", False),
    ("gastei 30 no uber; paguei 50 no mercado", False),
    ("1200, obrigado", False),
])
def test_contains_acha_pergunta_depois_de_qualquer_separador(text, esperado):
    assert contains_comparative_question(text) is esperado


@pytest.mark.parametrize("text", ["gastei " * 10000, "gastei " + "1" * 20000, "gastei " + "1" * 20000 + " x%"],
                         ids=["verbos", "digitos", "digitos_com_pct"])
def test_contains_e_linear_no_tamanho_do_texto(text):
    # Quadrático travava o worker único do webhook: um `is_comparative_question`
    # por verbo (5 s em 28 mil caracteres) e o "%" tentado de cada dígito (>15 s).
    import time
    t0 = time.perf_counter()
    contains_comparative_question(text)
    assert time.perf_counter() - t0 < 2


def _pendencia(uid):
    p = db.get_pending_action(uid)
    return p and p["action_type"]


def _alvo(uid, valor):
    return next(r["alvo"].lower() for r in db.list_launches(uid, limit=5)
                if float(r["valor"]) == valor)


@pytest.mark.parametrize("pergunta", [
    "eu gastei mais em 2025 ou 2026?", "gastei mais em 2025",
    "Gastei 30 no Uber. Gastei mais em 2025 ou 2026?", "gastei mais 30% esse mês?",
    "gastei + em 2025 ou 2026?", "gastamos mais em 2025 ou 2026?", "gastei 30% a mais esse mes?",
])
def test_fila_recusa_e_o_valor_depois_vai_pro_aluguel(free_small_uid, pergunta):
    uid = free_small_uid
    hi.handle_incoming(_msg(uid, "gastei 10 no pao e paguei o aluguel"))
    out = hi.handle_incoming(_msg(uid, pergunta))
    assert _valores(uid) == [10]
    assert "Isso parece uma pergunta" in out[0].text
    assert _pendencia(uid) == "multi_launch_values"
    hi.handle_incoming(_msg(uid, "1200"))
    assert _valores(uid) == [10, 1200]
    assert "aluguel" in _alvo(uid, 1200)


def test_clarification_recusa_e_o_valor_depois_vai_pra_luz(free_small_uid):
    uid = free_small_uid
    hi.handle_incoming(_msg(uid, "paguei a luz"))
    hi.handle_incoming(_msg(uid, "gastei mais em 2025"))
    assert _valores(uid) == []
    assert _pendencia(uid) == "clarification"
    hi.handle_incoming(_msg(uid, "132"))
    assert _valores(uid) == [132]
    assert "luz" in _alvo(uid, 132)


@pytest.mark.parametrize("pergunta", ["gastei mais em 2025", "gastei mais nos ultimos 3 meses"])
def test_conversa_nova_nao_grava(free_small_uid, pergunta):
    hi.handle_incoming(_msg(free_small_uid, pergunta))
    assert _valores(free_small_uid) == []


def test_conversa_nova_ano_como_valor_ainda_grava(free_small_uid):
    hi.handle_incoming(_msg(free_small_uid, "gastei 2025 no notebook"))
    assert _valores(free_small_uid) == [2025]


def test_multi_com_acima_do_normal_grava_os_dois(free_small_uid):
    hi.handle_incoming(_msg(free_small_uid, "gastei 30 no uber e paguei acima do normal 200 na luz"))
    assert _valores(free_small_uid) == [30, 200]


def test_audio_com_fila_de_pe_nao_grava_no_aluguel(pro_small_uid, monkeypatch):
    uid = pro_small_uid
    hi.handle_incoming(_msg(uid, "gastei 10 no pao e paguei o aluguel"))
    assert _pendencia(uid) == "multi_launch_values"
    monkeypatch.setattr(hi, "transcribe_audio",
                        lambda data, fn: "gastei 30 no uber. gastei mais em 2025 ou 2026")
    msg = IncomingMessage(platform="whatsapp", user_id=uid, text="", message_id="m",
                          attachments=[_Audio()], external_id="e", raw={})
    hi._handle_audio(msg, "whatsapp")
    assert _valores(uid) == [10]
    assert _pendencia(uid) == "multi_launch_values"


# ── #569 B3: `sem_perguntas_comparativas` acha a pergunta pela mesma varredura ──

@pytest.mark.parametrize("text,esperado", [
    ("paguei a fatura do nubank. gastei mais em 2025 ou 2026?",
     ("paguei a fatura do nubank", ["gastei mais em 2025 ou 2026?"])),
    ("gastei 30 no uber; gastei mais em 2025 ou 2026?", ("gastei 30 no uber", ["gastei mais em 2025 ou 2026?"])),
    ("gastei 30 no uber tb gastei mais em 2025 ou 2026?", ("gastei 30 no uber", ["gastei mais em 2025 ou 2026?"])),
    ("oi gastei mais em 2025?", ("", ["oi gastei mais em 2025?"])),   # só prefixo antes: nada legítimo
    # O que vem DEPOIS do verbo da pergunta sai junto (decisão do dono).
    ("gastei mais em 2025 ou 2026? e paguei 50 no mercado",
     ("", ["gastei mais em 2025 ou 2026? e paguei 50 no mercado"])),
    # Pedaço do split sem verbo próprio: só o split o acha.
    ("paguei 50 no mercado e 30 a mais que o normal na luz?",
     ("paguei 50 no mercado", ["paguei 30 a mais que o normal na luz?"])),
    ("paguei a luz", ("paguei a luz", [])),
])
def test_sem_perguntas_corta_na_pergunta(text, esperado):
    assert sem_perguntas_comparativas(text) == esperado


@pytest.mark.parametrize("fatura", [200, 3000])
def test_fatura_com_ponto_paga_o_valor_em_aberto_e_avisa(uid, ia_fora, fatura):
    db.add_launch_and_update_balance(uid, "receita", 5000, None, "seed")
    cid = nubank(uid)
    db.add_credit_purchase(uid, cid, fatura, "outros", "compra teste", today_tz())
    r = manda(uid, "paguei a fatura do nubank. gastei mais em 2025 ou 2026?")
    assert despesas(uid) == [float(fatura)] and r.count(_AVISO) == 1, r  # ia pagar R$ 2.025


@pytest.mark.parametrize("separador", [". ", "; ", " tb "])
def test_conta_com_separador_sem_conector_paga_a_luz_e_avisa(uid, ia_fora, separador):
    luz(uid)
    r = manda(uid, f"paguei a luz{separador}gastei mais em 2025 ou 2026?")
    assert contas_pagas(uid) == [150.0] and despesas(uid) == [150.0], r
    assert r.count(_AVISO) == 1, r
