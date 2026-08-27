from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from core.services.pluggy_health import safe_code


class PluggyConfigError(RuntimeError):
    pass


class PluggyApiError(RuntimeError):
    """Erro HTTP da Pluggy. `status_code` existe para distinguir 404 (item some
    de verdade) de 429/5xx (tenta de novo) sem ninguém fazer regex na mensagem.

    NÃO carrega o corpo da resposta: ele traz PII do titular (ver
    `_raise_for_pluggy_response`). `code` é o código curto da Pluggy, validado."""

    def __init__(self, message: str, *, status_code: int | None = None,
                 code: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


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
    """Levanta SEM o corpo da resposta. O corpo de erro da Pluggy carrega nome,
    CPF e número de conta do titular — medido — e `str(exc)` desta exceção vira
    `details` de `log_system_event`, PERSISTIDO em `system_event_logs` e lido
    pelo painel admin, além de sair no `print` do refresh. Sobra o que serve para
    depurar e não identifica ninguém: o HTTP status e, quando parece código, o
    `code` da Pluggy (`safe_code` é a mesma regra usada nos warnings do health).
    """
    if resp.is_success:
        return
    try:
        body: Any = resp.json()
    except ValueError:
        body = None
    code = safe_code(body.get("code")) if isinstance(body, dict) else ""
    raise PluggyApiError(
        f"{context}: Pluggy retornou HTTP {resp.status_code}" + (f" (code={code})" if code else ""),
        status_code=resp.status_code,
        code=code or None,
    )


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


def delete_pluggy_item(item_id: str, api_key: str | None = None) -> bool:
    """DELETE /items/{id}: remove o item na Pluggy (libera o acesso pra reconectar).

    Sem isso, desconectar no PigBank apagava só o nosso registro e o item ficava órfão
    na Pluggy, bloqueando a reconexão ("já possui conexão com este acesso"). Retorna True
    se removido (2xx) ou já inexistente (404); levanta PluggyApiError em erro inesperado.
    """
    if not item_id:
        return False
    key = api_key or create_pluggy_api_key()
    with httpx.Client(timeout=_pluggy_timeout()) as client:
        resp = client.delete(
            f"{_pluggy_base_url()}/items/{item_id}",
            headers={"X-API-KEY": key},
        )
    if resp.status_code in (200, 202, 204, 404):
        return True
    _raise_for_pluggy_response(resp, f"Falha ao deletar item {item_id} na Pluggy")
    return True


def list_pluggy_accounts(item_id: str, api_key: str | None = None) -> list[dict]:
    key = api_key or create_pluggy_api_key()
    data = _pluggy_get("/accounts", key, params={"itemId": item_id})
    results = data.get("results")
    return list(results) if isinstance(results, list) else []


# Catálogo de connectors (bancos) cacheado em processo — muda raramente e é grande
# (~250 itens). Chave do cache = include_sandbox; TTL configurável (default 6h).
_CONNECTORS_CACHE: dict[bool, dict[str, Any]] = {}
_CONNECTORS_TTL = float(os.getenv("PLUGGY_CONNECTORS_TTL", "21600"))


def list_pluggy_connectors(
    api_key: str | None = None, *, include_sandbox: bool = False
) -> list[dict]:
    """Lista os connectors (instituições) do Brasil disponíveis na Pluggy.

    Cacheado em processo por `include_sandbox` (TTL `PLUGGY_CONNECTORS_TTL`). Retorna
    os dicts crus da Pluggy (id, name, type, primaryColor, products, ...). Quando
    include_sandbox=True, mescla os connectors de teste (dedup por id) — usado só no
    beta pra permitir conectar o "Pluggy Bank" sem banco real."""
    now = time.time()
    cached = _CONNECTORS_CACHE.get(include_sandbox)
    if cached and (now - cached["ts"]) < _CONNECTORS_TTL:
        return cached["data"]

    key = api_key or create_pluggy_api_key()
    seen: dict[Any, dict] = {}
    sandbox_flags = ["false"] + (["true"] if include_sandbox else [])
    for sb in sandbox_flags:
        data = _pluggy_get("/connectors", key, params={"countries": "BR", "sandbox": sb})
        results = data.get("results")
        for c in results if isinstance(results, list) else []:
            cid = c.get("id")
            if cid is not None:
                seen[cid] = c
    out = list(seen.values())
    _CONNECTORS_CACHE[include_sandbox] = {"ts": now, "data": out}
    return out


def list_pluggy_investments(item_id: str, api_key: str | None = None) -> list[dict]:
    """Investimentos do item — inclui Caixinha do Nubank/PicPay (FIXED_INCOME/CDB)."""
    key = api_key or create_pluggy_api_key()
    data = _pluggy_get("/investments", key, params={"itemId": item_id})
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
    on_page: "Callable[[], None] | None" = None,
) -> list[dict]:
    """Puxa todas as transações de uma conta via /v2/transactions (paginação por cursor).

    O endpoint antigo /transactions (page-based) está deprecado até 2026-12-31; o v2
    devolve o cursor no campo `next` (null na última página) e o tamanho de página é
    fixo no servidor — passar `pageSize` retorna HTTP 400. Segue o cursor até acabar.

    `on_page` é um heartbeat chamado antes de CADA página: como o loop pode levar
    até max_pages requisições sequenciais (minutos), quem sincroniza usa isso pra
    renovar o hold de e-mail dos agentes e não deixar a janela expirar no meio de
    um sync longo. Fail-soft: falha do heartbeat nunca interrompe a busca.
    """
    key = api_key or create_pluggy_api_key()
    out: list[dict] = []
    params: dict[str, Any] = {"accountId": account_id}
    for _ in range(max_pages):
        if on_page is not None:
            try:
                on_page()
            except Exception:
                pass
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

    # INVESTMENTS é obrigatório: o sync lê /investments pra achar a Caixinha
    # (FIXED_INCOME/CDB). Se o item não coletar esse produto, /investments volta
    # vazio e a detecção de caixinha (base do Banqueiro OF-native) quebra.
    products_env = (os.getenv("PLUGGY_PRODUCTS") or "ACCOUNTS,TRANSACTIONS,CREDIT_CARDS,INVESTMENTS").strip()
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
