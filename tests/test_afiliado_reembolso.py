"""Reembolso da Stripe no programa de afiliados: rótulo, comissão, alerta e script.

Rota real `/billing/webhook` com assinatura de verdade: `construct_event` do SDK valida o HMAC
e entrega `StripeObject`, que não tem `.get`. Só a REDE é trocada (`Charge.retrieve`,
`InvoicePayment.list`, `Subscription.retrieve`). Postgres real. O script usa um Stripe falso,
porque nele o que se mede é a leitura de faturas.

O formato real de `InvoicePayment.list(payment=...)` NÃO foi conferido contra a Stripe.
"""
import hashlib
import hmac
import json
import sys
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest
from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import csrf
from _billing_grants_helpers import garantir_system_event_logs
from _afiliado_stripe import simular_stripe
from _stripe_cliente_falso import instalar
from core.services import admin_notify, email_service
import core.services.afiliado_reembolso as modulo_reembolso
from core.services.afiliado_reembolso import tratar_evento
from core.services.cartao_recusado_por_pix import MARCA
from db.affiliate_payments import marcar_reembolso, registrar_pagamento
from db.affiliates import get_affiliate_stats, mark_payout_paid, reject_payout, request_payout
from db.connection import get_conn
from test_afiliado_area import (  # noqa: F401
    SENHA,
    _afiliado,
    _comissao,
    _conta,
    _esperando_lock,
    _me,
    _painel_aberto,
    _sacar,
    _saques,
)
from test_afiliado_pagantes import AGORA, _admin, _indicado, _linhas, _numeros
from test_billing_webhook_lifecycle import _FakeStripe, _fake_sub

SEGREDO = "whsec_teste"
_MUNDO = None  # o `mundo` do teste: `_cobranca` grava a mesma cobrança na Stripe falsa que o webhook lê (#1498)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


@pytest.fixture(autouse=True)
def _logs():
    garantir_system_event_logs()


@pytest.fixture
def mundo(monkeypatch):
    """A rede da Stripe, e os alertas ao admin. `mundo.falha` faz a consulta levantar."""
    import stripe

    m = SimpleNamespace(cobrancas={}, pagamentos={}, chamadas=[], alertas=[], falha=None, subs={})

    def listar(**k):
        m.chamadas.append(k)
        if m.falha:
            raise m.falha
        return {"data": m.pagamentos.get(k["payment"]["payment_intent"], [])}

    def cobranca(cid, **k):
        m.chamadas.append(cid)
        return m.cobrancas[cid]

    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_xxx")
    monkeypatch.setattr(dashboard, "STRIPE_WEBHOOK_SECRET", SEGREDO)
    # `InvoicePayment.list(payment=...)` acha a fatura do reembolso; `(invoice=...)` é a conferência antes de pagar
    m.stripe = simular_stripe(monkeypatch, por_pagamento=listar)
    monkeypatch.setattr(stripe.Charge, "retrieve", staticmethod(cobranca))
    monkeypatch.setattr(stripe.Subscription, "retrieve", staticmethod(lambda sid, **k: m.subs[sid]))
    monkeypatch.setenv("ADMIN_NOTIFY_WEBHOOK_URL", "https://discord.com/api/webhooks/1/x")
    monkeypatch.setattr(admin_notify, "_send", lambda msg: m.alertas.append(msg) or True)
    monkeypatch.setitem(globals(), "_MUNDO", m)
    return m


def _enviar(tipo, objeto, *, segredo=SEGREDO, evento=None):
    corpo = json.dumps({"id": evento or f"evt_{uuid.uuid4().hex}", "object": "event", "type": tipo,
                        "created": int(time.time()), "data": {"object": objeto}}).encode()
    t = int(time.time())
    assinatura = hmac.new(segredo.encode(), f"{t}.{corpo.decode()}".encode(), hashlib.sha256).hexdigest()
    return TestClient(dashboard.app, raise_server_exceptions=False).post(
        "/billing/webhook", content=corpo, headers={"stripe-signature": f"t={t},v1={assinatura}"})


def _cobranca(fatura=None, intent=None, amount=100_000, devolvido=100_000):
    """A cobrança do evento. Com fatura, a Stripe falsa passa a mostrar a mesma: o webhook lê o valor lá."""
    objeto = {"id": f"ch_{uuid.uuid4().hex[:10]}", "object": "charge", "amount": amount,
              "amount_refunded": devolvido, "payment_intent": intent}
    if fatura and _MUNDO is not None:
        _MUNDO.stripe.cobra(fatura, devolvido=devolvido, amount=amount)
    return {**objeto, "invoice": fatura} if fatura else objeto


def _perde(fatura, valor, *, amount=100_000, devolvido=0):
    """A Stripe com a disputa perdida na cobrança da fatura, e o `charge.dispute.closed` dela pelo HTTP."""
    ch = f"ch_{uuid.uuid4().hex[:10]}"
    _MUNDO.cobrancas[ch] = _cobranca(fatura, amount=amount, devolvido=devolvido)
    _MUNDO.stripe.cobra(fatura, devolvido=devolvido, amount=amount, disputed=True)
    _MUNDO.stripe.disputas[fatura] = [{"status": "lost", "amount": valor}]
    r = _enviar("charge.dispute.closed", {"object": "dispute", "status": "lost", "charge": ch, "amount": valor})
    assert r.status_code == 200, r.text


def _reembolsa(fatura, **k):
    r = _enviar("charge.refunded", _cobranca(fatura, **k))
    assert r.status_code == 200, r.text


def _paga(c, reais=100, disponivel=True):
    """Indicado com a comissão da 1ª fatura e o pagamento no registro."""
    com = _comissao(c, reais, disponivel=disponivel)
    assert registrar_pagamento(com.indicado.uid, "stripe", com.fatura, reais * 1000, AGORA)
    return com


def _status(com):
    with get_conn() as conn:
        return conn.execute("select status, payout_id from affiliate_commissions where id = %s", (com.id,)).fetchone()


# ── Assinatura e rótulo ──────────────────────────────────────────────────────

def test_assinatura_invalida_nao_toca_em_nada(mundo):
    c = _afiliado()
    com = _paga(c)
    r = _enviar("charge.refunded", _cobranca(com.fatura), segredo="whsec_outro")
    assert r.status_code == 400
    assert _status(com)["status"] == "pending" and _numeros(c) == (1, 1, 0, 0, 0)


