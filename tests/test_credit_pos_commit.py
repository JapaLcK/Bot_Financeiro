"""WhatsApp, cartão de crédito: falha DEPOIS do commit nunca vira "tenta de novo" nem ecoa `{e}`.

A compra/o pagamento já está no banco quando a mensagem de sucesso é montada. Se a montagem
levantava no mesmo `try` da gravação, a resposta era "❌ Erro …: {e}" e o usuário repetia o
pedido (em dobro). A conversa vai pelo `handle_incoming` (`diga`), com banco real; a falha é
injetada DEPOIS da gravação (learn, formatação, rótulo) ou ANTES dela (a própria gravação).

Padrão de referência: `core/services/ai_chat/tools/cards.py::_pay_bill_execute`.
"""
from __future__ import annotations

import logging

import pytest

import db
from core.services.fonte_unica import FonteUnicaOF
# Importado ANTES de qualquer monkeypatch em `db.*` (credit faz `from db import ...` no topo).
import core.handlers.credit as credit
from tests._fusao_of_helpers import ia_fora, uid_pro  # noqa: F401 (fixtures)
from tests._of_cash_helpers import q
from tests._pendencia_credito_helpers import arma_pay_bill_choice, diga
from tests.test_ai_chat_fatura_do_periodo import fatura

SENTINELA = "DETAIL: Key (valor)=(50)"
PROIBIDO = ("Erro", "tenta de novo", "Tenta de novo", "Não consegui", "DETAIL", "Key (valor)")


def _boom(*_a, **_k):
    raise RuntimeError(SENTINELA)


def _compras(uid):
    return q("select count(*) as n from credit_transactions where user_id=%s", (uid,), True)[0]["n"]


def _pagamentos(uid):
    return q("select count(*) as n from launches where user_id=%s and categoria='pagamento_fatura'",
             (uid,), True)[0]["n"]


def _pago(uid):
    return float(q("select coalesce(sum(paid_amount),0) as p from credit_bills where user_id=%s",
                   (uid,), True)[0]["p"])


def _falhas(caplog, op):
    return [r for r in caplog.records
            if r.name == "core.observability" and r.getMessage().startswith(f"{op}: falha")]


def _log_ok(caplog, op, causa="RuntimeError"):
    """Uma linha do op, com a causa, sem o texto da exceção nem traceback."""
    linhas = _falhas(caplog, op)
    assert len(linhas) == 1, [r.getMessage() for r in caplog.records]
    assert f"causa={causa}" in linhas[0].getMessage()
    assert SENTINELA not in linhas[0].getMessage() and linhas[0].exc_info is None


def _sucesso(r):
    assert r.startswith("✅"), r
    assert not any(p in r for p in PROIBIDO), r


@pytest.fixture
def cartao_com_receita(uid_pro, ia_fora):
    c = db.create_card(uid_pro, "Nubank", 10, 17)
    db.add_launch_and_update_balance(uid_pro, "receita", 1000.0, None, "seed")
    return c


# ── DEPOIS do commit ─────────────────────────────────────────────────────────

def test_a_vista_com_learn_quebrado(uid_pro, cartao_com_receita, monkeypatch, caplog):
    monkeypatch.setattr(credit, "learn_from_inference", _boom)
    with caplog.at_level(logging.ERROR):
        r = diga(uid_pro, "gastei 50 no cartao nubank no mercado")
    _sucesso(r)
    assert _compras(uid_pro) == 1
    tx = q("select id from credit_transactions where user_id=%s", (uid_pro,), True)[0]["id"]
    assert f"CC{tx}" in r
    _log_ok(caplog, "add_credit_purchase_msg")


def test_a_vista_com_formatacao_quebrada(uid_pro, cartao_com_receita, monkeypatch, caplog):
    monkeypatch.setattr(credit, "_format_credit_purchase_success", _boom)
    with caplog.at_level(logging.ERROR):
        r = diga(uid_pro, "gastei 50 no cartao nubank no mercado")
    _sucesso(r)
    assert _compras(uid_pro) == 1
    tx = q("select id from credit_transactions where user_id=%s", (uid_pro,), True)[0]["id"]
    assert f"CC{tx}" in r and f"apagar CC{tx}" in r
    _log_ok(caplog, "add_credit_purchase_msg")


