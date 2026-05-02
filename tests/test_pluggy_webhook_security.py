import hashlib
import hmac
import json

from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard


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
    monkeypatch.setattr(dashboard, "log_system_event", _noop_log)

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
