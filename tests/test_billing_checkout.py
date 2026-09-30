"""
tests/test_billing_checkout.py — POST /billing/create-checkout.

Cobre:
- interval ausente == monthly e usa STRIPE_PRICE_ID_PRO_MENSAL (`plan` é obrigatório
  desde #352: sem ele é 400, não uma compra de Plus em silêncio)
- interval=annual usa STRIPE_PRICE_ID_PRO_ANUAL
- interval=monthly cai no fallback STRIPE_PRICE_ID_PRO se MENSAL nao setado
- interval invalido retorna 400, e `"ANNUAL"` NÃO é inválido (mesma
  normalização da /billing/change-plan)
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
                # Como na API: `url` só no hospedado, `client_secret` só no embutido.
                embutido = kwargs.get("ui_mode") == "embedded_page"
                session = {
                    "id": session_id,
                    "url": None if embutido else f"https://checkout.stripe.com/c/pay/{session_id}",
                    "client_secret": f"{session_id}_secret_x" if embutido else None,
                    "ui_mode": kwargs.get("ui_mode"),
                    "customer": kwargs.get("customer"),
                    "metadata": kwargs.get("metadata") or {},
                    "status": "open",
                }
                outer.open_sessions.append(session)
                return SimpleNamespace(id=session_id, url=session["url"],
                                       client_secret=session["client_secret"])

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


def test_checkout_omitted_interval_uses_monthly_price(user_id, monkeypatch):
    """`interval` ausente cai no default do modelo (`monthly`). O nome já disse
    só `default`, quando `plan` também tinha um; hoje `plan` é obrigatório
    (#352) e o único default que sobrou é este."""
    _, _, client = _auth_user_setup(f"def-{user_id}")
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_xxx")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_MENSAL", "price_mensal_abc")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_ANUAL", "price_anual_xyz")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO", "")
    fake = _patch_stripe(monkeypatch)

    resp = client.post("/billing/create-checkout", json={"plan": "plus"}, headers=_CSRF_HEADERS)
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
    # Locale pt-BR forca interface em portugues e moeda BRL no Checkout
    assert fake.last_session_kwargs["locale"] == "pt-BR"


def test_checkout_v1_trial_vem_de_pro_trial_days(user_id, monkeypatch):
    """v1 (freio PLANS_V2_ENABLED=0): o trial é PRO_TRIAL_DAYS, garantido pelo
    backend (o price não traz trial no Stripe novo). Apagar na Fase 2."""
    _, _, client = _auth_user_setup(f"v1trial-{user_id}")
    monkeypatch.setenv("PLANS_V2_ENABLED", "0")
    monkeypatch.setenv("PRO_TRIAL_DAYS", "7")
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_xxx")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_MENSAL", "price_mensal_abc")
    fake = _patch_stripe(monkeypatch)

    resp = client.post("/billing/create-checkout", json={"plan": "plus"}, headers=_CSRF_HEADERS)
    assert resp.status_code == 200, resp.text
    assert fake.last_session_kwargs["subscription_data"]["trial_period_days"] == 7


def test_checkout_annual_uses_annual_price(user_id, monkeypatch):
    _, _, client = _auth_user_setup(f"ann-{user_id}")
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_xxx")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_MENSAL", "price_mensal_abc")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_ANUAL", "price_anual_xyz")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO", "")
    # Trial lido de PRO_TRIAL_DAYS em runtime (default do código = 15). Fixa pra
    # o teste ficar deterministico independente do ambiente.
    monkeypatch.setenv("PRO_TRIAL_DAYS", "7")
    fake = _patch_stripe(monkeypatch)

    resp = client.post("/billing/create-checkout", json={"plan": "plus", "interval": "annual"}, headers=_CSRF_HEADERS)
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

    resp = client.post("/billing/create-checkout", json={"plan": "plus", "interval": "monthly"}, headers=_CSRF_HEADERS)
    assert resp.status_code == 200, resp.text
    assert fake.last_session_kwargs["line_items"] == [
        {"price": "price_legacy_pro", "quantity": 1}
    ]


