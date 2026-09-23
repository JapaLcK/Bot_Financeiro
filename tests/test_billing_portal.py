"""Regressão HTTP das duas entradas do portal, com banco real e Stripe simulado."""
import logging
import sys
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard


MESSAGE = (
    "Não foi possível abrir o gerenciamento da assinatura agora. "
    "Tente novamente em instantes."
)
ROUTES = ("/billing/portal", "/conta")


class PortalStripeError(Exception):
    pass


@pytest.fixture
def portal(monkeypatch):
    suffix = uuid.uuid4().hex
    email = f"portal-{suffix}@example.com"
    uid = int(db.register_auth_user(email, "senha-teste-123")["user_id"])
    customer = f"cus_portal_{suffix}"
    db.set_stripe_customer(uid, customer)
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(url="https://billing.stripe.com/p/session/test-portal")

    fake = SimpleNamespace(
        api_key=None,
        error=SimpleNamespace(StripeError=PortalStripeError),
        billing_portal=SimpleNamespace(Session=SimpleNamespace(create=create)),
    )
    monkeypatch.setitem(sys.modules, "stripe", fake)
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_portal_fake")
    dashboard.limiter._storage.reset()
    client = TestClient(dashboard.app, raise_server_exceptions=False)
    client.cookies.set(dashboard.AUTH_COOKIE_NAME, dashboard._make_jwt(uid, email))
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, "csrf-portal-test")
    yield SimpleNamespace(client=client, uid=uid, customer=customer, stripe=fake, calls=calls)
    client.close()


def request_portal(client, route):
    if route == "/conta":
        return client.get(route, headers={"Accept": "text/html"}, follow_redirects=False)
    return client.post(route, headers={dashboard.CSRF_HEADER_NAME: "csrf-portal-test"})


@pytest.mark.parametrize("route,return_path", [("/billing/portal", "/app"), ("/conta", "/settings")])
def test_portal_sucesso_preserva_destino_e_cliente(portal, route, return_path):
    response = request_portal(portal.client, route)
    expected = "https://billing.stripe.com/p/session/test-portal"
    if route == "/conta":
        assert response.status_code == 302
        assert response.headers["location"] == expected
    else:
        assert response.status_code == 200
        assert response.json() == {"portal_url": expected}
    assert portal.calls == [{"customer": portal.customer, "return_url": f"{dashboard.DASHBOARD_URL}{return_path}"}]


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("request_id", [None, "req_portal_test"])
def test_falha_stripe_controlada_e_log_seguro(portal, monkeypatch, caplog, route, request_id):
    marker = "segredo-sk_live_nao_vazar-email-cliente@example.com"
    error = PortalStripeError(marker)
    if request_id is not None:
        error.request_id = request_id

    def fail(**kwargs):
        raise error

    monkeypatch.setattr(portal.stripe.billing_portal.Session, "create", fail)
    with caplog.at_level(logging.ERROR):
        response = request_portal(portal.client, route)
    assert response.status_code == 502
    assert marker not in response.text
    assert marker not in caplog.text
    records = [r for r in caplog.records if "PortalStripeError" in r.getMessage()]
    assert len(records) == 1
    assert route in records[0].getMessage()
    assert f"request_id={request_id}" in records[0].getMessage()
    assert records[0].exc_info is None
    if route == "/conta":
        assert response.headers["content-type"].startswith("text/html")
        assert response.headers["cache-control"] == "no-store"
        assert MESSAGE in response.text
        assert 'href="/conta"' in response.text and "Tentar novamente" in response.text
        assert 'href="/settings"' in response.text and "Voltar para configurações" in response.text
    else:
        assert response.json() == {"detail": MESSAGE}


def test_mensagem_api_e_tela_sao_as_mesmas(portal, monkeypatch):
    """O override HTML exige literais: evita divergência entre as duas cópias."""
    def fail(**kwargs):
        raise PortalStripeError("falha simulada")

    monkeypatch.setattr(portal.stripe.billing_portal.Session, "create", fail)
    api = request_portal(portal.client, "/billing/portal")
    page = request_portal(portal.client, "/conta")
    assert api.status_code == page.status_code == 502
    assert api.json()["detail"] in page.text


@pytest.mark.parametrize("route", ROUTES)
def test_portal_sem_autenticacao_nao_chama_stripe(portal, route):
    portal.client.cookies.clear()
    portal.client.cookies.set(dashboard.CSRF_COOKIE_NAME, "csrf-portal-test")
    response = request_portal(portal.client, route)
    if route == "/conta":
        assert response.status_code == 302
        assert response.headers["location"] == dashboard._dashboard_url("/login?next=/conta")
    else:
        assert response.status_code == 401
    assert portal.calls == []


@pytest.mark.parametrize("route", ROUTES)
def test_portal_sem_configuracao_preserva_resposta(portal, monkeypatch, route):
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "")
    response = request_portal(portal.client, route)
    if route == "/conta":
        assert response.status_code == 302
        assert response.headers["location"] == dashboard._dashboard_url("/precos")
    else:
        assert response.status_code == 503
        assert response.json()["detail"] == "Pagamentos ainda não configurados."
    assert portal.calls == []


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("user", [None, {"stripe_customer_id": None}])
def test_portal_sem_cliente_preserva_resposta(portal, monkeypatch, route, user):
    monkeypatch.setattr(db, "get_auth_user", lambda uid: user)
    response = request_portal(portal.client, route)
    if route == "/conta":
        assert response.status_code == 302
        assert response.headers["location"] == dashboard._dashboard_url("/precos")
    else:
        assert response.status_code == 404
        assert response.json()["detail"] == "Sem assinatura ativa."
    assert portal.calls == []


@pytest.mark.parametrize("route", ROUTES)
def test_bug_inesperado_nao_vira_indisponibilidade_stripe(portal, monkeypatch, route):
    def fail(**kwargs):
        raise RuntimeError("bug de código simulado")

    monkeypatch.setattr(portal.stripe.billing_portal.Session, "create", fail)
    response = request_portal(portal.client, route)
    assert response.status_code == 500
    assert MESSAGE not in response.text


def test_api_portal_preserva_csrf(portal):
    response = portal.client.post("/billing/portal")
    assert response.status_code == 403
    assert portal.calls == []
