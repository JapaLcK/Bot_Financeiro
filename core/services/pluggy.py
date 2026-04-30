from __future__ import annotations

import os
from typing import Any

import httpx


class PluggyConfigError(RuntimeError):
    pass


class PluggyApiError(RuntimeError):
    pass


def _pluggy_base_url() -> str:
    return (os.getenv("PLUGGY_BASE_URL") or "https://api.pluggy.ai").rstrip("/")


def _pluggy_timeout() -> float:
    return float(os.getenv("PLUGGY_TIMEOUT", "20"))


def _configured_api_key() -> str:
    return (os.getenv("PLUGGY_API_KEY") or "").strip()


def _client_credentials() -> tuple[str, str]:
    client_id = (os.getenv("PLUGGY_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("PLUGGY_CLIENT_SECRET") or "").strip()
    if not client_id or not client_secret:
        raise PluggyConfigError("PLUGGY_CLIENT_ID e PLUGGY_CLIENT_SECRET precisam estar configurados.")
    return client_id, client_secret


def _raise_for_pluggy_response(resp: httpx.Response, context: str) -> None:
    if resp.is_success:
        return
    try:
        detail: Any = resp.json()
    except ValueError:
        detail = resp.text
    raise PluggyApiError(f"{context}: Pluggy retornou HTTP {resp.status_code}: {detail}")


def create_pluggy_api_key() -> str:
    """
    Gera uma API Key temporaria da Pluggy no servidor.
    Nunca exponha clientSecret ou apiKey no frontend.
    """
    configured = _configured_api_key()
    if configured:
        return configured

    client_id, client_secret = _client_credentials()
    with httpx.Client(timeout=_pluggy_timeout()) as client:
        resp = client.post(
            f"{_pluggy_base_url()}/auth",
            json={"clientId": client_id, "clientSecret": client_secret},
        )
    _raise_for_pluggy_response(resp, "Falha ao autenticar na Pluggy")
    data = resp.json()
    api_key = data.get("apiKey") or data.get("accessToken")
    if not api_key:
        raise PluggyApiError("Resposta de autenticação da Pluggy não trouxe apiKey.")
    return str(api_key)


def create_pluggy_connect_token(user_id: int, webhook_url: str | None = None) -> dict:
    api_key = create_pluggy_api_key()
    options: dict[str, Any] = {
        "clientUserId": str(user_id),
        "avoidDuplicates": True,
    }
    if webhook_url:
        options["webhookUrl"] = webhook_url

    products_env = (os.getenv("PLUGGY_PRODUCTS") or "ACCOUNTS,TRANSACTIONS,CREDIT_CARDS").strip()
    products = [p.strip().upper() for p in products_env.split(",") if p.strip()]
    if products:
        options["products"] = products

    payload = {"options": options}
    with httpx.Client(timeout=_pluggy_timeout()) as client:
        resp = client.post(
            f"{_pluggy_base_url()}/connect_token",
            headers={"X-API-KEY": api_key},
            json=payload,
        )
    _raise_for_pluggy_response(resp, "Falha ao criar connect token da Pluggy")
    data = resp.json()
    access_token = data.get("accessToken") or data.get("connectToken")
    if not access_token:
        raise PluggyApiError("Resposta da Pluggy não trouxe accessToken/connectToken.")
    return {
        "accessToken": str(access_token),
        "raw": data,
        "options": options,
    }