@pytest.mark.parametrize("interval", ["weekly", ""], ids=["weekly", "vazio"])
def test_checkout_invalid_interval_returns_400(request, user_id, monkeypatch, interval):
    """`""` entra aqui pelo mesmo motivo de `plan` (#352): um `or "monthly"`
    nesta rota transformava valor VAZIO em venda MENSAL silenciosa — 200 com
    `interval: "monthly"` num corpo que não escolheu ciclo nenhum.

    CONTROLE NEGATIVO do caso `vazio`: em `billing_create_checkout`
    (`frontend/finance_bot_websocket_custom.py`) troque `payload.interval.lower()`
    por `(payload.interval or "monthly").lower()` e
    `test_checkout_invalid_interval_returns_400[vazio]` fica VERMELHO em 200.
    Injetado num caso VERDE — `weekly` é 400 com e sem o `or`.

    A sessão zero é o que dá dinheiro à medição: 400 sozinho também sairia de
    uma validação depois de a cobrança nascer.
    """
    _, _, client = _auth_user_setup(f"inv-{request.node.callspec.id}-{user_id}")
    fake = _stripe_pronto(monkeypatch)

    resp = client.post("/billing/create-checkout", json={"plan": "plus", "interval": interval}, headers=_CSRF_HEADERS)
    assert resp.status_code == 400, resp.text
    assert "interval" in resp.json()["detail"].lower()
    assert fake.session_create_calls == 0, "recusa de interval abriu checkout no Stripe"


def _stripe_pronto(monkeypatch):
    """Stripe inteiramente configurado: mensal, anual e legado com price ID.

    É o que impede um 400 de plano de ser confundido com o 503 de "pagamentos
    não configurados" — com todos os preços no lugar, 503 aqui seria bug.
    """
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_xxx")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_MENSAL", "price_mensal_abc")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_ANUAL", "price_anual_xyz")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO", "price_legacy_pro")
    return _patch_stripe(monkeypatch)


@pytest.mark.parametrize("corpo", [
    None,                      # POST sem body nenhum
    {},                        # body vazio
    {"interval": "annual"},    # o caso do relato: interval sem plan
    {"plan": ""},
    # `"   "` NÃO discrimina: sob a mutação do bug (`or "plus"`), `"   " or
    # "plus"` devolve `"   "`, que já era 400 antes do conserto. Fica como
    # companhia do grupo, e não pode ser citado como prova de nada (§7).
    {"plan": "   "},
], ids=["sem-body", "body-vazio", "so-interval", "plan-vazio", "plan-so-espacos"])
def test_checkout_sem_plano_e_400_e_nao_vende_plus(request, user_id, monkeypatch, corpo):
    """Sem plano no corpo, a rota RECUSA — não vende Plus em silêncio (#352).

    O default histórico era `("" or "plus")`: um bug no JS que parasse de mandar
    o campo faria todo mundo comprar Plus sem escolher, e o sintoma ("as vendas
    migraram pro Plus") não aponta pra cá.

    A asserção de sessão zero é o que dá dinheiro à medição: 400 sozinho também
    sairia de um erro de validação depois da cobrança nascer.
    """
    _, _, client = _auth_user_setup(f"{request.node.callspec.id}-{user_id}")
    fake = _stripe_pronto(monkeypatch)

    kwargs = {"headers": _CSRF_HEADERS}
    if corpo is not None:
        kwargs["json"] = corpo
    resp = client.post("/billing/create-checkout", **kwargs)

    assert resp.status_code == 400, resp.text
    assert "plan" in str(resp.json()["detail"]).lower()
    assert fake.session_create_calls == 0, "recusa de plano abriu checkout no Stripe"


def test_checkout_com_plano_explicito_continua_vendendo(user_id, monkeypatch):
    """POSITIVO do grupo: `plan` explícito segue abrindo o checkout.

    Sem ele o grupo passaria numa rota que recusasse tudo — pior que o bug.
    """
    _, _, client = _auth_user_setup(f"complan-{user_id}")
    fake = _stripe_pronto(monkeypatch)

    resp = client.post("/billing/create-checkout",
                       json={"plan": "plus", "interval": "annual"},
                       headers=_CSRF_HEADERS)
    assert resp.status_code == 200, resp.text
    assert resp.json()["plan"] == "plus"
    assert fake.session_create_calls == 1