@pytest.mark.parametrize("turnos", [
    ["parcelei 300 em 3x no cartao nubank", "TV"],   # pergunta o nome (installment_pending)
    ["parcelei tv de 300 em 3x no cartao nubank"],      # um turno só
])
def test_parcelado_com_learn_quebrado(uid_pro, cartao_com_receita, monkeypatch, caplog, turnos):
    monkeypatch.setattr(credit, "learn_from_inference", _boom)
    with caplog.at_level(logging.ERROR):
        for t in turnos:
            r = diga(uid_pro, t)
    _sucesso(r)
    linhas = q("select distinct group_id from credit_transactions where user_id=%s",
               (uid_pro,), True)
    assert _compras(uid_pro) == 3 and len(linhas) == 1
    assert "PC" + str(linhas[0]["group_id"]).replace("-", "").upper()[:8] in r
    _log_ok(caplog, "add_credit_purchase_installments_msg")


def test_pagar_fatura_com_display_id_quebrado(uid_pro, cartao_com_receita, monkeypatch, caplog):
    fatura(uid_pro, cartao_com_receita, 0, 140)
    monkeypatch.setattr(db, "display_id_for", _boom)
    with caplog.at_level(logging.ERROR):
        r = diga(uid_pro, "pagar fatura nubank")
    _sucesso(r)
    assert "#" not in r  # sem ID: launch_id interno != user_seq
    assert _pagamentos(uid_pro) == 1 and _pago(uid_pro) == 140
    _log_ok(caplog, "pay_bill_msg")


def test_usar_saldo_com_rotulo_quebrado(uid_pro, cartao_com_receita, monkeypatch, caplog):
    fatura(uid_pro, cartao_com_receita, 0, 140)
    monkeypatch.setattr(credit, "_format_bill_label", _boom)
    with caplog.at_level(logging.ERROR):
        r = diga(uid_pro, "usar saldo para pagar nubank")
    _sucesso(r)
    assert "realizado!" in r
    assert _pagamentos(uid_pro) == 1 and _pago(uid_pro) == 140
    _log_ok(caplog, "pay_bill_saldo_msg")


def test_parcelado_com_contrato_do_retorno_quebrado(uid_pro, cartao_com_receita, monkeypatch, caplog):
    """`float(ret[1])` levantando já com as parcelas gravadas: ainda ✅, com o código do grupo."""
    real = credit.add_credit_purchase_installments
    monkeypatch.setattr(credit, "add_credit_purchase_installments", lambda **k: (real(**k)[0], None))
    with caplog.at_level(logging.ERROR):
        r = diga(uid_pro, "parcelei tv de 300 em 3x no cartao nubank")
    _sucesso(r)
    assert _compras(uid_pro) == 3 and "Código: PC" in r and "PC?" not in r
    _log_ok(caplog, "add_credit_purchase_installments_msg", "TypeError")


def test_parcelado_sem_group_id_nao_inventa_codigo(uid_pro, cartao_com_receita, monkeypatch, caplog):
    real = credit.add_credit_purchase_installments
    monkeypatch.setattr(credit, "add_credit_purchase_installments", lambda **k: (real(**k) and {}, 300.0))
    monkeypatch.setattr(credit, "learn_from_inference", _boom)  # força o fallback pós-commit
    with caplog.at_level(logging.ERROR):
        r = diga(uid_pro, "parcelei tv de 300 em 3x no cartao nubank")
    _sucesso(r)
    assert _compras(uid_pro) == 3 and "Código" not in r and "PC?" not in r
    assert "300" in r and "3x" in r
    _log_ok(caplog, "add_credit_purchase_installments_msg")


