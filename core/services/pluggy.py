from __future__ import annotations

import os
from typing import Any
from urllib.parse import parse_qs, urlparse

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


def _pluggy_get(path: str, api_key: str, params: dict[str, Any] | None = None) -> dict:
    """GET autenticado na Pluggy. `path` começa com '/'."""
    with httpx.Client(timeout=_pluggy_timeout()) as client:
        resp = client.get(
            f"{_pluggy_base_url()}{path}",
            headers={"X-API-KEY": api_key},
            params=params or {},
        )
    _raise_for_pluggy_response(resp, f"Falha ao consultar {path} na Pluggy")
    return resp.json()


def get_pluggy_item(item_id: str, api_key: str | None = None) -> dict:
    key = api_key or create_pluggy_api_key()
    return _pluggy_get(f"/items/{item_id}", key)


def update_pluggy_item(item_id: str, api_key: str | None = None) -> dict:
    """PATCH /items/{id}: força a Pluggy a re-buscar do banco. Ao concluir, ela manda
    webhook (item/updated, transactions/*), que dispara o sync. Usado no refresh periódico."""
    key = api_key or create_pluggy_api_key()
    with httpx.Client(timeout=_pluggy_timeout()) as client:
        resp = client.patch(
            f"{_pluggy_base_url()}/items/{item_id}",
            headers={"X-API-KEY": key},
            json={},
        )
    _raise_for_pluggy_response(resp, f"Falha ao atualizar item {item_id} na Pluggy")
    return resp.json()


def list_pluggy_accounts(item_id: str, api_key: str | None = None) -> list[dict]:
    key = api_key or create_pluggy_api_key()
    data = _pluggy_get("/accounts", key, params={"itemId": item_id})
    results = data.get("results")
    return list(results) if isinstance(results, list) else []


def _extract_after_cursor(next_value: Any) -> str | None:
    """O /v2/transactions devolve `next` como '?accountId=..&after=<cursor>' (ou null no fim)."""
    if not next_value:
        return None
    text = str(next_value)
    query = urlparse(text).query or text.lstrip("?")
    after = parse_qs(query).get("after")
    return after[0] if after else None


def list_pluggy_transactions(
    account_id: str,
    api_key: str | None = None,
    *,
    max_pages: int = 60,
) -> list[dict]:
    """Puxa todas as transações de uma conta via /v2/transactions (paginação por cursor).

    O endpoint antigo /transactions (page-based) está deprecado até 2026-12-31; o v2
    devolve o cursor no campo `next` (null na última página) e o tamanho de página é
    fixo no servidor — passar `pageSize` retorna HTTP 400. Segue o cursor até acabar.
    """
    key = api_key or create_pluggy_api_key()
    out: list[dict] = []
    params: dict[str, Any] = {"accountId": account_id}
    for _ in range(max_pages):
        data = _pluggy_get("/v2/transactions", key, params=params)
        results = data.get("results")
        if isinstance(results, list):
            out.extend(results)
        after = _extract_after_cursor(data.get("next"))
        if not after or not results:
            break
        params = {"accountId": account_id, "after": after}
    return out


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
