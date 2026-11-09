"""Comissão só fica disponível depois de conferida na Stripe (A5), e a limpeza de `no_commission`.

A sequência que o dono exigiu: reembolso total antes do `invoice.paid`, a linha `no_commission` limpa
depois de 30 dias, o `invoice.paid` atrasado. A comissão nunca pode ficar disponível. Rota real,
Postgres real; a Stripe é falsa só na rede (`InvoicePayment.list`, `PaymentIntent.retrieve`,
`Dispute.list`).
"""
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

import core.services.afiliado_reembolso as servico
import db
import db.affiliate_reviews_manutencao as manutencao
from _lifespan_probe import sondar
from db.affiliates import get_affiliate_stats, request_payout
from db.connection import get_conn
from test_afiliado_area import _afiliado, _esperando_lock, _painel_aberto, _vence  # noqa: F401
from test_afiliado_pagantes import AGORA, _indicado
from test_afiliado_pendencia import _quadros, _rev
from test_afiliado_reembolso import _cobranca, _enviar, _logs, mundo  # noqa: F401
from test_billing_webhook_lifecycle import _fake_sub


@pytest.fixture
def fonte(mundo, monkeypatch):
    """A Stripe que a conferência lê. `cobra(fatura, ...)` cria a cobrança paga da fatura."""
    import stripe

    f = SimpleNamespace(cobrancas={}, disputas={}, falha=None, consultas=0)

    def listar(**k):
        if "invoice" not in k:
            mundo.chamadas.append(k)
            return {"data": mundo.pagamentos.get(k["payment"]["payment_intent"], [])}
        f.consultas += 1
        if f.falha:
            raise f.falha
        return {"data": [{"status": "paid", "payment": {"type": "payment_intent", "payment_intent": pi}}
                         for pi, c in f.cobrancas.items() if c["fatura"] == k["invoice"]]}

    def intent(pi, expand, api_key):
        return {"latest_charge": {"id": pi, **{k: v for k, v in f.cobrancas[pi].items() if k != "fatura"}}}

    def cobra(fatura, devolvido=0, amount=100_000, disputed=False):
        f.cobrancas[f"pi_{fatura}"] = {"fatura": fatura, "amount": amount, "amount_refunded": devolvido, "disputed": disputed}

    monkeypatch.setattr(stripe.InvoicePayment, "list", staticmethod(listar))
    monkeypatch.setattr(stripe.PaymentIntent, "retrieve", staticmethod(intent))
    monkeypatch.setattr(stripe.Dispute, "list", staticmethod(lambda charge, limit, api_key: {"data": f.disputas.get(charge, [])}))
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_xxx")
    mundo.subs["sub_c"] = _fake_sub("active")
    f.cobra = cobra
    return f


def _invoice_paid(ind, fatura, valor=100_000):
    r = _enviar("invoice.paid", {"object": "invoice", "id": fatura, "subscription": "sub_c", "amount_paid": valor,
                                 "metadata": {"finbot_user_id": str(ind.uid)}, "lines": {"data": [], "has_more": False}})
    assert r.status_code == 200, r.text


def _comissao_da(fatura):
    with get_conn() as conn:
        return conn.execute("select * from affiliate_commissions where stripe_invoice_id = %s", (fatura,)).fetchone()


def _nova(c):
    return _indicado(c), f"in_{uuid.uuid4().hex[:12]}"


# ── O teste exigido pelo dono ────────────────────────────────────────────────

def test_reembolso_limpeza_invoice_paid_atrasado(fonte):
    c = _afiliado()
    ind, fatura = _nova(c)
    fonte.cobra(fatura, devolvido=100_000)
    assert _enviar("charge.refunded", _cobranca(fatura)).status_code == 200
    assert _rev(fatura)["status"] == "no_commission"
    with get_conn() as conn:  # o relógio: a linha tem 31 dias
        conn.execute("update affiliate_refund_reviews set created_at = now() - interval '31 days'"
                     " where stripe_invoice_id = %s", (fatura,))
        conn.commit()
    assert manutencao.limpar_sem_comissao() >= 1
    assert _rev(fatura) is None
    _invoice_paid(ind, fatura)
    _vence(_comissao_da(fatura)["id"])
    assert _comissao_da(fatura)["status"] == "reversed"
    assert get_affiliate_stats(c.aff["id"])["available_cents"] == 0
    with pytest.raises(ValueError):
        request_payout(c.aff["id"])


# ── Conferência ──────────────────────────────────────────────────────────────

def test_consulta_falha_mantem_pendente(fonte):
    c = _afiliado()
    ind, fatura = _nova(c)
    fonte.cobra(fatura)
    fonte.falha = RuntimeError("stripe fora")
    _invoice_paid(ind, fatura)
    com = _comissao_da(fatura)
    assert com["payment_check_pending"] is True
    _vence(com["id"])
    q = _quadros(c)
    assert (q["held"], q["available"]) == (100, 0)
    with pytest.raises(ValueError):
        request_payout(c.aff["id"])
    fonte.falha = None
    assert servico.conferir_pendentes() >= 1
    assert _comissao_da(fatura)["payment_check_pending"] is False
    assert _quadros(c)["available"] == 100


def test_conferencia_sem_reembolso_libera(fonte):
    """Positivo: sem reembolso a marca sai, e a comissão segue o fluxo normal."""
    c = _afiliado()
    ind, fatura = _nova(c)
    fonte.cobra(fatura)
    _invoice_paid(ind, fatura)
    com = _comissao_da(fatura)
    assert (com["status"], com["payment_check_pending"]) == ("pending", False)
    _vence(com["id"])
    assert request_payout(c.aff["id"])["amount_cents"] == 10_000