def test_pay_bill_choice_com_display_id_quebrado(uid_pro, ia_fora, monkeypatch, caplog):
    arma_pay_bill_choice(uid_pro)
    db.add_launch_and_update_balance(uid_pro, "receita", 1000.0, None, "seed")
    monkeypatch.setattr(db, "display_id_for", _boom)
    with caplog.at_level(logging.ERROR):
        r = diga(uid_pro, "1")
    _sucesso(r)
    assert "Pagamento registrado" in r and "#" not in r
    assert _pagamentos(uid_pro) == 1
    _log_ok(caplog, "pay_bill_msg")


# ── ANTES do commit: a gravação falha, nada gravado, mensagem genérica sem a exceção ──

def _pre_compra(uid, c):
    return "gastei 50 no cartao nubank no mercado"


def _pre_parcela(uid, c):
    return "parcelei tv de 300 em 3x no cartao nubank"


def _pre_fatura(uid, c):
    fatura(uid, c, 0, 140)
    return "pagar fatura nubank"


def _pre_saldo(uid, c):
    fatura(uid, c, 0, 140)
    return "usar saldo para pagar nubank"


@pytest.mark.parametrize("op, alvo, texto, quer", [
    ("add_credit_purchase", "add_credit_purchase", _pre_compra, "registrar a compra"),
    ("add_credit_purchase_installments", "add_credit_purchase_installments", _pre_parcela, "registrar a compra"),
    ("pay_bill", "pay_bill_amount", _pre_fatura, "registrar o pagamento da fatura"),
    ("pay_bill_saldo", "pay_bill_amount", _pre_saldo, "registrar o pagamento da fatura"),
])
def test_falha_na_gravacao_nao_ecoa_a_excecao(uid_pro, cartao_com_receita, monkeypatch, caplog, op, alvo, texto, quer):
    monkeypatch.setattr(credit, alvo, _boom)
    msg = texto(uid_pro, cartao_com_receita)
    with caplog.at_level(logging.ERROR):
        r = diga(uid_pro, msg)
    assert r.startswith("❌") and quer in r, r
    assert "DETAIL" not in r and "Key (valor)" not in r, r
    assert (_compras(uid_pro), _pagamentos(uid_pro), _pago(uid_pro)) == (0, 0, 0)
    _log_ok(caplog, op)


# ── Controle positivo: caminho feliz sem patch mantém os textos ──────────────

def test_caminho_feliz_compras_mantem_os_textos(uid_pro, cartao_com_receita):
    assert "Compra no Crédito Registrada" in diga(uid_pro, "gastei 50 no cartao nubank no mercado")
    assert "Parcelamento Registrado" in diga(uid_pro, "parcelei tv de 300 em 3x no cartao nubank")


@pytest.mark.parametrize("texto, frase", [
    ("pagar fatura nubank", "Pagamento registrado"),
    ("usar saldo para pagar nubank", "realizado!"),
])
def test_caminho_feliz_pagamentos_mantem_os_textos(uid_pro, cartao_com_receita, texto, frase):
    fatura(uid_pro, cartao_com_receita, 0, 140)
    assert frase in diga(uid_pro, texto)


# ── FonteUnicaOF é recusa, não falha: texto exato, nada gravado, sem _log_falha ──

@pytest.mark.parametrize("alvo, texto", [
    ("add_credit_purchase", "gastei 50 no cartao nubank no mercado"),
    ("add_credit_purchase_installments", "parcelei tv de 300 em 3x no cartao nubank"),
])
def test_fonte_unica_e_recusa_e_nao_falha(uid_pro, cartao_com_receita, monkeypatch, caplog, alvo, texto):
    def recusa(*_a, **_k):
        raise FonteUnicaOF("cartao")
    monkeypatch.setattr(credit, alvo, recusa)
    with caplog.at_level(logging.ERROR):
        r = diga(uid_pro, texto)
    assert r == str(FonteUnicaOF("cartao")), r
    assert _compras(uid_pro) == 0
    assert not [x for x in caplog.records if x.name == "core.observability" and "falha" in x.getMessage()]