def test_total_com_invoice_no_evento_rotula_e_estorna_a_comissao(mundo):
    c = _afiliado()
    com = _paga(c)
    _reembolsa(com.fatura)
    assert _numeros(c) == (1, 1, 1, 1, 0)  # a conversão fica
    assert _status(com)["status"] == "reversed"
    assert _me(c)["stats"]["generated"] == 0 and _me(c)["commissions"][0]["payment_refund"] == "total"
    assert mundo.chamadas == [] and mundo.alertas == []


@pytest.mark.parametrize("fatura_de", [lambda f: f, lambda f: {"id": f, "object": "invoice"}], ids=["id", "expandida"])
def test_total_sem_invoice_acha_a_fatura_pelo_invoice_payment(mundo, fatura_de):
    c = _afiliado()
    com = _paga(c)
    mundo.pagamentos["pi_1"] = [{"status": "open", "invoice": "in_tentativa"},
                                {"status": "paid", "invoice": fatura_de(com.fatura)}]
    mundo.stripe.cobra(com.fatura, devolvido=100_000)
    _reembolsa(None, intent="pi_1")
    assert mundo.chamadas == [{"payment": {"type": "payment_intent", "payment_intent": "pi_1"}, "limit": 100}]
    assert _numeros(c) == (1, 1, 1, 1, 0)


def test_parcial_rotula_nao_estorna_e_avisa(mundo):
    c = _afiliado()
    com = _paga(c)
    _reembolsa(com.fatura, devolvido=30_000)
    assert _numeros(c) == (1, 1, 0, 0, 1)
    assert _status(com)["status"] == "pending"
    assert len(mundo.alertas) == 1 and "reembolso parcial" in mundo.alertas[0]


def test_parciais_que_somam_o_total_viram_total(mundo):
    c = _afiliado()
    com = _paga(c)
    _reembolsa(com.fatura, devolvido=30_000)
    _reembolsa(com.fatura, devolvido=100_000)
    assert _numeros(c) == (1, 1, 1, 1, 0)
    assert _status(com)["status"] == "reversed"


def test_reentrega_nao_duplica_nem_repete_alerta(mundo):
    c = _afiliado()
    com = _paga(c)
    request_payout(c.aff["id"])
    objeto = _cobranca(com.fatura)
    for _ in range(3):
        assert _enviar("charge.refunded", objeto, evento="evt_igual").status_code == 200
    assert _numeros(c) == (1, 1, 1, 1, 0)
    assert len(mundo.alertas) == 1


def test_disputa_perdida_e_total_ou_parcial_pelo_valor(mundo):
    c = _afiliado()
    inteira, metade = _paga(c), _paga(c)
    _perde(inteira.fatura, 100_000)
    _perde(metade.fatura, 40_000)
    assert _numeros(c) == (2, 2, 1, 1, 1)
    assert _status(inteira)["status"] == "reversed" and _status(metade)["status"] == "pending"


def test_disputa_perdida_do_restante_vira_total_e_estorna(mundo):
    c = _afiliado()
    com = _paga(c)
    _reembolsa(com.fatura, devolvido=30_000)
    _perde(com.fatura, 70_000, devolvido=30_000)
    assert _numeros(c) == (1, 1, 1, 1, 0)
    assert _status(com)["status"] == "reversed"


@pytest.mark.parametrize("status", ["won", "warning_closed", "under_review"])
def test_disputa_que_nao_foi_perdida_nao_faz_nada(mundo, status):
    c = _afiliado()
    com = _paga(c)
    mundo.cobrancas["ch_w"] = _cobranca(com.fatura)
    r = _enviar("charge.dispute.closed", {"object": "dispute", "status": status, "charge": "ch_w", "amount": 100_000})
    assert r.status_code == 200
    assert mundo.chamadas == [] and _numeros(c) == (1, 1, 0, 0, 0)


def test_sem_fatura_e_fora_do_registro_sao_no_op(mundo):
    c = _afiliado()
    com = _paga(c)
    mundo.pagamentos["pi_vazio"] = []
    eventos = {"sem_nada": _cobranca(None), "fora_do_registro": _cobranca("in_de_outro_lugar"),
               "sem_pagamento": _cobranca(None, intent="pi_vazio")}
    prefixo = uuid.uuid4().hex
    for nome, objeto in eventos.items():
        assert _enviar("charge.refunded", objeto, evento=f"evt_{prefixo}_{nome}").status_code == 200
    assert _numeros(c) == (1, 1, 0, 0, 0) and _status(com)["status"] == "pending"
    with get_conn() as conn:
        logados = conn.execute("select details->>'event_id' as evento from system_event_logs"
                               " where event_type = 'affiliate_refund_sem_fatura' and details->>'event_id' like %s",
                               (f"evt_{prefixo}%",)).fetchall()
    assert sorted(x["evento"] for x in logados) == [f"evt_{prefixo}_sem_nada", f"evt_{prefixo}_sem_pagamento"]


def test_falha_da_stripe_vira_5xx_e_a_reentrega_conclui(mundo):
    c = _afiliado()
    com = _paga(c)
    mundo.pagamentos["pi_f"] = [{"status": "paid", "invoice": com.fatura}]
    objeto = _cobranca(None, intent="pi_f")
    mundo.stripe.cobra(com.fatura, devolvido=100_000)
    mundo.falha = RuntimeError("stripe fora do ar")
    assert _enviar("charge.refunded", objeto).status_code == 500
    assert _numeros(c) == (1, 1, 0, 0, 0) and _status(com)["status"] == "pending"
    mundo.falha = None
    assert _enviar("charge.refunded", objeto).status_code == 200
    assert _numeros(c) == (1, 1, 1, 1, 0) and _status(com)["status"] == "reversed"


def test_falha_no_rotulo_repete_e_nao_perde_nada(mundo, monkeypatch):
    c = _afiliado()
    com = _paga(c)
    objeto = _cobranca(com.fatura)
    import db.affiliate_reviews as modulo
    with monkeypatch.context() as m:
        m.setattr(modulo, "_rotular", lambda *a: 1 / 0)
        assert _enviar("charge.refunded", objeto).status_code == 500
    assert _status(com)["status"] == "pending"  # rótulo e estorno na mesma transação
    assert _enviar("charge.refunded", objeto).status_code == 200
    assert _numeros(c) == (1, 1, 1, 1, 0) and _status(com)["status"] == "reversed"


