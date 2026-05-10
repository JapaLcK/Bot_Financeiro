"""
tests/test_billing_checkout.py — POST /billing/create-checkout.

Cobre:
- default sem body == monthly e usa STRIPE_PRICE_ID_PRO_MENSAL
- interval=annual usa STRIPE_PRICE_ID_PRO_ANUAL
- interval=monthly cai no fallback STRIPE_PRICE_ID_PRO se MENSAL nao setado
- interval invalido retorna 400
- 503 se Stripe nao configurado para o interval pedido
- reaproveita stripe_customer_id existente
"""
from __future__ import annotations

import os
from types import SimpleNamespace

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

os.environ.setdefault("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())

import db
import frontend.finance_bot_websocket_custom as dashboard


_CSRF_TOKEN = "test-csrf-token-billing"


def _auth_user_setup(suffix: str) -> tuple[int, str, TestClient]:
    """Cria auth user real, monta TestClient com cookies validos (auth + CSRF)."""
    email = f"checkout-{suffix}@t.com"
    user = db.register_auth_user(email, "senha-forte-123")
    user_id = int(user["user_id"])
    client = TestClient(dashboard.app)
    client.cookies.set(dashboard.AUTH_COOKIE_NAME, dashboard._make_jwt(user_id, email))
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, _CSRF_TOKEN)
    return user_id, email, client


_CSRF_HEADERS = {dashboard.CSRF_HEADER_NAME: _CSRF_TOKEN}


class _FakeStripe:
    """Stub de stripe.Customer.create + stripe.checkout.Session.create.

    Captura args via .last_session_kwargs pra os testes assertarem.
    """

    def __init__(self):
        self.api_key = None
        self.last_session_kwargs: dict | None = None
        self.last_customer_kwargs: dict | None = None
        self.customer_create_calls = 0

        outer = self

        class _Customer:
            @staticmethod
            def create(**kwargs):
                outer.customer_create_calls += 1
                outer.last_customer_kwargs = kwargs
                return SimpleNamespace(id="cus_test_123")

        class _Session:
            @staticmethod
            def create(**kwargs):
                outer.last_session_kwargs = kwargs
                return SimpleNamespace(url="https://checkout.stripe.com/c/pay/test")

        self.Customer = _Customer
        self.checkout = SimpleNamespace(Session=_Session)


def _patch_stripe(monkeypatch) -> _FakeStripe:
    fake = _FakeStripe()
    import sys
    # Garante que `import stripe` dentro do endpoint resolve pro fake
    monkeypatch.setitem(sys.modules, "stripe", fake)
    return fake


def test_checkout_default_uses_monthly_price(user_id, monkeypatch):
    _, _, client = _auth_user_setup(f"def-{user_id}")
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_xxx")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_MENSAL", "price_mensal_abc")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_ANUAL", "price_anual_xyz")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO", "")
    fake = _patch_stripe(monkeypatch)

    resp = client.post("/billing/create-checkout", headers=_CSRF_HEADERS)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["interval"] == "monthly"
    assert body["checkout_url"].startswith("https://checkout.stripe.com/")

    assert fake.last_session_kwargs is not None
    assert fake.last_session_kwargs["line_items"] == [
        {"price": "price_mensal_abc", "quantity": 1}
    ]
    assert fake.last_session_kwargs["mode"] == "subscription"
    assert fake.last_session_kwargs["metadata"]["interval"] == "monthly"
    # Trial 7 dias garantido pelo backend (price ja nao traz mais trial no Stripe novo)
    assert fake.last_session_kwargs["subscription_data"]["trial_period_days"] == 7
    # Locale pt-BR forca interface em portugues e moeda BRL no Checkout
    assert fake.last_session_kwargs["locale"] == "pt-BR"


def test_checkout_annual_uses_annual_price(user_id, monkeypatch):
    _, _, client = _auth_user_setup(f"ann-{user_id}")
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_xxx")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_MENSAL", "price_mensal_abc")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_ANUAL", "price_anual_xyz")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO", "")
    fake = _patch_stripe(monkeypatch)

    resp = client.post("/billing/create-checkout", json={"interval": "annual"}, headers=_CSRF_HEADERS)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["interval"] == "annual"
    assert fake.last_session_kwargs["line_items"] == [
        {"price": "price_anual_xyz", "quantity": 1}
    ]
    assert fake.last_session_kwargs["metadata"]["interval"] == "annual"


def test_checkout_monthly_falls_back_to_legacy_price(user_id, monkeypatch):
    """STRIPE_PRICE_ID_PRO (legacy) eh usado se MENSAL nao setado."""
    _, _, client = _auth_user_setup(f"leg-{user_id}")
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_xxx")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_MENSAL", "")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_ANUAL", "")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO", "price_legacy_pro")
    fake = _patch_stripe(monkeypatch)

    resp = client.post("/billing/create-checkout", json={"interval": "monthly"}, headers=_CSRF_HEADERS)
    assert resp.status_code == 200, resp.text
    assert fake.last_session_kwargs["line_items"] == [
        {"price": "price_legacy_pro", "quantity": 1}
    ]


def test_checkout_invalid_interval_returns_400(user_id, monkeypatch):
    _, _, client = _auth_user_setup(f"inv-{user_id}")
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_xxx")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_MENSAL", "price_mensal_abc")

    resp = client.post("/billing/create-checkout", json={"interval": "weekly"}, headers=_CSRF_HEADERS)
    assert resp.status_code == 400
    assert "interval" in resp.json()["detail"].lower()


def test_checkout_returns_503_when_annual_price_missing(user_id, monkeypatch):
    """Anual nao tem fallback — 503 se nao configurado."""
    _, _, client = _auth_user_setup(f"503-{user_id}")
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_xxx")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_MENSAL", "price_mensal_abc")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_ANUAL", "")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO", "price_legacy_pro")

    resp = client.post("/billing/create-checkout", json={"interval": "annual"}, headers=_CSRF_HEADERS)
    assert resp.status_code == 503


def test_checkout_creates_new_customer_with_brazil_country_and_locale(user_id, monkeypatch):
    """Novo customer Stripe nasce com address.country=BR e preferred_locales pt-BR.

    Sem isso, Stripe Checkout sugere USD e formulario em ingles para usuarios
    brasileiros (problema visto em test em 2026-05-10).
    """
    _, _, client = _auth_user_setup(f"br-{user_id}")
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_xxx")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_MENSAL", "price_mensal_abc")
    fake = _patch_stripe(monkeypatch)

    resp = client.post("/billing/create-checkout", headers=_CSRF_HEADERS)
    assert resp.status_code == 200, resp.text

    assert fake.customer_create_calls == 1
    assert fake.last_customer_kwargs is not None
    assert fake.last_customer_kwargs["address"] == {"country": "BR"}
    assert fake.last_customer_kwargs["preferred_locales"] == ["pt-BR"]


def test_checkout_reuses_existing_stripe_customer(user_id, monkeypatch):
    """Se user ja tem stripe_customer_id, nao cria customer novo."""
    uid, _, client = _auth_user_setup(f"reuse-{user_id}")
    db.set_stripe_customer(uid, "cus_existing_999")

    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_xxx")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_MENSAL", "price_mensal_abc")
    fake = _patch_stripe(monkeypatch)

    resp = client.post("/billing/create-checkout", headers=_CSRF_HEADERS)
    assert resp.status_code == 200
    assert fake.customer_create_calls == 0
    assert fake.last_session_kwargs["customer"] == "cus_existing_999"
