"""Catálogo de conectores (bancos) da Pluggy, em cache de processo.

Público e sem dado de usuário: nada aqui recebe nem guarda `user_id`.
"""
from __future__ import annotations

import os
import time
from typing import Any

from core.services.pluggy import _pluggy_get, create_pluggy_api_key

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


def logo_do_conector(connector_id: str) -> str | None:
    """`imageUrl` cru do conector no cache, ou None. SÓ memória: nunca rede, nunca exceção
    (roda no caminho do snapshot). Ignora o TTL: logo velho é melhor que nenhum. `.get`,
    nunca iterar o dict do cache: o tick grava de outra thread. Quem valida a URL é
    `logo_url_valida`."""
    for sandbox in (True, False):
        for c in (_CONNECTORS_CACHE.get(sandbox) or {}).get("data") or ():
            if isinstance(c, dict) and str(c.get("id")) == connector_id:
                return c.get("imageUrl")
    return None