# ── Regra única no webhook (#1498) ───────────────────────────────────────────

def _pendencia(fatura):
    with get_conn() as conn:
        return conn.execute("select tipo, refunded_cents, status from affiliate_refund_reviews where stripe_invoice_id = %s",
                            (fatura,)).fetchone()


def _marcada(com):
    with get_conn() as conn:
        return conn.execute("select payment_check_pending from affiliate_commissions where id = %s",
                            (com.id,)).fetchone()["payment_check_pending"]


def _reembolso_com_disputa_na_stripe(mundo, fatura, devolvido):
    """O `charge.refunded` de agora, com a Stripe ainda mostrando a disputa de antes na cobrança."""
    objeto = _cobranca(fatura, devolvido=devolvido)
    mundo.stripe.cobra(fatura, devolvido=devolvido, disputed=True)
    assert _enviar("charge.refunded", objeto).status_code == 200


def test_disputa_perdida_e_depois_reembolso_do_restante_somam_total(mundo):
    """Caso A da issue: disputa perdida de R$ 30, depois reembolso dos R$ 70. Pelo evento era parcial de R$ 70."""
    c = _afiliado()
    com = _paga(c)
    _perde(com.fatura, 30_000)
    assert dict(_pendencia(com.fatura)) == {"tipo": "partial", "refunded_cents": 30_000, "status": "open"}
    _reembolso_com_disputa_na_stripe(mundo, com.fatura, 70_000)
    assert dict(_pendencia(com.fatura)) == {"tipo": "total", "refunded_cents": 100_000, "status": "resolved"}
    assert _status(com)["status"] == "reversed"
    assert _me(c)["commissions"][0]["payment_refund"] == "total"


def test_disputa_perdida_e_depois_reembolso_menor_somam_total(mundo):
    """Caso B da issue: disputa perdida de R$ 70, depois reembolso de R$ 30. Pelo evento, 30 não era fato novo."""
    c = _afiliado()
    com = _paga(c)
    _perde(com.fatura, 70_000)
    _reembolso_com_disputa_na_stripe(mundo, com.fatura, 30_000)
    assert dict(_pendencia(com.fatura)) == {"tipo": "total", "refunded_cents": 100_000, "status": "resolved"}
    assert _status(com)["status"] == "reversed"


def test_a_stripe_vence_o_evento(mundo):
    """O evento diz R$ 100 devolvidos e a Stripe diz R$ 30: vale a Stripe, parcial em revisão, sem estorno."""
    c = _afiliado()
    com = _paga(c)
    objeto = _cobranca(com.fatura)
    mundo.stripe.cobra(com.fatura, devolvido=30_000)
    assert _enviar("charge.refunded", objeto).status_code == 200
    assert dict(_pendencia(com.fatura)) == {"tipo": "partial", "refunded_cents": 30_000, "status": "open"}
    assert _status(com)["status"] == "pending"
    assert len(mundo.alertas) == 1 and "reembolso parcial" in mundo.alertas[0]


def test_disputa_aberta_na_fatura_marca_e_o_worker_conclui(mundo):
    """P1 = A: a Stripe ainda não confirma. 200, comissão "em conferência", sem pendência; o worker refaz."""
    c = _afiliado()
    com = _paga(c)
    mundo.stripe.disputas[com.fatura] = [{"status": "needs_response", "amount": 70_000}]
    _reembolso_com_disputa_na_stripe(mundo, com.fatura, 30_000)
    assert _marcada(com) and _pendencia(com.fatura) is None and _status(com)["status"] == "pending"
    assert _me(c)["commissions"][0]["checking"] is True and _me(c)["stats"]["available"] == 0
    mundo.stripe.disputas[com.fatura] = [{"status": "lost", "amount": 70_000}]
    modulo_reembolso.conferir_pendentes()
    assert not _marcada(com) and _status(com)["status"] == "reversed"
    assert dict(_pendencia(com.fatura)) == {"tipo": "total", "refunded_cents": 100_000, "status": "resolved"}


def test_worker_nao_apaga_a_marca_que_o_webhook_pos_depois_da_leitura(mundo, monkeypatch):
    """R5 (#1498): o worker lê a Stripe sem reembolso; antes de liberar, o webhook marca (R$ 30 devolvidos,
    R$ 70 em disputa aberta). A marca nova fica; com a disputa ganha, a passada seguinte grava a parcial."""
    c = _afiliado()
    com = _paga(c)
    modulo_reembolso.marcar_conferencia([com.id])
    real = modulo_reembolso.liberar_conferencia

    def webhook_no_meio(commission_id, lido):
        objeto = _cobranca(com.fatura, devolvido=30_000)
        mundo.stripe.cobra(com.fatura, devolvido=30_000, disputed=True)
        mundo.stripe.disputas[com.fatura] = [{"status": "needs_response", "amount": 70_000}]
        assert _enviar("charge.refunded", objeto).status_code == 200
        return real(commission_id, lido)

    mundo.stripe.cobra(com.fatura, devolvido=0)
    with monkeypatch.context() as m:
        m.setattr(modulo_reembolso, "liberar_conferencia", webhook_no_meio)
        modulo_reembolso.conferir_pendentes()
    assert _marcada(com) and _me(c)["stats"]["available"] == 0 and _pendencia(com.fatura) is None
    mundo.stripe.disputas[com.fatura] = [{"status": "won", "amount": 70_000}]
    modulo_reembolso.conferir_pendentes()
    assert not _marcada(com) and dict(_pendencia(com.fatura)) == {"tipo": "partial", "refunded_cents": 30_000, "status": "open"}