def test_checkout_aceita_plano_com_espacos_como_o_pix(user_id, monkeypatch):
    """`" plus "` era 200 no Pix (`.strip().lower()`) e 400 aqui (só `.lower()`),
    com UM só JS alimentando as duas rotas. As gêmeas normalizam igual."""
    _, _, client = _auth_user_setup(f"espacos-{user_id}")
    fake = _stripe_pronto(monkeypatch)

    resp = client.post("/billing/create-checkout",
                       json={"plan": " plus ", "interval": "annual"},
                       headers=_CSRF_HEADERS)
    assert resp.status_code == 200, resp.text
    assert resp.json()["plan"] == "plus"
    assert fake.session_create_calls == 1


def test_checkout_aceita_interval_em_caixa_alta_como_a_troca(user_id, monkeypatch):
    """`interval` normaliza IGUAL à `/billing/change-plan` (§0.7): `"ANNUAL"`
    era 400 aqui e 409 lá, com UM só JS alimentando as duas.

    A asserção é o PRICE ID, não só o status: um `annual` mal normalizado que
    caísse em `monthly` sairia 200 e venderia o plano mensal com cara de acerto.

    CONTROLE NEGATIVO deste caso: troque a linha do `interval` na rota
    (`frontend/finance_bot_websocket_custom.py`, `billing_create_checkout`) de
    `payload.interval.lower()` por `payload.interval` e este teste fica VERMELHO
    em 400 `interval inválido`. Injetado num caso VERDE — `annual` sem espaços
    passa com e sem o conserto (`test_checkout_annual_uses_annual_price` é ele).
    """
    _, _, client = _auth_user_setup(f"interval-{user_id}")
    fake = _stripe_pronto(monkeypatch)

    resp = client.post("/billing/create-checkout",
                       json={"plan": "plus", "interval": "ANNUAL"},
                       headers=_CSRF_HEADERS)
    assert resp.status_code == 200, resp.text
    assert resp.json()["interval"] == "annual"
    assert fake.last_session_kwargs["line_items"] == [
        {"price": "price_anual_xyz", "quantity": 1}
    ]


def test_checkout_returns_503_when_annual_price_missing(user_id, monkeypatch):
    """Anual nao tem fallback — 503 se nao configurado."""
    _, _, client = _auth_user_setup(f"503-{user_id}")
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_xxx")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_MENSAL", "price_mensal_abc")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_ANUAL", "")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO", "price_legacy_pro")

    resp = client.post("/billing/create-checkout", json={"plan": "plus", "interval": "annual"}, headers=_CSRF_HEADERS)
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

    resp = client.post("/billing/create-checkout", json={"plan": "plus"}, headers=_CSRF_HEADERS)
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

    resp = client.post("/billing/create-checkout", json={"plan": "plus"}, headers=_CSRF_HEADERS)
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

    resp = client.post("/billing/create-checkout", json={"plan": "plus"}, headers=_CSRF_HEADERS)
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

    resp = client.post("/billing/create-checkout", json={"plan": "plus"}, headers=_CSRF_HEADERS)
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

    resp = client.post("/billing/create-checkout", json={"plan": "plus"}, headers=_CSRF_HEADERS)
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

    resp = client.post("/billing/create-checkout", json={"plan": "plus"}, headers=_CSRF_HEADERS)
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

    resp = client.post("/billing/create-checkout", json={"plan": "plus"}, headers=_CSRF_HEADERS)
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

    resp = client.post("/billing/create-checkout", json={"plan": "plus"}, headers=_CSRF_HEADERS)
    assert resp.status_code == 200, resp.text
    assert "trial_period_days" not in fake.last_session_kwargs["subscription_data"]


def _success_params(fake) -> dict:
    from urllib.parse import parse_qs, urlparse
    return {k: v[0] for k, v in parse_qs(urlparse(fake.last_session_kwargs["success_url"]).query).items()}