def test_conferencia_parcial_vai_para_revisao(fonte):
    c = _afiliado()
    ind, fatura = _nova(c)
    fonte.cobra(fatura, devolvido=30_000)
    _invoice_paid(ind, fatura)
    com = _comissao_da(fatura)
    assert (com["status"], com["payment_check_pending"]) == ("pending", False)
    assert (_rev(fatura)["status"], _rev(fatura)["tipo"]) == ("open", "partial")
    _vence(com["id"])
    assert (_quadros(c)["review"], _quadros(c)["available"]) == (100, 0)


@pytest.mark.parametrize("disputa,status,marca", [
    ("needs_response", "pending", True),
    ("under_review", "pending", True),
    ("lost", "reversed", False),
    ("won", "pending", False),
], ids=["aberta", "em_analise", "perdida", "ganha"])
def test_disputa_na_conferencia(fonte, disputa, status, marca):
    c = _afiliado()
    ind, fatura = _nova(c)
    fonte.cobra(fatura, disputed=True)
    fonte.disputas[f"pi_{fatura}"] = [{"status": disputa, "amount": 100_000}]
    _invoice_paid(ind, fatura)
    com = _comissao_da(fatura)
    assert (com["status"], com["payment_check_pending"]) == (status, marca)


def test_sem_chave_a_marca_fica(fonte, monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY")
    c = _afiliado()
    ind, fatura = _nova(c)
    fonte.cobra(fatura)
    _invoice_paid(ind, fatura)
    assert _comissao_da(fatura)["payment_check_pending"] is True and fonte.consultas == 0
    assert servico.conferir_pendentes() == 0


def test_comissao_com_pendencia_ja_existente_nao_e_marcada(fonte):
    """Com linha da fatura, quem decide é `promover`: sem consulta à Stripe."""
    c = _afiliado()
    ind, fatura = _nova(c)
    assert _enviar("charge.refunded", _cobranca(fatura, devolvido=30_000)).status_code == 200
    _invoice_paid(ind, fatura)
    com = _comissao_da(fatura)
    assert (com["payment_check_pending"], fonte.consultas, _rev(fatura)["status"]) == (False, 0, "open")


# ── Limpeza ──────────────────────────────────────────────────────────────────

def _linha(status, dias, comissao=None, evento=False):
    fatura = f"in_{uuid.uuid4().hex[:12]}"
    with get_conn() as conn:
        rid = conn.execute(
            "insert into affiliate_refund_reviews(stripe_invoice_id, commission_id, tipo, refunded_cents, status, created_at)"
            " values (%s, %s, 'total', 1000, %s, now() - make_interval(days => %s)) returning id",
            (fatura, comissao, status, dias)).fetchone()["id"]
        if evento:
            conn.execute("insert into affiliate_refund_review_events(review_id, action, actor) values (%s, 'reopened', 'sistema')", (rid,))
        conn.commit()
    return fatura


def test_limpeza_criterio(fonte):
    c = _afiliado()
    ind, fatura_c = _nova(c)
    fonte.cobra(fatura_c)
    _invoice_paid(ind, fatura_c)
    antiga = _linha("no_commission", 31)
    fica = {"recente": _linha("no_commission", 29), "com_evento": _linha("no_commission", 31, evento=True),
            "afiliado_excluido": _linha("recovery", 31), "aberta": _linha("open", 31),
            "ligada": _linha("open", 31, comissao=_comissao_da(fatura_c)["id"]),
            # estado que o fluxo não produz hoje; a guarda de `commission_id` segura o futuro
            "no_commission_ligada": _linha("no_commission", 31, comissao=_comissao_da(fatura_c)["id"])}
    assert manutencao.limpar_sem_comissao() >= 1
    assert _rev(antiga) is None
    assert {k: _rev(f) is not None for k, f in fica.items()} == {k: True for k in fica}
    assert manutencao.limpar_sem_comissao() == 0


def test_limpeza_x_invoice_paid(fonte):
    """A limpeza espera a linha que `promover` segura e reavalia o status: linha promovida fica."""
    fatura = _linha("no_commission", 31)
    with ThreadPoolExecutor(1) as pool, get_conn() as segura:
        segura.execute("select id from affiliate_refund_reviews where stripe_invoice_id = %s for update", (fatura,))
        try:
            limpeza = pool.submit(manutencao.limpar_sem_comissao)
            assert _esperando_lock(1)
            segura.execute("update affiliate_refund_reviews set status = 'open' where stripe_invoice_id = %s", (fatura,))
        finally:
            segura.commit()
        limpeza.result(timeout=15)
    assert _rev(fatura)["status"] == "open"


def test_comissoes_antigas_nao_marcadas(fonte):
    """Banco antigo, sem a coluna: depois do `init_db`, a comissão que existia segue disponível."""
    c = _afiliado()
    ind, fatura = _nova(c)
    fonte.cobra(fatura)
    _invoice_paid(ind, fatura)
    _vence(_comissao_da(fatura)["id"])
    with get_conn() as conn:
        conn.execute("alter table affiliate_commissions drop column payment_check_pending")
        conn.commit()
    db.init_db()
    db.init_db()
    assert _comissao_da(fatura)["payment_check_pending"] is False
    assert _quadros(c)["available"] == 100


def test_worker_confere_e_limpa():
    resultado, diagnostico = sondar({
        "limpa": "db.affiliate_reviews_manutencao:limpar_sem_comissao",
        "confere": "core.services.afiliado_reembolso:conferir_pendentes",
        "aviso": "core.services.afiliado_reembolso:reenviar_pendentes",
    })
    assert resultado == {"limpa": True, "confere": True, "aviso": True, "do_disco": []}, diagnostico