def test_marca_do_webhook_tira_a_espera_de_um_adiamento_antigo(mundo):
    """Comissão adiada há 50 min (disputa aberta) recebe a marca do webhook: entra como nunca tentada, e a
    próxima passada do worker confere já, sem esperar o resto da hora (#1498)."""
    c = _afiliado()
    com = _paga(c)
    with get_conn() as conn:
        conn.execute("update affiliate_commissions set payment_check_pending = true, payment_check_reason = 'disputa_aberta',"
                     " payment_check_tried_at = now() - interval '50 minutes' where id = %s", (com.id,))
        conn.commit()
    objeto = _cobranca(com.fatura)
    mundo.stripe.cobra(com.fatura, devolvido=0)  # a Stripe ainda não mostra o reembolso
    assert _enviar("charge.refunded", objeto).status_code == 200
    mundo.stripe.cobra(com.fatura, devolvido=100_000)
    modulo_reembolso.conferir_pendentes()
    assert _status(com)["status"] == "reversed" and not _marcada(com)


def test_comissao_estornada_nao_e_marcada(mundo):
    """M1: a marca do webhook não pega comissão já estornada (ela nunca mais sai)."""
    c = _afiliado()
    com = _paga(c)
    _reembolsa(com.fatura)
    assert _status(com)["status"] == "reversed"
    objeto = _cobranca(com.fatura)
    mundo.stripe.cobra(com.fatura, devolvido=0)
    assert _enviar("charge.refunded", objeto).status_code == 200
    assert not _marcada(com)


def _sem_confirmacao(mundo, caso, fatura):
    import stripe
    if caso == "payment_record":
        mundo.stripe.payment_record.add(fatura)
    elif caso == "stripe_404":
        mundo.stripe.falha = stripe.InvalidRequestError("No such invoice " + fatura, "invoice", http_status=404)
    elif caso == "stripe_403":
        mundo.stripe.falha = stripe.PermissionError("sem acesso a " + fatura, http_status=403)
    else:
        mundo.stripe.cobra(fatura, devolvido=0)


@pytest.mark.parametrize("caso,motivo", [("payment_record", "forma_inesperada"), ("stripe_404", "stripe_404"),
                                         ("stripe_403", "stripe_403"), ("zero", "sem_devolucao")])
def test_fatura_sem_confirmacao_marca_e_responde_200(mundo, caso, motivo):
    c = _afiliado()
    com = _paga(c)
    objeto = _cobranca(com.fatura)
    _sem_confirmacao(mundo, caso, com.fatura)
    evento = f"evt_{uuid.uuid4().hex}"
    assert _enviar("charge.refunded", objeto, evento=evento).status_code == 200
    assert _marcada(com) and _pendencia(com.fatura) is None and _status(com)["status"] == "pending"
    assert _me(c)["commissions"][0]["checking"] is True
    with get_conn() as conn:
        logs = conn.execute("select details from system_event_logs where event_type = 'affiliate_commission_check_pending'"
                            " and (details->>'commission_id')::bigint = %s", (com.id,)).fetchall()
    assert [x["details"] for x in logs] == [{"commission_id": com.id, "motivo": motivo, "event_id": evento}]
    if caso == "zero":  # o reembolso não se confirmou: o worker libera
        modulo_reembolso.conferir_pendentes()
        assert not _marcada(com) and _status(com)["status"] == "pending" and _pendencia(com.fatura) is None


def test_sem_confirmacao_e_sem_comissao_so_loga_sem_a_fatura(mundo):
    fatura = f"in_{uuid.uuid4().hex[:12]}"
    objeto = _cobranca(fatura)
    mundo.stripe.cobra(fatura, devolvido=0)
    evento = f"evt_{uuid.uuid4().hex}"
    assert _enviar("charge.refunded", objeto, evento=evento).status_code == 200
    with get_conn() as conn:
        logs = conn.execute("select details from system_event_logs where event_type = 'affiliate_refund_check_pending'"
                            " and details->>'event_id' = %s", (evento,)).fetchall()
    assert [x["details"] for x in logs] == [{"event_id": evento, "motivo": "sem_devolucao"}]
    assert _pendencia(fatura) is None


def test_queda_da_stripe_na_regra_vira_5xx_sem_marca_e_a_reentrega_conclui(mundo):
    """Positivo do P1: queda não é "sem confirmação". 500, nada gravado, nenhuma marca; a reentrega conclui."""
    import stripe
    c = _afiliado()
    com = _paga(c)
    objeto = _cobranca(com.fatura)
    mundo.stripe.falha = stripe.APIConnectionError("stripe fora do ar")
    assert _enviar("charge.refunded", objeto).status_code == 500
    assert not _marcada(com) and _pendencia(com.fatura) is None and _status(com)["status"] == "pending"
    mundo.stripe.falha = None
    assert _enviar("charge.refunded", objeto).status_code == 200
    assert _status(com)["status"] == "reversed" and not _marcada(com)


def test_conversa_disputa_reembolso_e_parcial_de_outro_afiliado(mundo):
    """Pelo HTTP, dois afiliados: `invoice.paid` (A5) → disputa perdida de R$ 30 → reembolso de R$ 70 na
    fatura de A, e um parcial na de B. Cada um vê só o seu; a fatura isola, sem cruzar afiliado."""
    a, b = _afiliado(), _afiliado()
    mundo.subs["sub_c"] = _fake_sub("active")
    faturas = {}
    for c in (a, b):
        ind, fatura = _indicado(c), f"in_{uuid.uuid4().hex[:12]}"
        r = _enviar("invoice.paid", {"object": "invoice", "id": fatura, "subscription": "sub_c", "amount_paid": 100_000,
                                     "metadata": {"finbot_user_id": str(ind.uid)}, "lines": {"data": [], "has_more": False}})
        assert r.status_code == 200, r.text
        faturas[c.aff["code"]] = fatura
    fa, fb = faturas[a.aff["code"]], faturas[b.aff["code"]]
    assert [x["checking"] for x in _me(a)["commissions"] + _me(b)["commissions"]] == [False, False]
    _perde(fa, 30_000)
    _reembolso_com_disputa_na_stripe(mundo, fa, 70_000)
    _reembolsa(fb, devolvido=30_000)
    ma, mb = _me(a), _me(b)
    assert ma["stats"]["generated"] == 0 and [x["status"] for x in ma["commissions"]] == ["reversed"]
    assert ma["stats"]["refunds"] == {"total": 1, "partial": 0}
    assert mb["stats"]["generated"] == 100 and [x["status"] for x in mb["commissions"]] == ["pending"]
    assert mb["stats"]["refunds"] == {"total": 0, "partial": 1}
    assert dict(_pendencia(fb)) == {"tipo": "partial", "refunded_cents": 30_000, "status": "open"}