def test_success_url_leva_cota_de_ia_do_plano_comprado(user_id, monkeypatch):
    """A tela de confirmação prometia "Piggy IA sem limite de mensagens" pra
    Plus e Pro, mas `ai_monthly_messages: None` cai no teto AI_CHAT_MONTHLY_LIMIT
    e o chat corta ali. O número tem que sair do backend, junto do `td` e do
    `pl` — chumbar no HTML é o bug que o `td` já tinha resolvido."""
    import core.services.ai_chat_commands as aicc
    _, _, client = _auth_user_setup(f"iaquota-{user_id}")
    monkeypatch.setenv("PLANS_V2_ENABLED", "1")
    monkeypatch.setattr(aicc, "AI_CHAT_MONTHLY_LIMIT", 777)
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_xxx")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_MENSAL", "price_mensal_abc")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_ESSENCIAL_MENSAL", "price_ess_abc")
    monkeypatch.setattr("db.plans.is_trial_eligible_for_user", lambda uid: True)
    fake = _patch_stripe(monkeypatch)

    resp = client.post("/billing/create-checkout", json={"plan": "plus"}, headers=_CSRF_HEADERS)
    assert resp.status_code == 200, resp.text
    params = _success_params(fake)
    assert params["pl"] == "plus"
    assert params["ia"] == "777", "cota do Plus tem que ser o teto global, não 'ilimitado'"

    resp = client.post("/billing/create-checkout", json={"plan": "essencial"}, headers=_CSRF_HEADERS)
    assert resp.status_code == 200, resp.text
    params = _success_params(fake)
    assert params["pl"] == "essencial"
    assert params["ia"] == "200", "Essencial tem cota própria (200), menor que o teto"


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

    resp = client.post("/billing/create-checkout", json={"plan": "plus"}, headers=_CSRF_HEADERS)

    assert resp.status_code == 503
    assert fake.session_create_calls == 0


@pytest.mark.parametrize("corpo,chave", [
    ({"plan": "plus"}, "checkout_url"),
    ({"plan": "plus", "origem": "assinar", "embutido": True}, "client_secret"),
], ids=["precos", "assinar-embutido"])
def test_checkout_concorrente_reutiliza_uma_unica_sessao(user_id, monkeypatch, corpo, chave):
    """Duas requisições simultâneas recebem a mesma sessão e criam só 1."""
    uid, email, client_a = _auth_user_setup(f"race-{user_id}")
    client_b = TestClient(dashboard.app)
    client_b.cookies.set(dashboard.AUTH_COOKIE_NAME, dashboard._make_jwt(uid, email))
    client_b.cookies.set(dashboard.CSRF_COOKIE_NAME, _CSRF_TOKEN)
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_xxx")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_MENSAL", "price_mensal_abc")
    monkeypatch.setattr(dashboard, "STRIPE_PUBLISHABLE_KEY", "pk_test_abc")
    fake = _patch_stripe(monkeypatch)

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(
            lambda client: client.post("/billing/create-checkout", json=corpo, headers=_CSRF_HEADERS),
            (client_a, client_b),
        ))

    assert [response.status_code for response in responses] == [200, 200]
    assert responses[0].json()[chave] == responses[1].json()[chave]
    assert responses[0].json()[chave]
    assert fake.session_create_calls == 1


