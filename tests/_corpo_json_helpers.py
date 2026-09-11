"""Clientes, corpos e fixtures dos testes de corpo JSON malformado (issue #310).

Sem prefixo `test_` de propósito, como `tests/_billing_grants_helpers.py`: o
pytest não coleta este arquivo. Ele existe para os três arquivos do assunto
(topo não-objeto, campo filho, número não finito) não carregarem três cópias do
mesmo `_post_bruto`/`_admin_client` — cópia de helper é como dois testes passam
a medir coisas diferentes achando que medem a mesma.

As fixtures moram AQUI e não num `conftest.py` de propósito: `configured_admin`
é `autouse`, e num conftest ela passaria a trocar a senha do admin e a resetar o
storage do rate limiter na suíte inteira. Importada por nome, vale só nos
arquivos que a importam.
"""
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

import core.admin_dashboard as admin_dashboard
import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.open_finance as open_finance_routes

# Topos válidos em JSON que não são objeto. `null` e `true` incluídos de
# propósito: são os que passam despercebidos numa validação por truthiness.
NAO_OBJETOS = [[1, 2, 3], "abc", 42, None, True]
IDS = ["lista", "string", "numero", "null", "true"]

PLUGGY_SECRET = "test-webhook-secret"
CSRF = "test-admin-csrf"
# id que não existe: isola o teste de estado de outros testes e faz o caminho
# feliz terminar em 404 de forma determinística, sem fixture de dados.
INEXISTENTE = 999_999_999


@pytest.fixture(scope="module", autouse=True)
def _admin_tables():
    """system_event_logs não vem do init_db — só do ensure_admin_tables()
    (startup do app). Mesmo precedente de tests/test_admin_users_panel.py."""
    import asyncio

    asyncio.run(admin_dashboard.ensure_admin_tables())


@pytest.fixture(autouse=True)
def configured_admin(monkeypatch):
    async def _noop_log(*args, **kwargs):
        return None

    monkeypatch.setattr(admin_dashboard, "ADMIN_DASHBOARD_PASSWORD", "secret-admin")
    monkeypatch.setattr(admin_dashboard, "ADMIN_DASHBOARD_PASSWORD_HASH", "")
    monkeypatch.setattr(admin_dashboard, "log_system_event", _noop_log)
    monkeypatch.setattr(open_finance_routes, "log_system_event", _noop_log)
    monkeypatch.setenv("PLUGGY_WEBHOOK_SECRET", PLUGGY_SECRET)
    # /admin/auth/login tem @limiter.limit("10/minute") com storage em memória
    # compartilhado entre testes. Sem este reset, os 5 corpos ruins + o corpo
    # bom + os logins de _admin_client() estouram o teto e o 429 passaria por
    # "não é 500" — por isso os asserts dos testes são por status exato.
    try:
        dashboard.limiter._storage.reset()
    except Exception:
        pass


@pytest.fixture
def afiliado_stub(monkeypatch):
    """Sem tocar no banco: o email resolve para um user_id fixo e o
    create_affiliate vira espião. Devolve a lista de (user_id, code, bps)."""
    import db
    import db.affiliates

    chamadas = []

    def _cria(user_id, code, commission_bps):
        chamadas.append((user_id, code, commission_bps))
        return {"id": 1, "code": code or "GERADO", "status": "active",
                "commission_bps": commission_bps}

    monkeypatch.setattr(db, "find_user_id_by_email", lambda email: 4242)
    monkeypatch.setattr(db.affiliates, "create_affiliate", _cria)
    return chamadas


def _client() -> TestClient:
    # raise_server_exceptions=False: sem isso o 500 vira exceção propagada e o
    # teste nunca chega a ler o status que quer medir.
    client = TestClient(
        dashboard.app, base_url="https://testserver", raise_server_exceptions=False
    )
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, CSRF)
    return client


def _admin_client() -> TestClient:
    client = _client()
    login = client.post(
        "/admin/auth/login",
        headers={dashboard.CSRF_HEADER_NAME: CSRF},
        json={"username": "admin", "password": "secret-admin"},
    )
    assert login.status_code == 200
    return client


def _post_texto(client: TestClient, url: str, texto: str) -> "object":
    """POST com o corpo JSON escrito à mão, byte a byte.

    Existe separado do `_post_bruto` porque `json.dumps` não consegue produzir
    o token `1e400`: em Python ele JÁ é `inf`, e o dumps o escreve como
    `Infinity` — outro token, outro hook do parser. Para medir o `1e400` o
    corpo tem de ser texto.

    O header CSRF é obrigatório em todo POST: sem ele a resposta é 403 e um
    assert frouxo ficaria verde de graça.
    """
    return client.post(
        url,
        content=texto,
        headers={
            "Content-Type": "application/json",
            dashboard.CSRF_HEADER_NAME: CSRF,
        },
    )


def _post_bruto(client: TestClient, url: str, corpo) -> "object":
    """POST com o corpo exatamente como escrito, sem o açúcar do httpx."""
    return _post_texto(client, url, json.dumps(corpo))


def _pluggy_post_texto(texto: str) -> "object":
    """Webhook com o corpo em texto cru — mesma razão do `_post_texto`.

    A assinatura HMAC é calculada sobre os bytes que vão de fato, então o corpo
    passa pelo `_authorize_pluggy_webhook` sem depender de reserialização.
    """
    raw_body = texto.encode("utf-8")
    assinatura = hmac.new(
        PLUGGY_SECRET.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return TestClient(dashboard.app, raise_server_exceptions=False).post(
        "/open-finance/pluggy/webhook",
        content=raw_body,
        headers={
            "Content-Type": "application/json",
            "X-Pluggy-Signature": f"sha256={assinatura}",
        },
    )


def _pluggy_post(corpo) -> "object":
    return _pluggy_post_texto(json.dumps(corpo, separators=(",", ":")))