# ── Comissão (D9) ────────────────────────────────────────────────────────────

def test_em_saque_nao_muda_e_avisa(mundo):
    c = _afiliado()
    com = _paga(c)
    saque = request_payout(c.aff["id"])
    _reembolsa(com.fatura)
    assert _status(com) == {"status": "pending", "payout_id": saque["id"]}
    assert [p["status"] for p in _saques(c)] == ["requested"]
    assert _numeros(c) == (1, 1, 1, 1, 0)
    assert len(mundo.alertas) == 1 and f"saque `{saque['id']}`" in mundo.alertas[0]


def test_ja_paga_nao_muda_e_avisa(mundo):
    c = _afiliado()
    com = _paga(c)
    assert mark_payout_paid(request_payout(c.aff["id"])["id"])
    _reembolsa(com.fatura)
    assert _status(com)["status"] == "paid"
    assert get_affiliate_stats(c.aff["id"])["paid_cents"] == 100_00
    assert len(mundo.alertas) == 1


def test_saque_rejeitado_depois_do_reembolso_nao_devolve_a_comissao(mundo, monkeypatch):
    """Pelas rotas: o reembolso cai com a comissão em saque, o admin rejeita, e ela não pode
    voltar a ser sacável."""
    c = _afiliado()
    com = _paga(c)
    pedido = _sacar_rota(c)
    _reembolsa(com.fatura)
    assert _status(com)["status"] == "pending"
    admin = _admin(monkeypatch)
    r = admin.post(f"/admin/api/affiliates/payouts/{pedido}/reject", headers=csrf(admin), json={"note": "reembolso"})
    assert r.status_code == 200, r.text
    assert _status(com) == {"status": "reversed", "payout_id": None}
    s = _me(c)["stats"]
    assert (s["available"], s["requested"], s["generated"], s["paying_clients"]) == (0, 0, 0, 1)
    assert _sacar(c).status_code == 400


def test_rejeicao_estorna_so_a_comissao_reembolsada(mundo):
    c = _afiliado()
    reembolsada, parcial, intacta = _paga(c, 60), _paga(c, 50), _paga(c, 40)
    saque = request_payout(c.aff["id"])["id"]
    _reembolsa(reembolsada.fatura, amount=60_000, devolvido=60_000)
    _reembolsa(parcial.fatura, amount=50_000, devolvido=1_000)
    assert reject_payout(saque)
    assert _status(reembolsada)["status"] == "reversed"
    assert _status(parcial) == _status(intacta) == {"status": "pending", "payout_id": None}
    s = _me(c)["stats"]
    assert (s["available"], s["review"]) == (40, 50)  # a parcial volta para a revisão, não para o saldo


def test_rejeicao_sem_reembolso_devolve_tudo(mundo):
    c = _afiliado()
    com = _paga(c)
    assert reject_payout(request_payout(c.aff["id"])["id"])
    assert _status(com) == {"status": "pending", "payout_id": None}
    assert _me(c)["stats"]["available"] == 100


def test_rejeicao_nao_le_o_rotulo_de_outro_afiliado(mundo):
    """Linha do registro em referral de B com a fatura de A: não é do dono do saque."""
    a, b = _afiliado(), _afiliado()
    com = _comissao(a, 100)
    registrar_pagamento(_indicado(b).uid, "stripe", com.fatura, 100_000, AGORA)
    assert marcar_reembolso("stripe", com.fatura, "total", AGORA)
    assert reject_payout(request_payout(a.aff["id"])["id"])
    assert _status(com) == {"status": "pending", "payout_id": None}


def test_reembolso_no_meio_da_rejeicao_espera_a_trava(mundo, monkeypatch):
    """O webhook roda depois do `update` da rejeição, que já usou o rótulo velho, e antes do
    commit. Sem a trava em `reverse_commission`, ele vê a comissão ainda no saque e sai sem
    esperar. Com ela, espera o commit e acha a comissão solta."""
    c = _afiliado()
    com = _paga(c)
    saque = request_payout(c.aff["id"])["id"]
    original = psycopg.Cursor.execute
    com_pool = ThreadPoolExecutor(1)
    pendente = []

    def executa(cur, query, *a, **k):
        resposta = original(cur, query, *a, **k)
        if "then 'reversed' else" in str(query) and not pendente:
            pendente.append(com_pool.submit(tratar_evento, _evento(com.fatura)))
            fim = time.monotonic() + 5
            while time.monotonic() < fim and not pendente[0].done() and not _esperando_lock(1):
                pass
        return resposta

    monkeypatch.setattr(psycopg.Cursor, "execute", executa)
    assert reject_payout(saque)
    assert pendente[0].exception(timeout=15) is None
    com_pool.shutdown()
    monkeypatch.undo()
    assert _status(com) == {"status": "reversed", "payout_id": None}


@pytest.mark.parametrize("parada", [0, 1], ids=["na_primeira", "na_segunda"])
def test_rejeicao_parada_em_outra_comissao_nao_usa_rotulo_velho(mundo, parada):
    """O `update` da rejeição fica preso numa comissão do saque, com o snapshot já tirado, e o
    reembolso da outra corre. Sem a trava antes, ele devolve com o rótulo de antes. O índice
    cobre as duas ordens de varredura: só uma delas chega à comissão reembolsada por último."""
    c = _afiliado()
    comissoes = [_paga(c, 60), _paga(c, 50)]
    saque = request_payout(c.aff["id"])["id"]
    presa, reembolsada = comissoes[parada], comissoes[1 - parada]
    with ThreadPoolExecutor(2) as pool, get_conn() as segura:
        _segura(segura, presa)
        try:
            rejeicao = pool.submit(reject_payout, saque)
            assert _esperando_lock(1)
            reembolso = pool.submit(tratar_evento, _evento(reembolsada.fatura))
            fim = time.monotonic() + 3
            while time.monotonic() < fim and not reembolso.done():
                time.sleep(0.02)
        finally:
            segura.commit()
        assert rejeicao.result(timeout=15) and reembolso.exception(timeout=15) is None
    assert _status(reembolsada) == {"status": "reversed", "payout_id": None}
    assert _status(presa) == {"status": "pending", "payout_id": None}


