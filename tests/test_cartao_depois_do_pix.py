"""Cartão nunca cobra período que um Pix pago já cobre — nas duas ordens.

A conversa entre os DOIS webhooks, com banco real: o Pix pago pelo dreno de
verdade (`entregar`) e o cartão pelo `POST /billing/webhook` (`_post`).

Caminho A — Pix pago, DEPOIS o checkout de cartão (sessão aberta antes do Pix):
  o webhook cancela a assinatura na hora (`Subscription.cancel` com a MARCA),
  registra os cadernos e não materializa nada; plano cobrado vira alerta.
Caminho B — cartão primeiro, Pix pago depois: o efeito `stripe_cancel` pergunta
  ao Stripe mesmo sem `stripe_subscription_id` e agenda `cancel_at_period_end`.

Controles negativos (rodados, ver o relato do PR):
  · sem a guarda do `checkout`         → A1, A2, A3, A4a vermelhos;
  · sem a guarda do `invoice.paid`     → A2 e A4b vermelhos;
  · sem a MARCA no `deleted`           → A5 vermelho;
  · sem o passo 5 no `_stripe_cancel`  → B1 e B3 vermelhos.
  · plano = `amount_total` cru         → A3 vermelho;
  · alerta só quando ESTE evento cancela → A4b vermelho;
  · total sem fatura rotulado de plano  → A3b vermelho;
  · alerta sem "só DEPOIS da entrega"   → A3 e A3b vermelhos;
  · log sempre `warning`                → A5b vermelho;
  · `invoice.paid` recusando qualquer `billing_reason` → A7 vermelho;
  · `_stripe_cancel` sem `stripe.api_key` → B1 e B2 vermelhos (o fake recusa
    como o SDK real).
Positivos: A6 (sem Pix, o checkout materializa como sempre), A5-controle (sem a
marca o e-mail sai), A7 (renovação segue) e B2 (customer sem assinatura viva não
fala em `modify`). A falha entre efeitos mora em `test_cartao_pix_retentativa.py`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import db
import db_support
from _billing_grants_helpers import garantir_system_event_logs
from _dreno_pix_helpers import entregar, mundo_externo, nova_cobranca
from core.services import admin_notify
from core.services import email_service as es
from core.services.cartao_recusado_por_pix import MARCA
from db.affiliates import create_affiliate, record_referral
from db.connection import get_conn
from db.plan_grants import list_grants
import frontend.finance_bot_websocket_custom as dashboard
from test_billing_webhook_lifecycle import _T_LIFE, _cleanup_trial, _fake_sub, _post, _setup

_PRECO = "price_caderno"
_URL = "https://drive.test/uc?id=cad"


def _completar(fake) -> None:
    """O que o `_FakeStripe` do lifecycle não tem: cancel, list, modify, Invoice."""
    fake.cancelados, fake.modificados, fake.invoices = [], [], {}

    def cancel(sub_id, **kw):
        fake.cancelados.append((sub_id, kw))
        fake.subs[sub_id]["status"] = "canceled"
        return fake.subs[sub_id]

    def listar(customer=None, status=None, limit=None):
        if not fake.api_key:   # como o SDK real: sem chave, nem sai do processo
            raise RuntimeError("No API key provided")
        return {"data": [s for s in fake.subs.values()
                         if s.get("customer") == customer and s["status"] == status][:limit]}

    def modify(sub_id, **kw):
        fake.modificados.append((sub_id, kw))
        return fake.subs[sub_id]

    fake.Subscription.cancel = staticmethod(cancel)
    fake.Subscription.list = staticmethod(listar)
    fake.Subscription.modify = staticmethod(modify)
    fake.Invoice = SimpleNamespace(retrieve=lambda inv: fake.invoices[inv])


@pytest.fixture()
def cena(user_id, monkeypatch):
    garantir_system_event_logs()
    externo = mundo_externo(monkeypatch)
    uid, client, fake = _setup(monkeypatch, f"cdp-{user_id}")
    _completar(fake)
    vistos = {k: [] for k in ("welcome", "charged", "canceled", "new_pro",
                              "notify_canceled", "fundador")}

    def _espiao(nome, ret=True):
        def _fn(*a, **kw):
            vistos[nome].append(a or kw)
            return ret
        _fn.__name__ = f"espiao_{nome}"   # chave da dedupe do `_fire_email`
        return _fn

    monkeypatch.setattr(es, "send_pro_welcome_email", _espiao("welcome"))
    monkeypatch.setattr(es, "send_pro_charged_email", _espiao("charged"))
    monkeypatch.setattr(es, "send_subscription_canceled_email", _espiao("canceled"))
    monkeypatch.setattr(admin_notify, "notify_new_pro", _espiao("new_pro", None))
    monkeypatch.setattr(admin_notify, "notify_subscription_canceled",
                        _espiao("notify_canceled", None))
    original = es.send_founder_email_once

    def _fundador(user, to, source, ref):
        vistos["fundador"].append(source)
        return original(user, to, source, ref)
    monkeypatch.setattr(es, "send_founder_email_once", _fundador)
    yield SimpleNamespace(uid=uid, client=client, fake=fake, externo=externo, vistos=vistos)
    _cleanup_trial(uid)


def _sub(fake, sub_id, status, customer=None, days=30):
    fake.subs[sub_id] = dict(_fake_sub(status, "price_plano", days), id=sub_id, customer=customer)
    return fake.subs[sub_id]


def _checkout(uid, sub_id, *, amount=0, sid="cs_cdp", evt="evt_cdp_co", meta=None, invoice=None):
    obj = {"id": sid, "metadata": {"finbot_user_id": str(uid), **(meta or {})},
           "subscription": sub_id, "amount_total": amount, "currency": "brl"}
    if invoice:
        obj["invoice"] = invoice
    return {"type": "checkout.session.completed", "id": evt, "created": _T_LIFE,
            "data": {"object": obj}}


def _linha(price, amount):
    return {"amount": amount, "pricing": {"price_details": {"price": price}},
            "discount_amounts": []}


def _fatura(uid, sub_id, amount, *, inv="in_cdp", evt="evt_cdp_inv",
            reason="subscription_create"):
    return {"type": "invoice.paid", "id": evt, "created": _T_LIFE,
            "data": {"object": {"id": inv, "metadata": {"finbot_user_id": str(uid)},
                                "subscription": sub_id, "amount_paid": amount,
                                "currency": "brl", "billing_reason": reason,
                                "lines": {"data": [_linha("price_plano", amount)]}}}}


def _deleted(uid, sub_id, comment=None):
    return {"type": "customer.subscription.deleted", "id": f"evt_del_{sub_id}",
            "created": _T_LIFE + 10,
            "data": {"object": {"id": sub_id, "metadata": {"finbot_user_id": str(uid)},
                                "items": {"data": [{"price": {"id": "price_plano"}}]},
                                "cancellation_details": {"reason": "cancellation_requested",
                                                         "comment": comment}}}}


def _grants(uid, source):
    return [g for g in list_grants(uid) if g["source"] == source and g["status"] == "active"]


def _pix_pago(c):
    entregar("PAYMENT_RECEIVED", nova_cobranca(c.uid))
    assert dashboard._grant_pix_vigente(c.uid) is not None
    return {"capi": c.externo["capi"], "ga4": c.externo["ga4"],
            "alerta": len(c.externo["alerta"])}


def _sql(q, a=()):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(q, a)
        return cur.fetchall() if cur.description else None


def _conta_pix_intacta(uid):
    db_support.invalidate_auth_user_cache(uid)
    conta = db.get_auth_user(uid)
    assert (conta["plan"], conta["last_payment_status"]) == ("pro_max", "active"), conta


# ── Caminho A ────────────────────────────────────────────────────────────────

def test_a1_trial_com_pix_vigente_cancela_e_nao_materializa(cena):
    c = cena
    antes = _pix_pago(c)
    _sub(c.fake, "sub_a1", "trialing")
    r = _post(c.client, c.fake, _checkout(c.uid, "sub_a1"))
    assert r.status_code == 200, r.text

    assert _grants(c.uid, "stripe") == []
    assert c.fake.cancelados == [("sub_a1", {"cancellation_details": {"comment": MARCA}})]
    _conta_pix_intacta(c.uid)
    assert (c.vistos["welcome"], c.vistos["new_pro"]) == ([], [])
    assert "stripe" not in c.vistos["fundador"]
    assert (c.externo["capi"], c.externo["ga4"], len(c.externo["alerta"])) == (
        antes["capi"], antes["ga4"], antes["alerta"])
    assert _sql("select 1 from plan_trials where phone_hash = %s", (f"ph_wh_{c.uid}",)) == []


def test_a2_sem_trial_alerta_e_a_fatura_nao_materializa_nem_comissiona(cena, user_id):
    c = cena
    assert record_referral(create_affiliate(user_id)["code"], c.uid)
    antes = _pix_pago(c)
    _sub(c.fake, "sub_a2", "active")
    assert _post(c.client, c.fake, _checkout(c.uid, "sub_a2", amount=4990)).status_code == 200
    novos = c.externo["alerta"][antes["alerta"]:]
    assert len(novos) == 1 and "R$ 49.90" in novos[0] and "cs_cdp" in novos[0], novos

    assert _post(c.client, c.fake, _fatura(c.uid, "sub_a2", 4990)).status_code == 200
    assert _grants(c.uid, "stripe") == []
    assert c.vistos["charged"] == []
    assert _sql("select 1 from affiliate_commissions where referred_user_id = %s", (c.uid,)) == []
    assert len(c.fake.cancelados) == 1
    _conta_pix_intacta(c.uid)


def test_a3_cadernos_sao_entregues_e_o_alerta_separa_o_plano(cena):
    c = cena
    antes = _pix_pago(c)
    _sub(c.fake, "sub_a3", "active")
    c.fake.invoices["in_a3"] = {"id": "in_a3", "amount_paid": 5980, "lines": {"data": [
        _linha(_PRECO, 990), _linha("price_plano", 4990)]}}
    r = _post(c.client, c.fake, _checkout(c.uid, "sub_a3", amount=5980, invoice="in_a3",
                                          meta={"ebook_price": _PRECO, "ebook_url": _URL}))
    assert r.status_code == 200, r.text
    assert _sql("select session_id, ebook_price from ebook_entregas where user_id = %s",
                (c.uid,)) == [{"session_id": "cs_cdp", "ebook_price": _PRECO}]
    (alerta,) = c.externo["alerta"][antes["alerta"]:]
    assert "plano R$ 49.90 + cadernos R$ 9.90" in alerta, alerta
    # E2 × estorno: estornar antes da entrega fecha os cadernos como `estornado`
    assert "só DEPOIS de os cadernos constarem como enviados" in alerta, alerta
    assert "session_id = 'cs_cdp'" in alerta, alerta
    assert _grants(c.uid, "stripe") == []


def test_a3b_sem_fatura_o_total_nao_vira_plano(cena):
    c = cena
    antes = _pix_pago(c)
    _sub(c.fake, "sub_a3b", "active")
    assert _post(c.client, c.fake, _checkout(c.uid, "sub_a3b", amount=5980, meta={
        "ebook_price": _PRECO, "ebook_url": _URL})).status_code == 200
    (alerta,) = c.externo["alerta"][antes["alerta"]:]
    assert "total R$ 59.80 inclui os cadernos" in alerta, alerta
    assert "plano cobrado" not in alerta and "só DEPOIS" in alerta, alerta


def test_a4a_checkout_reentregue_cancela_uma_vez(cena):
    c = cena
    _pix_pago(c)
    _sub(c.fake, "sub_a4", "trialing")
    for _ in range(2):
        assert _post(c.client, c.fake, _checkout(c.uid, "sub_a4")).status_code == 200
    assert len(c.fake.cancelados) == 1
    assert _grants(c.uid, "stripe") == []


def test_a4b_fatura_antes_do_checkout_mesmo_resultado(cena):
    c = cena
    antes = _pix_pago(c)
    _sub(c.fake, "sub_a4b", "active")
    assert _post(c.client, c.fake, _fatura(c.uid, "sub_a4b", 4990)).status_code == 200
    assert len(c.fake.cancelados) == 1
    assert _post(c.client, c.fake, _checkout(c.uid, "sub_a4b", amount=4990)).status_code == 200
    assert len(c.fake.cancelados) == 1
    assert _grants(c.uid, "stripe") == []
    assert c.vistos["charged"] == [] and c.vistos["welcome"] == []
    # o alerta de estorno não se perde quando a fatura chega primeiro
    assert len(c.externo["alerta"]) - antes["alerta"] == 1
    _conta_pix_intacta(c.uid)


def test_a5_deleted_com_a_marca_nao_avisa_e_o_pix_fica(cena):
    c = cena
    _pix_pago(c)
    _sub(c.fake, "sub_a5", "canceled")
    assert _post(c.client, c.fake, _deleted(c.uid, "sub_a5", MARCA)).status_code == 200
    assert (c.vistos["canceled"], c.vistos["notify_canceled"]) == ([], [])
    _conta_pix_intacta(c.uid)


def test_a5_controle_deleted_sem_a_marca_avisa_como_hoje(cena):
    c = cena
    _pix_pago(c)
    _sub(c.fake, "sub_a5c", "canceled")
    assert _post(c.client, c.fake, _deleted(c.uid, "sub_a5c")).status_code == 200
    assert len(c.vistos["canceled"]) == 1
    assert len(c.vistos["notify_canceled"]) == 1


def test_a5b_alerta_que_nao_sai_vira_log_de_erro(cena, monkeypatch):
    c = cena
    _pix_pago(c)
    monkeypatch.setattr(admin_notify, "notify_pix_alerta", lambda msg: False)
    _sub(c.fake, "sub_a5b", "active")
    assert _post(c.client, c.fake, _checkout(c.uid, "sub_a5b", amount=4990)).status_code == 200
    assert _sql("select level from system_event_logs where user_id = %s and event_type ="
                " 'billing_cartao_recusado_pix'", (c.uid,)) == [{"level": "error"}]


def test_a6_positivo_sem_pix_o_checkout_materializa(cena):
    c = cena
    _sub(c.fake, "sub_a6", "trialing")
    assert _post(c.client, c.fake, _checkout(c.uid, "sub_a6")).status_code == 200
    assert len(_grants(c.uid, "stripe")) == 1
    assert len(c.vistos["welcome"]) == 1
    assert c.fake.cancelados == []


def test_a7_renovacao_com_pix_vigente_segue_como_sempre(cena):
    """Só a 1ª fatura (`subscription_create`) é recusada: a renovação de um
    cartão que já existia é outro problema (migração) e segue o caminho de hoje."""
    c = cena
    _pix_pago(c)
    _sub(c.fake, "sub_a7", "active")
    r = _post(c.client, c.fake, _fatura(c.uid, "sub_a7", 4990, reason="subscription_cycle"))
    assert r.status_code == 200, r.text
    assert c.fake.cancelados == []
    assert len(_grants(c.uid, "stripe")) == 1


# ── Caminho B ────────────────────────────────────────────────────────────────

def _fim(sub):
    return datetime.fromtimestamp(sub["current_period_end"], tz=timezone.utc)


def test_b1_pix_pago_com_cartao_vivo_agenda_o_cancelamento(cena):
    c = cena
    db.set_stripe_customer(c.uid, "cus_b1")
    sub = _sub(c.fake, "sub_b1", "trialing", customer="cus_b1")
    entregar("PAYMENT_RECEIVED", nova_cobranca(c.uid))
    assert c.fake.modificados == [("sub_b1", {"cancel_at_period_end": True})]
    (pix,) = _grants(c.uid, "pix")
    assert pix["starts_at"] >= _fim(sub) - timedelta(minutes=1)


def test_b2_positivo_customer_sem_assinatura_viva(cena):
    c = cena
    db.set_stripe_customer(c.uid, "cus_b2")
    _sub(c.fake, "sub_b2", "canceled", customer="cus_b2")
    entregar("PAYMENT_RECEIVED", nova_cobranca(c.uid))
    assert c.fake.modificados == []
    (pix,) = _grants(c.uid, "pix")
    assert pix["starts_at"] <= datetime.now(timezone.utc) + timedelta(minutes=1)


def test_b3_conversa_checkout_em_trial_e_depois_o_pix(cena):
    c = cena
    db.set_stripe_customer(c.uid, "cus_b3")
    sub = _sub(c.fake, "sub_b3", "trialing", customer="cus_b3")
    assert _post(c.client, c.fake, _checkout(c.uid, "sub_b3")).status_code == 200
    assert len(_grants(c.uid, "stripe")) == 1

    entregar("PAYMENT_RECEIVED", nova_cobranca(c.uid))
    assert c.fake.modificados == [("sub_b3", {"cancel_at_period_end": True})]
    assert c.fake.cancelados == []
    (pix,) = _grants(c.uid, "pix")
    assert pix["starts_at"] >= _fim(sub) - timedelta(minutes=1)
