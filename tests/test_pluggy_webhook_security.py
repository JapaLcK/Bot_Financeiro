import hashlib
import hmac
import json

from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.open_finance as open_finance_routes


def _signed_headers(raw_body: bytes, secret: str) -> dict[str, str]:
    signature = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Pluggy-Signature": f"sha256={signature}",
    }


def test_pluggy_webhook_rejects_missing_signature(monkeypatch):
    monkeypatch.setenv("PLUGGY_WEBHOOK_SECRET", "test-webhook-secret")

    response = TestClient(dashboard.app).post(
        "/open-finance/pluggy/webhook",
        json={"event": "item/updated"},
    )

    assert response.status_code == 401


def test_pluggy_webhook_rejects_invalid_signature(monkeypatch):
    monkeypatch.setenv("PLUGGY_WEBHOOK_SECRET", "test-webhook-secret")

    response = TestClient(dashboard.app).post(
        "/open-finance/pluggy/webhook",
        json={"event": "item/updated"},
        headers={"X-Pluggy-Signature": "sha256=invalid"},
    )

    assert response.status_code == 401


def test_pluggy_webhook_accepts_valid_sha256_signature(monkeypatch):
    async def _noop_log(*args, **kwargs):
        return None

    monkeypatch.setenv("PLUGGY_WEBHOOK_SECRET", "test-webhook-secret")
    monkeypatch.setattr(open_finance_routes, "log_system_event", _noop_log)

    raw_body = json.dumps(
        {"event": "item/updated", "itemId": ""},
        separators=(",", ":"),
    ).encode("utf-8")

    response = TestClient(dashboard.app).post(
        "/open-finance/pluggy/webhook",
        content=raw_body,
        headers=_signed_headers(raw_body, "test-webhook-secret"),
    )

    assert response.status_code == 200
    assert response.json() == {"received": True}


def test_pluggy_webhook_accepts_url_token(monkeypatch):
    """A Pluggy nao assina o corpo; autentica pelo token na URL do webhook."""
    async def _noop_log(*args, **kwargs):
        return None

    monkeypatch.setenv("PLUGGY_WEBHOOK_SECRET", "test-webhook-secret")
    monkeypatch.setattr(open_finance_routes, "log_system_event", _noop_log)

    response = TestClient(dashboard.app).post(
        "/open-finance/pluggy/webhook?token=test-webhook-secret",
        json={"event": "item/updated", "itemId": ""},
    )

    assert response.status_code == 200
    assert response.json() == {"received": True}


def test_pluggy_webhook_accepts_header_token(monkeypatch):
    """Alternativa: secret compartilhado via header custom configurado no painel."""
    async def _noop_log(*args, **kwargs):
        return None

    monkeypatch.setenv("PLUGGY_WEBHOOK_SECRET", "test-webhook-secret")
    monkeypatch.setattr(open_finance_routes, "log_system_event", _noop_log)

    response = TestClient(dashboard.app).post(
        "/open-finance/pluggy/webhook",
        json={"event": "item/updated", "itemId": ""},
        headers={"X-Webhook-Token": "test-webhook-secret"},
    )

    assert response.status_code == 200
    assert response.json() == {"received": True}


def test_pluggy_webhook_rejects_wrong_url_token(monkeypatch):
    monkeypatch.setenv("PLUGGY_WEBHOOK_SECRET", "test-webhook-secret")

    response = TestClient(dashboard.app).post(
        "/open-finance/pluggy/webhook?token=wrong",
        json={"event": "item/updated"},
    )

    assert response.status_code == 401


def test_pluggy_webhook_rejects_non_ascii_credentials(monkeypatch):
    """Os três caminhos de autenticação leem valor escolhido por quem chama.

    `compare_digest` sobre str não-ASCII levanta TypeError → 500 com stack
    trace, sem autenticação nenhuma. Tem de continuar 401 nos três.
    """
    monkeypatch.setenv("PLUGGY_WEBHOOK_SECRET", "test-webhook-secret")
    client = TestClient(dashboard.app)

    # 1. token na query string
    por_query = client.post(
        "/open-finance/pluggy/webhook?token=café",
        json={"event": "item/updated"},
    )
    assert por_query.status_code == 401

    # 2. header de secret compartilhado (em bytes: o httpx recusa str não-ASCII)
    por_header = client.post(
        "/open-finance/pluggy/webhook",
        json={"event": "item/updated"},
        headers={"X-Webhook-Token": "café".encode("latin-1")},
    )
    assert por_header.status_code == 401

    # 3. assinatura HMAC
    por_assinatura = client.post(
        "/open-finance/pluggy/webhook",
        json={"event": "item/updated"},
        headers={"X-Pluggy-Signature": "sha256=café".encode("latin-1")},
    )
    assert por_assinatura.status_code == 401


# ── o `itemId` do CORPO vira URL na API da Pluggy (com a NOSSA chave) ────────

def _webhook_com_item(monkeypatch, item_id: str) -> list[str]:
    """Manda `item/created` com este `itemId` e devolve as URLs que saíram."""
    import httpx

    import core.services.pluggy as pluggy_svc

    monkeypatch.setenv("PLUGGY_WEBHOOK_SECRET", "test-webhook-secret")
    monkeypatch.setattr(open_finance_routes, "_schedule_pluggy_sync", lambda i: None)
    monkeypatch.setattr(pluggy_svc, "create_pluggy_api_key", lambda: "k")
    urls: list[str] = []

    def _get(self, url, **kw):
        urls.append(str(httpx.URL(url)))
        raise RuntimeError("parou aqui: o teste só quer a URL")

    monkeypatch.setattr(httpx.Client, "get", _get)
    r = TestClient(dashboard.app).post(
        "/open-finance/pluggy/webhook?token=test-webhook-secret",
        content=json.dumps({"event": "item/created", "itemId": item_id}).encode(),
        headers={"Content-Type": "application/json"})
    assert r.status_code == 200, r.text
    return urls


def test_item_id_do_corpo_nao_forja_url_na_pluggy(monkeypatch):
    """Antes da adoção de item órfão, o `itemId` do corpo do webhook NUNCA virava
    HTTP para a Pluggy; agora vira, e `get_pluggy_item` monta `/items/{id}`.

    Medido sem a régua de `core/services/pluggy.py`: `itemId='../../accounts'`
    saía como `GET https://api.pluggy.ai/accounts` — requisição forjada por um
    corpo de fora, com a nossa chave (o secret do webhook trafega em `?token=`,
    logo vaza em log de proxy).
    """
    for hostil in ["../../accounts", "x?include=all", "abc#frag", "a b", "..", "%2e%2e"]:
        assert _webhook_com_item(monkeypatch, hostil) == [], (
            f"itemId={hostil!r} virou requisição na API da Pluggy")


def test_item_id_valido_continua_consultando_a_pluggy(monkeypatch):
    """CONTROLE POSITIVO do par acima: sem ele, os dois passariam num código que
    simplesmente parou de consultar a Pluggy."""
    assert _webhook_com_item(monkeypatch, "item-valido_9") == \
        ["https://api.pluggy.ai/items/item-valido_9"]
