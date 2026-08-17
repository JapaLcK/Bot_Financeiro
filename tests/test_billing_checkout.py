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
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

os.environ.setdefault("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())

import db
import frontend.finance_bot_websocket_custom as dashboard


_CSRF_TOKEN = "test-csrf-token-billing"


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """/billing/create-checkout tem rate limit de 20/hora (slowapi, storage em
    memória compartilhado entre testes). Com muitos testes no arquivo, cada um
    fazendo 1-2 POSTs, o teto estoura e o último cai com 429. Zera o storage
    por teste — não afrouxa o limite em produção."""
    try:
        dashboard.limiter._storage.reset()
    except Exception:
        pass
    yield


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


class _FakeStripeError(Exception):
    pass


class _FakeInvalidRequestError(_FakeStripeError):
    def __init__(self, message, param=None, code=None):
        super().__init__(message)
        self.param = param
        self.code = code


class _FakeStripe:
    """Stub de stripe.Customer.create + stripe.checkout.Session.create.

    Captura args via .last_session_kwargs pra os testes assertarem.
    """

    def __init__(self):
        self.api_key = None
        self.last_session_kwargs: dict | None = None
        self.last_customer_kwargs: dict | None = None
        self.customer_create_calls = 0
        self.session_create_calls = 0
        self.session_expire_calls = 0
        self.missing_customer_ids: set[str] = set()
        self.open_sessions: list[dict] = []

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
                outer.session_create_calls += 1
                if kwargs.get("customer") in outer.missing_customer_ids:
                    raise _FakeInvalidRequestError(
                        "No such customer", param="customer", code="resource_missing")
                outer.last_session_kwargs = kwargs
                session_id = f"cs_test_{outer.session_create_calls}"
                session = {
                    "id": session_id,
                    "url": f"https://checkout.stripe.com/c/pay/{session_id}",
                    "customer": kwargs.get("customer"),
                    "metadata": kwargs.get("metadata") or {},
                    "status": "open",
                }
                outer.open_sessions.append(session)
                return SimpleNamespace(id=session_id, url=session["url"])

            @staticmethod
            def list(**kwargs):
                customer = kwargs.get("customer")
                if customer in outer.missing_customer_ids:
                    raise _FakeInvalidRequestError(
                        "No such customer", param="customer", code="resource_missing")
                return {
                    "data": [
                        session for session in outer.open_sessions
                        if session["customer"] == customer and session["status"] == "open"
                    ]
                }

            @staticmethod
            def expire(session_id):
                outer.session_expire_calls += 1
                for session in outer.open_sessions:
                    if session["id"] == session_id:
                        session["status"] = "expired"
                        return session
                raise _FakeInvalidRequestError(
                    "No such checkout session", param="session", code="resource_missing")

        class _Subscription:
            @staticmethod
            def list(**kwargs):
                return {"data": []}

        self.Customer = _Customer
        self.Subscription = _Subscription
        self.checkout = SimpleNamespace(Session=_Session)
        self.error = SimpleNamespace(
            StripeError=_FakeStripeError,
            InvalidRequestError=_FakeInvalidRequestError,
        )


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
    # Trial lido de PRO_TRIAL_DAYS em runtime (default do código = 30). Fixa pra
    # o teste ficar deterministico independente do ambiente.
    monkeypatch.setenv("PRO_TRIAL_DAYS", "7")
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
    # Trial lido de PRO_TRIAL_DAYS em runtime (default do código = 30). Fixa pra
    # o teste ficar deterministico independente do ambiente.
    monkeypatch.setenv("PRO_TRIAL_DAYS", "7")
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


# ─── Guarda anti-assinatura-dupla (fail-closed, achado de review) ────────────

def test_checkout_bloqueia_quem_ja_assina(user_id, monkeypatch):
    """Customer com assinatura ativa → 409 already_subscribed (nunca 2º checkout)."""
    uid, _, client = _auth_user_setup(f"dup-{user_id}")
    db.set_stripe_customer(uid, "cus_ja_assina")
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_xxx")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_MENSAL", "price_mensal_abc")
    fake = _patch_stripe(monkeypatch)

    class _Subscription:
        @staticmethod
        def list(**kwargs):
            return {"data": [{"id": "sub_viva", "schedule": None}]}

    fake.Subscription = _Subscription

    resp = client.post("/billing/create-checkout", headers=_CSRF_HEADERS)
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["error"] == "already_subscribed"
    assert fake.last_session_kwargs is None  # checkout NUNCA foi criado


def test_checkout_fail_closed_com_stripe_fora(user_id, monkeypatch):
    """Se a consulta de assinatura FALHA (API instável), o checkout responde
    503 em vez de assumir 'sem assinatura' e arriscar cobrança dupla."""
    uid, _, client = _auth_user_setup(f"fc-{user_id}")
    db.set_stripe_customer(uid, "cus_stripe_fora")
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_xxx")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_MENSAL", "price_mensal_abc")
    fake = _patch_stripe(monkeypatch)

    class _SubscriptionBoom:
        @staticmethod
        def list(**kwargs):
            raise RuntimeError("stripe 500")

    fake.Subscription = _SubscriptionBoom

    resp = client.post("/billing/create-checkout", headers=_CSRF_HEADERS)
    assert resp.status_code == 503, resp.text
    assert fake.last_session_kwargs is None  # nada de checkout no escuro