def test_checkout_novo_plano_expira_sessao_aberta_incompativel(user_id, monkeypatch):
    """Mudar a escolha mensal/anual invalida o checkout antigo antes do novo."""
    _, _, client = _auth_user_setup(f"replace-{user_id}")
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_xxx")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_MENSAL", "price_mensal_abc")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_ANUAL", "price_anual_xyz")
    fake = _patch_stripe(monkeypatch)

    first = client.post("/billing/create-checkout", json={"plan": "plus"}, headers=_CSRF_HEADERS)
    second = client.post(
        "/billing/create-checkout",
        json={"plan": "plus", "interval": "annual"},
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
        "/billing/create-checkout", json={"plan": "plus", "interval": "monthly"}, headers=_CSRF_HEADERS
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

    resp = client.post("/billing/create-checkout", json={"plan": "plus"}, headers=_CSRF_HEADERS)
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

    r1 = client.post("/billing/create-checkout", json={"plan": "plus"}, headers=_CSRF_HEADERS)
    r2 = client.post("/billing/create-checkout", json={"plan": "plus"}, headers=_CSRF_HEADERS)
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


def test_checkout_nao_fecha_gate_da_precos_na_abertura(user_id, monkeypatch):
    """Item #2: abrir o checkout NÃO marca plan_selected_at — só o webhook
    (pagamento completo) fecha o gate. Assim o abandonador continua obrigado a
    escolher um plano na /precos. Confere também as URLs de retorno."""
    from db import get_conn

    uid, _, client = _auth_user_setup(f"gate-{user_id}")
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_xxx")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_MENSAL", "price_mensal_abc")
    fake = _patch_stripe(monkeypatch)

    resp = client.post("/billing/create-checkout", json={"plan": "plus"}, headers=_CSRF_HEADERS)
    assert resp.status_code == 200, resp.text

    # plan_selected_at continua NULL — o gate NÃO foi fechado na abertura
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select plan_selected_at from auth_accounts where user_id = %s", (uid,))
            assert cur.fetchone()["plan_selected_at"] is None

    # Abandono volta pra /precos (escolha forçada); sucesso pra /home?upgrade=success
    assert fake.last_session_kwargs["cancel_url"].endswith("/precos?escolha=1")
    assert "upgrade=success" in fake.last_session_kwargs["success_url"]


# ─── /assinar: checkout embutido e hospedado (funil v3, PR 2) ────────────────

_ASSINAR_E = {"plan": "plus", "origem": "assinar", "embutido": True}
_ASSINAR_H = {"plan": "plus", "origem": "assinar"}
_PRECOS = {"plan": "plus"}
_KWARGS_DA_PRECOS = {
    "customer", "payment_method_types", "line_items", "mode", "locale",
    "allow_promotion_codes", "success_url", "cancel_url", "metadata", "subscription_data",
}


def _assinar_pronto(monkeypatch, elegivel=True, ebook="price_ebook_abc", pk="pk_test_abc",
                    ebook_url="https://exemplo.test/ebook.pdf"):
    fake = _stripe_pronto(monkeypatch)
    monkeypatch.setenv("PLANS_V2_ENABLED", "1")
    monkeypatch.setenv("PLANS_TRIAL_DAYS", "15")
    monkeypatch.setattr("db.plans.is_trial_eligible_for_user", lambda uid: elegivel)
    monkeypatch.setattr(dashboard, "STRIPE_PUBLISHABLE_KEY", pk)
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_EBOOK", ebook)
    monkeypatch.setattr(dashboard, "EBOOK_URL", ebook_url)
    return fake


def _post(client, corpo):
    return client.post("/billing/create-checkout", json=corpo, headers=_CSRF_HEADERS)


@pytest.mark.parametrize("corpo", [
    _PRECOS, {**_PRECOS, "origem": "precos", "embutido": False},
], ids=["sem-campos-novos", "campos-novos-explicitos"])
def test_precos_manda_o_mesmo_checkout_de_antes(request, user_id, monkeypatch, corpo):
    """POSITIVO do PR: a /precos não herda nada da /assinar. A igualdade de
    CONJUNTO pega qualquer kwarg que vaze (ui_mode, adaptive_pricing,
    optional_items, expires_at, return_url)."""
    uid, _, client = _auth_user_setup(f"precos-{request.node.callspec.id}-{user_id}")
    fake = _assinar_pronto(monkeypatch)

    resp = _post(client, corpo)
    assert resp.status_code == 200, resp.text
    kw = fake.last_session_kwargs
    assert set(kw) == _KWARGS_DA_PRECOS
    assert kw["cancel_url"].endswith("/precos?escolha=1")
    assert kw["metadata"] == {
        "finbot_user_id": str(uid), "interval": "monthly", "plan": "plus",
        "price_id": "price_mensal_abc", "origem": "precos", "td": "15",
    }
    assert set(resp.json()) == {"checkout_url", "interval", "plan"}


