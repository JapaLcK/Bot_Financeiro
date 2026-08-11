"""
tests/test_billing_webhook_lifecycle.py — ciclo de vida do trial via webhook Stripe.

Smoke test (A2) do caminho que NUNCA rodou em prod: desde o modelo novo de trial
(2026-08-06) nenhuma assinatura nasceu `trialing` (plan_trials vazia). Este teste
alimenta eventos Stripe sintéticos pelo POST /billing/webhook e verifica as
transições no banco de teste — provando que a máquina de cobrança funciona antes
do primeiro pagante real:

  checkout.session.completed (trialing) → plano setado + status 'trialing' + trial reivindicado
  customer.subscription.trial_will_end  → aviso de fim de trial (não quebra o webhook)
  invoice.payment_succeeded             → status 'active' (converteu no dia 31)
  invoice.payment_failed                → status 'past_due'
  customer.subscription.deleted         → volta pra plano 'free' / status 'canceled'

Stripe é mockado via sys.modules (rede bloqueada no conftest); send_email idem.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

os.environ.setdefault("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())

import db
from db.connection import get_conn
import frontend.finance_bot_websocket_custom as dashboard


def _future_ts(days: int) -> int:
    return int((datetime.now(timezone.utc) + timedelta(days=days)).timestamp())


class _FakeStripe:
    """Só o que o webhook usa: Webhook.construct_event + Subscription.retrieve +
    error.SignatureVerificationError. `next_event` é o evento que o próximo POST
    vai receber; `subs` alimenta o retrieve do checkout/invoice."""

    class _SigErr(Exception):
        pass

    def __init__(self):
        self.api_key = None
        self.next_event = None
        self.subs: dict = {}
        outer = self

        class _Webhook:
            @staticmethod
            def construct_event(payload, sig_header, secret):
                if outer.next_event is None:
                    raise outer._SigErr("assinatura inválida")
                return outer.next_event

        class _Subscription:
            @staticmethod
            def retrieve(sub_id):
                return outer.subs[sub_id]

        self.Webhook = _Webhook
        self.Subscription = _Subscription
        self.error = SimpleNamespace(
            SignatureVerificationError=self._SigErr,
            StripeError=Exception,
        )


def _fake_sub(status: str, price_id: str = "price_desconhecido", days: int = 30) -> dict:
    ts = _future_ts(days)
    return {
        "status": status,
        "current_period_end": ts,
        "items": {"data": [{"price": {"id": price_id}, "current_period_end": ts}]},
    }


def _setup(monkeypatch, suffix) -> tuple[int, TestClient, _FakeStripe]:
    email = f"wh-{suffix}@t.com"
    user = db.register_auth_user(email, "senha-forte-123")
    uid = int(user["user_id"])
    # phone_hash é obrigatório pro claim_trial_for_user (o trial ancora no telefone).
    # Em prod só chega `trialing` quem é elegível — e elegível exige telefone.
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update auth_accounts set phone_hash=%s where user_id=%s",
                (f"ph_wh_{uid}", uid),
            )
        conn.commit()
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_xxx")
    fake = _FakeStripe()
    monkeypatch.setitem(sys.modules, "stripe", fake)
    client = TestClient(dashboard.app)
    return uid, client, fake


def _post(client, fake, event: dict, subs: dict | None = None):
    fake.next_event = event
    if subs:
        fake.subs.update(subs)
    return client.post(
        "/billing/webhook",
        content=b"{}",
        headers={"stripe-signature": "t=1,v1=fake"},
    )


def _cleanup_trial(uid: int):
    # plan_trials NÃO cascateia com a deleção de conta (sem FK, por design) — o
    # conftest limpa o user mas deixaria a trava do trial. Limpa aqui.
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("delete from plan_trials where phone_hash=%s", (f"ph_wh_{uid}",))
        conn.commit()


def test_trial_lifecycle_completo(user_id, monkeypatch):
    """checkout(trialing) → trial_will_end → invoice paga: plano converte de
    trialing pra active e o trial é reivindicado no telefone."""
    uid, client, fake = _setup(monkeypatch, f"life-{user_id}")
    try:
        # 1) checkout concluído com trial → status trialing + plano + claim
        r = _post(
            client, fake,
            {"type": "checkout.session.completed",
             "data": {"object": {"metadata": {"finbot_user_id": str(uid)},
                                  "subscription": "sub_life"}}},
            subs={"sub_life": _fake_sub("trialing")},
        )
        assert r.status_code == 200, r.text
        row = db.get_auth_user(uid)
        assert row["plan"] == "pro"                      # price desconhecido → Plus legado
        assert row["last_payment_status"] == "trialing"
        # trial reivindicado → telefone agora inelegível a um novo trial
        from db.plans import is_trial_eligible_for_user
        assert is_trial_eligible_for_user(uid) is False

        # 2) aviso "trial acaba em 3 dias" → não pode quebrar o webhook
        r = _post(
            client, fake,
            {"type": "customer.subscription.trial_will_end",
             "data": {"object": {"metadata": {"finbot_user_id": str(uid)},
                                  "current_period_end": _future_ts(3)}}},
        )
        assert r.status_code == 200, r.text

        # 3) fatura paga (dia 31) → converteu de verdade, status active
        r = _post(
            client, fake,
            {"type": "invoice.payment_succeeded",
             "data": {"object": {"metadata": {"finbot_user_id": str(uid)},
                                  "subscription": "sub_life", "amount_paid": 1990,
                                  "id": "in_life_1"}}},
            subs={"sub_life": _fake_sub("active")},
        )
        assert r.status_code == 200, r.text
        assert db.get_auth_user(uid)["last_payment_status"] == "active"
    finally:
        _cleanup_trial(uid)


def test_payment_failed_marca_past_due(user_id, monkeypatch):
    """Falha de pagamento marca past_due (não rebaixa — Stripe vai retentar)."""
    uid, client, fake = _setup(monkeypatch, f"pf-{user_id}")
    try:
        r = _post(
            client, fake,
            {"type": "invoice.payment_failed",
             "data": {"object": {"metadata": {"finbot_user_id": str(uid)},
                                  "attempt_count": 1}}},
        )
        assert r.status_code == 200, r.text
        assert db.get_auth_user(uid)["last_payment_status"] == "past_due"
    finally:
        _cleanup_trial(uid)


def test_subscription_deleted_volta_pra_free(user_id, monkeypatch):
    """Cancelamento definitivo → plano free + status canceled."""
    uid, client, fake = _setup(monkeypatch, f"del-{user_id}")
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("update auth_accounts set plan='pro' where user_id=%s", (uid,))
            conn.commit()
        r = _post(
            client, fake,
            {"type": "customer.subscription.deleted",
             "data": {"object": {"metadata": {"finbot_user_id": str(uid)}}}},
        )
        assert r.status_code == 200, r.text
        row = db.get_auth_user(uid)
        assert row["plan"] == "free"
        assert row["last_payment_status"] == "canceled"
    finally:
        _cleanup_trial(uid)


def test_assinatura_invalida_da_400(user_id, monkeypatch):
    """Assinatura Stripe inválida → 400 (não processa evento forjado)."""
    uid, client, fake = _setup(monkeypatch, f"sig-{user_id}")
    fake.next_event = None  # construct_event levanta SignatureVerificationError
    r = client.post("/billing/webhook", content=b"{}",
                    headers={"stripe-signature": "bad"})
    assert r.status_code == 400
