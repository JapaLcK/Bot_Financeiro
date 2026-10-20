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


# Logo PRÓPRIO dos 10 bancos principais: PNG 256 em frontend/brand/bancos/<ispb>.png, servido pela rota
# `/brand/{path}` (cache imutável de 1 ano: trocar a arte de um banco exige trocar o NOME do arquivo E ampliar a regex
# `LOGO_PROPRIO` do cliente (webapp/src/dashboard/conta/api.ts e, quando existir, o app) no mesmo PR; nunca sobrescrever o PNG).
# Origem: pacote npm logos-bancos-br 0.8.2 (MIT no código; ver o DISCLAIMER.md do pacote, github.com/rzmt/logos-bancos-br).
# Os logos são marcas dos bancos, NÃO cobertas pela MIT: uso nominativo, só para identificar a instituição, sem deformar nem
# recolorir. Precedência no produto: próprio > `logo_url` da Pluggy > inicial. Busca SÓ por id exato do conector (nunca por nome).
# Regra de quem entra (catálogo da Pluggy, medido em 2026-10-20): só conectores de PESSOA FÍSICA do banco principal; BTG 614 (investimentos
# OF) e 214 (direto) entram por serem a mesma marca em pessoa física. Empresas, Bradescard, 'sem conta', Caixa Tem, Previdência
# (279 BB, 246 Caixa), Corretoras (812 Santander) e cripto ficam FORA e seguem com o logo da Pluggy.
_LOGO_PROPRIO_ISPB: dict[str, str] = {
    "601": "60701190",  # Itaú
    "603": "60746948",  # Bradesco
    "611": "00000000",  # Banco do Brasil
    "608": "90400888",  # Santander
    "619": "00360305", "219": "00360305",  # Caixa
    "823": "00416968", "215": "00416968",  # Inter
    "626": "31872495",  # C6
    "612": "18236120",  # Nubank
    "675": "30306294", "614": "30306294", "214": "30306294",  # BTG
    "606": "10573521", "206": "10573521",  # Mercado Pago
}


def logo_proprio(connector_id: Any) -> str | None:
    """Caminho relativo do logo próprio do conector, ou None. Só id exato (str); nunca exceção."""
    ispb = _LOGO_PROPRIO_ISPB.get(connector_id) if isinstance(connector_id, str) else None
    return f"/brand/bancos/{ispb}.png" if ispb else None