def _query(url: str) -> dict:
    from urllib.parse import parse_qs, urlparse
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


def test_assinar_embutido_kwargs_e_resposta(user_id, monkeypatch):
    import time
    _, _, client = _auth_user_setup(f"assinar-e-{user_id}")
    fake = _assinar_pronto(monkeypatch)

    agora = int(time.time())
    resp = _post(client, _ASSINAR_E)
    assert resp.status_code == 200, resp.text
    kw = fake.last_session_kwargs
    assert kw["ui_mode"] == "embedded_page"
    assert "success_url" not in kw and "cancel_url" not in kw
    assert "/home?" in kw["return_url"]
    q = _query(kw["return_url"])
    assert (q["upgrade"], q["sid"], q["ev"], q["td"], q["pl"]) == (
        "success", "{CHECKOUT_SESSION_ID}", "trial", "15", "plus")
    assert "ia" in q
    assert agora + 3500 <= kw["expires_at"] <= agora + 3700
    assert kw["adaptive_pricing"] == {"enabled": False}
    assert kw["allow_promotion_codes"] is True
    assert kw["customer"] == "cus_test_123" and "customer_email" not in kw
    assert kw["optional_items"] == [{"price": "price_ebook_abc", "quantity": 1}]
    assert kw["metadata"]["origem"] == "assinar"
    assert kw["subscription_data"]["metadata"]["origem"] == "assinar"

    corpo = resp.json()
    assert set(corpo) == {"client_secret", "publishable_key", "trial_days", "interval", "plan"}
    assert corpo["client_secret"] == "cs_test_1_secret_x"
    assert corpo["publishable_key"] == "pk_test_abc"
    assert corpo["trial_days"] == 15


@pytest.mark.parametrize("ebook,ebook_url,oferece", [
    ("price_ebook_abc", "", False),
    ("", "https://exemplo.test/ebook.pdf", False),
    ("price_ebook_abc", "https://exemplo.test/ebook.pdf", True),
], ids=["so-o-preco", "so-a-url", "as-duas"])
@pytest.mark.parametrize("corpo", [_ASSINAR_E, _ASSINAR_H], ids=["embutido", "hospedado"])
def test_assinar_ebook_so_com_as_duas_envs(request, user_id, monkeypatch, corpo, ebook, ebook_url, oferece):
    """Preço sem URL venderia um e-book que o webhook não tem como entregar.
    Quando oferecido, o preço vai de foto nos DOIS metadatas (o PR 3 lê dali);
    quando não, a chave nem existe."""
    _, _, client = _auth_user_setup(f"ebook-{request.node.callspec.id}-{user_id}")
    fake = _assinar_pronto(monkeypatch, ebook=ebook, ebook_url=ebook_url)

    assert _post(client, corpo).status_code == 200
    kw = fake.last_session_kwargs
    for meta in (kw["metadata"], kw["subscription_data"]["metadata"]):
        if oferece:
            assert meta["ebook_price"] == "price_ebook_abc"
        else:
            assert "ebook_price" not in meta
    if oferece:
        assert kw["optional_items"] == [{"price": "price_ebook_abc", "quantity": 1}]
    else:
        assert "optional_items" not in kw


def test_assinar_hospedado_plano_b(user_id, monkeypatch):
    """O plano B da /assinar: mesmo trial, mesma volta de sucesso do embutido,
    e o abandono volta para a /assinar com a escolha."""
    import time
    _, _, client = _auth_user_setup(f"assinar-h-{user_id}")
    fake = _assinar_pronto(monkeypatch)

    assert _post(client, _ASSINAR_E).status_code == 200
    embutido = fake.last_session_kwargs
    agora = int(time.time())
    resp = _post(client, _ASSINAR_H)
    assert resp.status_code == 200, resp.text
    kw = fake.last_session_kwargs
    assert kw is not embutido
    assert "ui_mode" not in kw and "return_url" not in kw
    # 1 h também no hospedado: o default de 24 h do Stripe deixaria aberta a
    # janela de cobrança dupla (Pix numa aba, cartão na outra).
    assert agora + 3500 <= kw["expires_at"] <= agora + 3700
    assert kw["success_url"] == embutido["return_url"]
    assert kw["cancel_url"].endswith("/assinar?plano=plus&ciclo=monthly")
    assert kw["optional_items"] == [{"price": "price_ebook_abc", "quantity": 1}]
    assert kw["adaptive_pricing"] == {"enabled": False}
    assert kw["allow_promotion_codes"] is True
    assert (kw["subscription_data"]["trial_period_days"]
            == embutido["subscription_data"]["trial_period_days"] == 15)
    assert set(resp.json()) == {"checkout_url", "interval", "plan"}
    assert resp.json()["checkout_url"].startswith("https://checkout.stripe.com/")