def _logs_de_estorno(comissao_id):
    with get_conn() as conn:
        return conn.execute("select level, message, details from system_event_logs"
                            " where event_type = 'affiliate_commission_reversed_by_refund'"
                            " and (details->>'commission_id')::bigint = %s", (comissao_id,)).fetchall()


def test_rejeicao_que_estorna_deixa_um_log_por_comissao_sem_pii(mundo):
    c = _afiliado()
    reembolsada, intacta = _paga(c, 60), _paga(c, 50)
    saque = request_payout(c.aff["id"])["id"]
    _reembolsa(reembolsada.fatura, amount=60_000, devolvido=60_000)
    assert _logs_de_estorno(reembolsada.id) == []  # em saque: o webhook não estornou
    assert reject_payout(saque)
    assert reject_payout(saque) is False  # já rejeitado: nada novo
    logs = _logs_de_estorno(reembolsada.id)
    assert len(logs) == 1 and logs[0]["details"] == {"commission_id": reembolsada.id, "payout_id": saque}
    assert _logs_de_estorno(intacta.id) == []
    assert [p for p in (reembolsada.fatura, reembolsada.indicado.email, str(reembolsada.indicado.uid))
            if p in str(logs[0])] == []


def test_rejeicao_e_reembolso_em_threads_nunca_deixam_a_comissao_solta(mundo):
    for _ in range(25):
        c = _afiliado()
        com = _paga(c)
        saque = request_payout(c.aff["id"])["id"]
        largada = threading.Barrier(2, timeout=10)

        def rejeita():
            largada.wait()
            return reject_payout(saque)

        def reembolsa():
            largada.wait()
            return tratar_evento(_evento(com.fatura))

        with ThreadPoolExecutor(2) as pool:
            passos = [pool.submit(rejeita), pool.submit(reembolsa)]
            for passo in passos:
                passo.result(timeout=15)
        assert _status(com)["status"] == "reversed"


def test_saque_rejeitado_devolve_e_o_reembolso_estorna(mundo):
    c = _afiliado()
    com = _paga(c)
    assert reject_payout(request_payout(c.aff["id"])["id"])
    _reembolsa(com.fatura)
    assert _status(com) == {"status": "reversed", "payout_id": None} and mundo.alertas == []


def test_fatura_sem_comissao_nao_avisa_nem_toca_na_comissao_da_primeira(mundo):
    """Renovação reembolsada: só a 1ª cobrança tem comissão."""
    c = _afiliado()
    com = _paga(c)
    registrar_pagamento(com.indicado.uid, "stripe", "in_renovacao", 100_000, AGORA)
    _reembolsa("in_renovacao")
    assert _status(com)["status"] == "pending" and mundo.alertas == []
    assert _numeros(c) == (1, 1, 0, 1, 0)


def test_comissao_sem_linha_no_registro_e_estornada_mesmo_assim(mundo):
    """Webhook perdido: o rótulo fica para o script, a comissão não espera."""
    c = _afiliado()
    com = _comissao(c, 100)
    _reembolsa(com.fatura)
    assert _status(com)["status"] == "reversed" and _linhas(c) == []


def test_reembolso_de_a_nao_toca_em_b(mundo):
    a, b = _afiliado(), _afiliado()
    de_a, de_b = _paga(a), _paga(b)
    _reembolsa(de_a.fatura)
    assert _numeros(a) == (1, 1, 1, 1, 0) and _numeros(b) == (1, 1, 0, 0, 0)
    assert _status(de_b)["status"] == "pending" and _me(b)["stats"]["generated"] == 100


def test_alerta_nao_leva_dado_do_indicado(mundo):
    c = _afiliado()
    com = _paga(c)
    request_payout(c.aff["id"])
    _reembolsa(com.fatura)
    assert [p for p in (com.fatura, com.indicado.email, str(com.indicado.uid)) if p in mundo.alertas[0]] == []


def test_evento_fora_de_ordem_guarda_a_pendencia_e_nao_atrapalha_o_pagamento(mundo):
    c = _afiliado()
    ind = _indicado(c)
    fatura = f"in_{uuid.uuid4().hex[:12]}"
    _reembolsa(fatura)
    assert registrar_pagamento(ind.uid, "stripe", fatura, 100, AGORA)
    assert _numeros(c) == (1, 1, 0, 0, 0)  # limite declarado: o rótulo depende da linha, quem repara é o script
    with get_conn() as conn:
        assert conn.execute("select status from affiliate_refund_reviews where stripe_invoice_id = %s",
                            (fatura,)).fetchone()["status"] == "no_commission"
    assert mundo.alertas == []


# ── Corrida com o saque ──────────────────────────────────────────────────────

def _evento(fatura, devolvido=100_000):
    return {"id": f"evt_{uuid.uuid4().hex}", "type": "charge.refunded", "created": int(time.time()),
            "data": {"object": _cobranca(fatura, devolvido=devolvido)}}


def _sacar_rota(c):
    r = _sacar(c)
    assert r.status_code == 200, r.text
    return r.json()["payout"]["id"]


def _segura(conn, com):
    conn.execute("select id from affiliate_commissions where id = %s for update", (com.id,))


def test_saque_chega_antes_do_reembolso_e_o_admin_e_avisado(mundo):
    c = _afiliado()
    com = _paga(c)
    with ThreadPoolExecutor(2) as pool, get_conn() as segura:
        _segura(segura, com)
        try:
            saque = pool.submit(request_payout, c.aff["id"])
            assert _esperando_lock(1)
            reembolso = pool.submit(tratar_evento, _evento(com.fatura))
            assert _esperando_lock(2)
        finally:
            segura.commit()
        assert reembolso.exception(timeout=15) is None  # sem `DeadlockDetected`
        pedido = saque.result(timeout=15)
    assert _status(com) == {"status": "pending", "payout_id": pedido["id"]}
    assert len(mundo.alertas) == 1 and _numeros(c) == (1, 1, 1, 1, 0)


def test_reembolso_chega_antes_do_saque_e_a_comissao_fica_fora_dele(mundo):
    c = _afiliado()
    com = _paga(c)
    outra = _paga(c, 70)
    with ThreadPoolExecutor(2) as pool, get_conn() as segura:
        _segura(segura, com)
        try:
            reembolso = pool.submit(tratar_evento, _evento(com.fatura))
            assert _esperando_lock(1)
            saque = pool.submit(request_payout, c.aff["id"])
            assert _esperando_lock(2)
        finally:
            segura.commit()
        assert reembolso.exception(timeout=15) is None
        pedido = saque.result(timeout=15)
    assert pedido["amount_cents"] == 70_00
    assert _status(com) == {"status": "reversed", "payout_id": None}
    assert _status(outra)["payout_id"] == pedido["id"] and mundo.alertas == []