def test_checkout_recupera_customer_apagado_no_stripe(user_id, monkeypatch):
    """Customer inexistente não é pane da API: recria e conclui o checkout."""
    uid, _, client = _auth_user_setup(f"missing-{user_id}")
    db.set_stripe_customer(uid, "cus_apagado")
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_xxx")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_MENSAL", "price_mensal_abc")
    fake = _patch_stripe(monkeypatch)
    fake.missing_customer_ids.add("cus_apagado")

    class _MissingCustomerSubscription:
        @staticmethod
        def list(**kwargs):
            raise _FakeInvalidRequestError(
                "No such customer", param="customer", code="resource_missing")

    fake.Subscription = _MissingCustomerSubscription

    resp = client.post("/billing/create-checkout", headers=_CSRF_HEADERS)
    assert resp.status_code == 200, resp.text
    assert fake.customer_create_calls == 1
    assert fake.session_create_calls == 1
    assert fake.last_session_kwargs["customer"] == "cus_test_123"
    assert db.get_auth_user(uid)["stripe_customer_id"] == "cus_test_123"


def test_checkout_sem_customer_segue_normal(user_id, monkeypatch):
    """Usuário sem stripe_customer_id (nunca assinou) não consulta assinatura
    e cria checkout normalmente — o caminho feliz continua intacto."""
    _, _, client = _auth_user_setup(f"novo-{user_id}")
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_xxx")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_MENSAL", "price_mensal_abc")
    fake = _patch_stripe(monkeypatch)

    resp = client.post("/billing/create-checkout", headers=_CSRF_HEADERS)
    assert resp.status_code == 200, resp.text
    assert fake.last_session_kwargs is not None


# ─── Trial v2 (2026-08-06): 30d do plano escolhido, gated por elegibilidade ──

def test_checkout_v2_elegivel_manda_trial_de_30(user_id, monkeypatch):
    """v2 ON + telefone elegível → trial_period_days = PLANS_TRIAL_DAYS (não mais
    PRO_TRIAL_DAYS nem os dias restantes de um trial de telefone)."""
    _, _, client = _auth_user_setup(f"v2elig-{user_id}")
    monkeypatch.setenv("PLANS_V2_ENABLED", "1")
    monkeypatch.setenv("PLANS_TRIAL_DAYS", "30")
    monkeypatch.setenv("PRO_TRIAL_DAYS", "7")  # deve ser IGNORADO no caminho v2
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_xxx")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_MENSAL", "price_mensal_abc")
    monkeypatch.setattr("db.plans.is_trial_eligible_for_user", lambda uid: True)
    fake = _patch_stripe(monkeypatch)

    resp = client.post("/billing/create-checkout", headers=_CSRF_HEADERS)
    assert resp.status_code == 200, resp.text
    assert fake.last_session_kwargs["subscription_data"]["trial_period_days"] == 30


def test_checkout_v2_inelegivel_cobra_na_hora(user_id, monkeypatch):
    """v2 ON + telefone que já queimou o trial → sem trial_period_days (Stripe
    cobra imediatamente). Regra: 1 trial por telefone na vida."""
    _, _, client = _auth_user_setup(f"v2inelig-{user_id}")
    monkeypatch.setenv("PLANS_V2_ENABLED", "1")
    monkeypatch.setenv("PLANS_TRIAL_DAYS", "30")
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_xxx")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_MENSAL", "price_mensal_abc")
    monkeypatch.setattr("db.plans.is_trial_eligible_for_user", lambda uid: False)
    fake = _patch_stripe(monkeypatch)

    resp = client.post("/billing/create-checkout", headers=_CSRF_HEADERS)
    assert resp.status_code == 200, resp.text
    assert "trial_period_days" not in fake.last_session_kwargs["subscription_data"]


def test_checkout_v2_falha_fechada_se_elegibilidade_indisponivel(user_id, monkeypatch):
    """Sem conseguir decidir o trial, não cobra nem concede benefício no escuro."""
    _, _, client = _auth_user_setup(f"v2fail-{user_id}")
    monkeypatch.setenv("PLANS_V2_ENABLED", "1")
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_xxx")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_MENSAL", "price_mensal_abc")

    def fail(_uid):
        from db.plans import TrialEligibilityError
        raise TrialEligibilityError("db fora")

    monkeypatch.setattr("db.plans.is_trial_eligible_for_user", fail)
    fake = _patch_stripe(monkeypatch)

    resp = client.post("/billing/create-checkout", headers=_CSRF_HEADERS)

    assert resp.status_code == 503
    assert fake.session_create_calls == 0