@pytest.mark.parametrize("origem", ["outra", "", "ASSINAR"])
def test_origem_invalida_400_sem_tocar_no_stripe(request, user_id, monkeypatch, origem):
    _, _, client = _auth_user_setup(f"origem-{request.node.callspec.id}-{user_id}")
    fake = _assinar_pronto(monkeypatch)

    resp = _post(client, {**_ASSINAR_E, "origem": origem})
    assert resp.status_code == 400, resp.text
    assert "origem" in resp.json()["detail"]
    assert (fake.session_create_calls, fake.customer_create_calls) == (0, 0)


def test_embutido_sem_chave_publicavel_503_antes_do_stripe(user_id, monkeypatch):
    _, _, client = _auth_user_setup(f"sem-pk-{user_id}")
    fake = _assinar_pronto(monkeypatch, pk="")

    resp = _post(client, _ASSINAR_E)
    assert resp.status_code == 503, resp.text
    assert (fake.session_create_calls, fake.customer_create_calls) == (0, 0)


def test_hospedado_da_assinar_nao_precisa_da_chave_publicavel(user_id, monkeypatch):
    """POSITIVO do 503 acima: a chave só é exigida do embutido."""
    _, _, client = _auth_user_setup(f"sem-pk-h-{user_id}")
    fake = _assinar_pronto(monkeypatch, pk="")

    assert _post(client, _ASSINAR_H).status_code == 200
    assert fake.session_create_calls == 1


@pytest.mark.parametrize("elegivel,dias", [(True, 15), (False, 0)], ids=["elegivel", "inelegivel"])
def test_assinar_embutido_trial_segue_a_elegibilidade(request, user_id, monkeypatch, elegivel, dias):
    _, _, client = _auth_user_setup(f"trial-{request.node.callspec.id}-{user_id}")
    fake = _assinar_pronto(monkeypatch, elegivel=elegivel)

    resp = _post(client, _ASSINAR_E)
    assert resp.status_code == 200, resp.text
    assert resp.json()["trial_days"] == dias
    assert fake.last_session_kwargs["metadata"]["td"] == str(dias)
    assert fake.last_session_kwargs["subscription_data"].get("trial_period_days") == (dias or None)


_SEMEADA = "cs_semeada"
# (sessão aberta semeada, pedido, reaproveita?) — uma linha por célula do plano.
# Semeadas: A-E = embutida da /assinar; A-H = hospedada da /assinar; P-H =
# hospedada da /precos; V-H = hospedada anterior ao deploy (sem origem/td).
_REUSO = {
    "a-AE+embutido": (dict(origem="assinar", td="0", embutida=True), _ASSINAR_E, True),
    "b-AE+assinar-hospedado": (dict(origem="assinar", td="15", embutida=True), _ASSINAR_H, False),
    "c-PH+assinar-embutido": (dict(origem="precos", td="15"), _ASSINAR_E, False),
    "d-AH+precos": (dict(origem="assinar", td="15"), _PRECOS, False),
    "e-VH+precos": (dict(), _PRECOS, True),
    "f-VH+assinar-hospedado": (dict(), _ASSINAR_H, False),
    "g-AH+assinar-embutido": (dict(origem="assinar", td="15"), _ASSINAR_E, False),
    "h-AE-sem-td+embutido": (dict(origem="assinar", embutida=True), _ASSINAR_E, False),
    "i-AH+assinar-hospedado": (dict(origem="assinar", td="15"), _ASSINAR_H, True),
}