# ── A conversa inteira ───────────────────────────────────────────────────────

def test_conversa_do_link_ao_reembolso(mundo, monkeypatch):
    c = _afiliado()
    codigos = {}
    monkeypatch.setattr(email_service, "send_verification_email",
                        lambda to, code, *a, **k: codigos.update({to: code}) or True)
    monkeypatch.setattr(email_service, "send_welcome_email", lambda *a, **k: True)
    monkeypatch.setattr(email_service, "send_pro_charged_email", lambda *a, **k: True)
    visitante = TestClient(dashboard.app)
    assert visitante.get(f"/r/{c.aff['code']}", follow_redirects=False).status_code == 302
    email = f"afil-reemb-{uuid.uuid4().hex[:10]}@example.com"
    r = visitante.post("/auth/register", headers=csrf(visitante), json={
        "email": email, "password": SENHA, "phone": f"55119{uuid.uuid4().int % 100_000_000:08d}", "name": "Bia"})
    assert r.status_code == 200, r.text
    r = visitante.post("/auth/verify-email", headers=csrf(visitante), json={"email": email, "code": codigos[email]})
    assert r.status_code == 200, r.text
    novo = int(r.json()["user_id"])

    mundo.subs["sub_c"] = _fake_sub("active")
    fatura = f"in_{uuid.uuid4().hex[:12]}"
    r = _enviar("invoice.paid", {"object": "invoice", "id": fatura, "subscription": "sub_c", "amount_paid": 60_000,
                                 "metadata": {"finbot_user_id": str(novo)}, "lines": {"data": [], "has_more": False}})
    assert r.status_code == 200, r.text
    m = _me(c)
    assert m["stats"]["paying_clients"] == 1 and m["stats"]["held"] == 60

    mundo.pagamentos["pi_c"] = [{"status": "paid", "invoice": fatura}]
    mundo.stripe.cobra(fatura, amount=60_000, devolvido=60_000)
    _reembolsa(None, intent="pi_c", amount=60_000, devolvido=60_000)
    m = _me(c)
    s = m["stats"]
    assert (s["referrals"], s["paying_clients"], s["refunded_clients"], s["refunds"]) == (1, 1, 1, {"total": 1, "partial": 0})
    assert s["generated"] == 0
    assert (m["commissions"][0]["status"], m["commissions"][0]["payment_refund"]) == ("reversed", "total")
    db.delete_user_data(novo)
    assert _numeros(c) == (1, 1, 1, 1, 0)


# ── Script ───────────────────────────────────────────────────────────────────

class _Lista(list):
    def auto_paging_iter(self):
        return iter(self)


class _StripeDoScript(_FakeStripe):
    """Faturas por cliente, cobrança por fatura. `quebra` = clientes cuja listagem levanta."""

    def __init__(self):
        super().__init__()
        self.faturas, self.cobrancas, self.quebra, self.assinaturas = {}, {}, set(), {}
        self.reembolsos, self.pagamentos, self.disputas, self.listagens = {}, {}, {}, Counter()
        outer = self

        class Invoice:
            @staticmethod
            def list(customer, status, limit):
                assert status == "paid"
                if customer in outer.quebra:
                    raise RuntimeError(f"cliente {customer} deu erro")
                return _Lista(outer.faturas.get(customer, []))

        class InvoicePayment:
            @staticmethod
            def list(invoice, api_key, limit):
                outer.listagens[invoice] += 1
                pagamentos = outer.pagamentos.get(
                    invoice, [{"status": "paid", "payment": {"type": "payment_intent", "payment_intent": invoice}}])
                if isinstance(pagamentos, Exception):
                    raise pagamentos
                return {"data": pagamentos}

        class PaymentIntent:
            @staticmethod
            def retrieve(intent, expand, api_key):
                cobranca = outer.cobrancas.get(intent, {"amount": 1, "amount_refunded": 0, "disputed": False})
                return {"latest_charge": {"id": intent, **cobranca}}

        class Refund:
            @staticmethod
            def list(charge, limit):
                return {"data": [{"created": t} for t in outer.reembolsos.get(charge, [1_790_500_000])]}

        class Dispute:
            @staticmethod
            def list(charge, limit, api_key):
                return {"data": outer.disputas.get(charge, [])}

        self.Invoice, self.InvoicePayment, self.PaymentIntent, self.Refund = Invoice, InvoicePayment, PaymentIntent, Refund
        self.Dispute = Dispute
        self.Subscription = SimpleNamespace(retrieve=lambda sid: outer.assinaturas.get(sid, {"metadata": {}}))


@pytest.fixture
def script(monkeypatch):
    import backfill_afiliado_pagamentos as modulo
    fake = _StripeDoScript()
    monkeypatch.setitem(sys.modules, "stripe", fake)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_xxx")
    instalar(monkeypatch)
    return SimpleNamespace(rodar=modulo.rodar, main=modulo.main, stripe=fake)


def _cliente(ind):
    cliente = f"cus_{uuid.uuid4().hex[:10]}"
    with get_conn() as conn:
        conn.execute("update auth_accounts set stripe_customer_id = %s where user_id = %s", (cliente, ind.uid))
        conn.commit()
    return cliente


def _fatura(fid, valor=60_000, sub="sub_s", **k):
    return {"id": fid, "amount_paid": valor, "subscription": sub, "created": 1_790_000_000,
            "status_transitions": {"paid_at": 1_790_000_100}, "lines": {"data": [], "has_more": False}, **k}


def test_script_sem_aplicar_nao_grava(script):
    c = _afiliado()
    script.stripe.faturas[_cliente(_indicado(c))] = [_fatura("in_s1")]
    n = script.rodar()
    assert (n["clientes"], n["faturas_novas"]) == (1, 1)
    assert _linhas(c) == []