def test_checkout_concorrente_reutiliza_uma_unica_sessao(user_id, monkeypatch):
    """Duas requisições simultâneas recebem a mesma URL e criam só 1 sessão."""
    uid, email, client_a = _auth_user_setup(f"race-{user_id}")
    client_b = TestClient(dashboard.app)
    client_b.cookies.set(dashboard.AUTH_COOKIE_NAME, dashboard._make_jwt(uid, email))
    client_b.cookies.set(dashboard.CSRF_COOKIE_NAME, _CSRF_TOKEN)
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_xxx")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_MENSAL", "price_mensal_abc")
    fake = _patch_stripe(monkeypatch)

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(
            lambda client: client.post("/billing/create-checkout", headers=_CSRF_HEADERS),
            (client_a, client_b),
        ))

    assert [response.status_code for response in responses] == [200, 200]
    assert responses[0].json()["checkout_url"] == responses[1].json()["checkout_url"]
    assert fake.session_create_calls == 1


def test_checkout_novo_plano_expira_sessao_aberta_incompativel(user_id, monkeypatch):
    """Mudar a escolha mensal/anual invalida o checkout antigo antes do novo."""
    _, _, client = _auth_user_setup(f"replace-{user_id}")
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_xxx")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_MENSAL", "price_mensal_abc")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_ANUAL", "price_anual_xyz")
    fake = _patch_stripe(monkeypatch)

    first = client.post("/billing/create-checkout", headers=_CSRF_HEADERS)
    second = client.post(
        "/billing/create-checkout",
        json={"interval": "annual"},
        headers=_CSRF_HEADERS,
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["checkout_url"] != second.json()["checkout_url"]
    assert fake.session_create_calls == 2
    assert fake.session_expire_calls == 1


def test_checkout_grava_started_no_funil_com_session_id(user_id, monkeypatch):
    """Abrir o checkout com sucesso grava um 'started' na tabela dedicada
    checkout_funnel_events, com o session_id do Stripe (o que permite
    correlacionar com o 'completed' do webhook). O session_id NÃO vaza no
    payload devolvido ao cliente."""
    from db import get_conn

    uid, _, client = _auth_user_setup(f"funnel-{user_id}")
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_xxx")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_MENSAL", "price_mensal_abc")
    _patch_stripe(monkeypatch)

    resp = client.post(
        "/billing/create-checkout", json={"interval": "monthly"}, headers=_CSRF_HEADERS
    )
    assert resp.status_code == 200, resp.text
    assert "session_id" not in resp.json(), "session_id não pode vazar pro cliente"

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select session_id, kind from checkout_funnel_events "
                "where user_id = %s and kind = 'started' order by id desc limit 1",
                (uid,),
            )
            row = cur.fetchone()
    assert row is not None, "'started' não foi gravado na tabela do funil"
    assert row["session_id"] and row["session_id"].startswith("cs_test_")


def test_checkout_falho_nao_grava_started(user_id, monkeypatch):
    """Sessão que não nasce (Stripe não configurado) não polui o funil."""
    from db import get_conn

    uid, _, client = _auth_user_setup(f"nofunnel-{user_id}")
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "")  # 503: pagamentos off
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_MENSAL", "price_mensal_abc")

    resp = client.post("/billing/create-checkout", headers=_CSRF_HEADERS)
    assert resp.status_code == 503

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select count(*) as n from checkout_funnel_events "
                "where user_id = %s and kind = 'started'",
                (uid,),
            )
            n = cur.fetchone()["n"]
    assert n == 0


def test_checkout_reaproveitado_propaga_session_id_no_funil(user_id, monkeypatch):
    """P2: quando o checkout REAPROVEITA uma sessão aberta, o 'started' precisa
    carregar o session_id da sessão reusada — senão iria NULL e a conclusão
    dessa sessão nunca correlacionaria no funil."""
    from db import get_conn

    uid, _, client = _auth_user_setup(f"reuse-funnel-{user_id}")
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_xxx")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_MENSAL", "price_mensal_abc")
    fake = _patch_stripe(monkeypatch)

    r1 = client.post("/billing/create-checkout", headers=_CSRF_HEADERS)
    r2 = client.post("/billing/create-checkout", headers=_CSRF_HEADERS)
    assert r1.status_code == 200 and r2.status_code == 200
    # 2ª chamada reaproveitou a sessão da 1ª (não criou nova)
    assert fake.session_create_calls == 1
    assert r1.json()["checkout_url"] == r2.json()["checkout_url"]

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select session_id from checkout_funnel_events "
                "where user_id = %s and kind = 'started' order by id",
                (uid,),
            )
            sids = [row["session_id"] for row in cur.fetchall()]
    # dois 'started' (criação + reuso), AMBOS com o mesmo session_id não-nulo
    assert len(sids) == 2
    assert all(s and s.startswith("cs_test_") for s in sids), sids
    assert sids[0] == sids[1]