@pytest.mark.parametrize("caso", list(_REUSO))
def test_reaproveitamento_por_origem_e_modo(user_id, monkeypatch, caso):
    semente, corpo, reaproveita = _REUSO[caso]
    semente = dict(semente)
    uid, _, client = _auth_user_setup(f"reuso-{caso}-{user_id}")
    db.set_stripe_customer(uid, "cus_test_123")
    fake = _assinar_pronto(monkeypatch, elegivel=True)
    embutida = semente.pop("embutida", False)
    metadata = {"finbot_user_id": str(uid), "interval": "monthly", "plan": "plus",
                "price_id": "price_mensal_abc", **semente}
    fake.open_sessions.append({
        "id": _SEMEADA, "customer": "cus_test_123", "status": "open", "metadata": metadata,
        "url": None if embutida else f"https://checkout.stripe.com/c/pay/{_SEMEADA}",
        "client_secret": f"{_SEMEADA}_secret_x" if embutida else None,
    })

    resp = _post(client, corpo)
    assert resp.status_code == 200, resp.text
    chave = "client_secret" if corpo.get("embutido") else "checkout_url"
    if reaproveita:
        assert (fake.session_create_calls, fake.session_expire_calls) == (0, 0)
        assert _SEMEADA in resp.json()[chave]
        if corpo.get("embutido"):
            # Trial DA SESSÃO (td="0"), mesmo com a elegibilidade agora True.
            assert resp.json()["trial_days"] == 0
    else:
        assert (fake.session_create_calls, fake.session_expire_calls) == (1, 1)
        assert resp.json()[chave] and _SEMEADA not in resp.json()[chave]


@pytest.mark.parametrize("motivo", ["already_subscribed", "lifetime", "pix_active"])
def test_assinar_embutido_409_como_hoje(user_id, monkeypatch, motivo):
    uid, _, client = _auth_user_setup(f"409-{motivo}-{user_id}")
    fake = _assinar_pronto(monkeypatch)
    if motivo == "already_subscribed":
        db.set_stripe_customer(uid, "cus_ja_assina")

        class _Subscription:
            @staticmethod
            def list(**kwargs):
                return {"data": [{"id": "sub_viva", "schedule": None}]}

        fake.Subscription = _Subscription
    elif motivo == "lifetime":
        from db_support import invalidate_auth_user_cache
        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute("update auth_accounts set last_payment_status = 'grandfathered' where user_id = %s",
                        (uid,))
            conn.commit()
        invalidate_auth_user_cache(uid)
    else:
        monkeypatch.setattr(dashboard, "_grant_pix_vigente", lambda _uid: {"id": 1})

    resp = _post(client, _ASSINAR_E)
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["error"] == motivo
    assert fake.session_create_calls == 0


def test_csp_do_header_http_libera_o_stripe_na_diretiva_certa():
    """Medido no header de uma resposta REAL, diretiva por diretiva. Classe cega:
    isto prova o header, não que o Stripe carregue sem violação (só o console)."""
    resp = TestClient(dashboard.app).get("/precos")
    assert resp.status_code == 200
    diretivas = {}
    for trecho in resp.headers["content-security-policy"].split(";"):
        nome, *valores = trecho.split()
        diretivas[nome] = valores

    for host in ("https://js.stripe.com", "https://*.js.stripe.com", "https://checkout.stripe.com"):
        assert host in diretivas["script-src"], host
    for host in ("https://js.stripe.com", "https://*.js.stripe.com",
                 "https://hooks.stripe.com", "https://checkout.stripe.com"):
        assert host in diretivas["frame-src"], host
    # O Stripe SOMA ao que já estava: o Pluggy (Open Finance) segue liberado.
    for host in ("https://cdn.pluggy.ai", "https://connect.pluggy.ai"):
        assert host in diretivas["frame-src"], host
    for nome in ("script-src", "frame-src"):
        assert not {"*", "https:", "http:"} & set(diretivas[nome]), nome
    assert diretivas["frame-ancestors"] == ["'none'"]
    assert resp.headers["x-frame-options"] == "DENY"