def test_script_aplica_e_e_idempotente_e_estorna_a_comissao_solta(script):
    c = _afiliado()
    com = _comissao(c, 60)  # comissão antiga, sem linha no registro
    novo = _indicado(c)
    script.stripe.faturas[_cliente(com.indicado)] = [_fatura(com.fatura)]
    script.stripe.faturas[_cliente(novo)] = [_fatura("in_sem_comissao", 50_000), _fatura("in_parcial", 40_000)]
    script.stripe.cobrancas[com.fatura] = {"amount": 60_000, "amount_refunded": 60_000, "disputed": False}
    script.stripe.cobrancas["in_parcial"] = {"amount": 40_000, "amount_refunded": 1_000, "disputed": True}

    n = script.rodar(aplicar=True)
    assert (n["faturas_novas"], n["reembolsos_total"], n["reembolsos_partial"], n["contestadas_para_conferir"]) == (3, 1, 1, 0)
    achado = {x["payment_ref"]: (x["amount_cents"], x["refund_status"]) for x in _linhas(c)}
    # contestada sem disputa aberta (ganha): rotulada pela regra única, só o reembolso conta (#1498)
    assert achado == {com.fatura: (60_000, "total"), "in_sem_comissao": (50_000, "none"), "in_parcial": (40_000, "partial")}
    assert _status(com)["status"] == "reversed"  # total confirmado, comissão solta: `a_estornar`

    n = script.rodar(aplicar=True)
    assert (n["faturas_novas"], n["faturas_ja_no_registro"]) == (0, 3)
    assert len(_linhas(c)) == 3 and _numeros(c) == (2, 2, 1, 1, 1)


def test_script_tira_o_caderno_e_ignora_o_que_nao_e_plano(script):
    c = _afiliado()
    ind = _indicado(c)
    script.stripe.assinaturas["sub_cad"] = {"metadata": {"ebook_price": "price_cad"}}
    linhas = {"data": [{"pricing": {"price_details": {"price": "price_cad"}}, "amount": 9_900}], "has_more": False}
    script.stripe.faturas[_cliente(ind)] = [
        _fatura("in_so_caderno", 9_900, "sub_cad", lines=linhas),
        _fatura("in_com_caderno", 69_900, "sub_cad", lines=linhas),
        _fatura("in_avulsa", 5_000, None),
    ]
    n = script.rodar(aplicar=True)
    assert n["faturas_fora_da_regra"] == 2
    assert [(x["payment_ref"], x["amount_cents"]) for x in _linhas(c)] == [("in_com_caderno", 60_000)]


def test_script_grava_a_data_do_reembolso_e_nao_a_do_pagamento(script):
    c = _afiliado()
    script.stripe.faturas[_cliente(_indicado(c))] = [_fatura("in_data")]
    script.stripe.cobrancas["in_data"] = {"amount": 60_000, "amount_refunded": 60_000, "disputed": False}
    script.stripe.reembolsos["in_data"] = [1_790_400_000, 1_790_600_000]
    script.rodar(aplicar=True)
    with get_conn() as conn:
        linha = conn.execute("select paid_at, refunded_at from affiliate_referral_payments"
                             " where payment_ref = 'in_data'").fetchone()
    assert linha["paid_at"] == datetime.fromtimestamp(1_790_000_100, timezone.utc)
    assert linha["refunded_at"] == datetime.fromtimestamp(1_790_600_000, timezone.utc)


def test_script_pula_cartao_recusado_com_pix_e_conta_assinatura_cancelada_pelo_cliente(script):
    c = _afiliado()
    script.stripe.assinaturas["sub_recusada"] = {"metadata": {}, "cancellation_details": {"comment": MARCA}}
    script.stripe.assinaturas["sub_cancelada"] = {"metadata": {}, "cancellation_details": {"comment": "mudei de ideia"}}
    script.stripe.faturas[_cliente(_indicado(c))] = [_fatura("in_recusada", sub="sub_recusada"),
                                                     _fatura("in_cancelada", sub="sub_cancelada")]
    assert script.rodar(aplicar=True)["faturas_fora_da_regra"] == 1
    assert [x["payment_ref"] for x in _linhas(c)] == ["in_cancelada"]


def test_script_acha_a_assinatura_no_parent_da_api_nova(script):
    c = _afiliado()
    fatura = _fatura("in_parent", sub=None, parent={"subscription_details": {"subscription": "sub_s"}})
    script.stripe.faturas[_cliente(_indicado(c))] = [fatura]
    script.rodar(aplicar=True)
    assert [x["payment_ref"] for x in _linhas(c)] == ["in_parent"]


def test_script_falha_de_um_cliente_nao_para_os_outros_e_nao_imprime_pii(script, capsys, monkeypatch):
    c = _afiliado()
    ruim, bom = _indicado(c), _indicado(c)
    cliente_ruim, cliente_bom = _cliente(ruim), _cliente(bom)
    script.stripe.quebra.add(cliente_ruim)
    script.stripe.faturas[cliente_bom] = [_fatura("in_bom")]
    monkeypatch.setattr(sys, "argv", ["backfill", "--aplicar"])
    assert script.main() == 1
    saida = capsys.readouterr()
    assert [x["payment_ref"] for x in _linhas(c)] == ["in_bom"]
    assert "clientes_com_falha: 1" in saida.out
    assert [p for p in (cliente_ruim, cliente_bom, "in_bom", ruim.email, str(ruim.uid)) if p in saida.out + saida.err] == []


def test_script_nao_traz_de_volta_conta_excluida(script):
    c = _afiliado()
    ind = _indicado(c)
    script.stripe.faturas[_cliente(ind)] = [_fatura("in_excluida")]
    db.delete_user_data(ind.uid)
    assert script.rodar(aplicar=True)["clientes"] == 0


def test_script_sem_chave_para_antes_de_ler(script, monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY")
    monkeypatch.setattr(sys, "argv", ["backfill"])
    assert script.main() == 2


def test_pagamento_perdido_sem_comissao_e_reparado_pelo_script(script):
    """A4: fatura sem comissão fora do registro, e o boot não tem de onde recuperar."""
    c = _afiliado()
    ind = _indicado(c)
    script.stripe.faturas[_cliente(ind)] = [_fatura("in_perdida")]
    db.init_db()
    assert _numeros(c)[:2] == (1, 0)
    script.rodar(aplicar=True)
    assert _numeros(c)[:2] == (1, 1)
