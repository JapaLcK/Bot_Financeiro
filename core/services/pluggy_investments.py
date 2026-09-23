"""Leitura de `/investments` da Pluggy — paginada e com a metadata conferida.

Módulo próprio, e não mais um trecho de `core/services/pluggy.py`, porque o
arquivo passou do teto de 350 linhas (`tests/test_max_lines_python.py`) e a
divisão por assunto é o que o CLAUDE.md §0.5 manda. Movimento puro: o código
abaixo é byte a byte o que estava lá.

`/accounts`, `/v2/transactions`, `/connectors` e o CRUD de item continuam em
`core/services/pluggy.py` — só o assunto "investimentos" saiu.
"""
from __future__ import annotations

from typing import Any

from core.services.pluggy import PluggyApiError, _pluggy_get, create_pluggy_api_key


def _inv_int(value: Any) -> int | None:
    """int() tolerante para a metadata do /investments (a Pluggy manda número ou string)."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def list_pluggy_investments(item_id: str, api_key: str | None = None, *,
                            max_pages: int = 20) -> list[dict]:
    """Investimentos do item — inclui Caixinha do Nubank/PicPay (FIXED_INCOME/CDB).

    PAGINADO, e a metadata é CONFERIDA a cada página. O retorno desta função é o
    que autoriza a reconciliação de `save_open_finance_investments` (posição que
    não veio é posição que saiu do banco, e a caixinha dela é removida), então
    leitura parcial não pode sair daqui com cara de carteira menor: qualquer
    incoerência vira `PluggyApiError`, que o chamador já lê como
    `investments_ok=False` (core/services/pluggy_sync.py) e não remove nada.

    Não manda `pageSize`: fica no default do servidor (500) — o irmão
    `/v2/transactions` devolve HTTP 400 quando ele é enviado.

    `max_pages=20` → 20 × 500 = 10.000 posições por conexão; a maior carteira PF
    medida neste repositório tem 10 (ver `_CAIXINHA_CDB`, db/open_finance.py). O
    teto existe contra resposta que nunca termina, não contra carteira grande —
    por isso bater nele LEVANTA em vez de truncar: truncar seria o mesmo `[]`
    mentiroso que esta função tinha antes.
    """
    key = api_key or create_pluggy_api_key()
    out: list[dict] = []
    total: Any = None

    def incompleta(motivo: str) -> PluggyApiError:
        # Sem o corpo da resposta, mesma régua de `_raise_for_pluggy_response`.
        return PluggyApiError(f"Leitura de /investments incompleta: {motivo}")

    pagina = 1
    while True:
        if pagina > max_pages:
            raise incompleta("teto_de_paginas")
        data = _pluggy_get("/investments", key, params={"itemId": item_id, "page": pagina})
        if not isinstance(data, dict):
            raise incompleta("resposta_nao_dict")
        results = data.get("results")
        if not isinstance(results, list):
            raise incompleta("results_ausente")
        # A referência da Pluggy mostra `"page": 0` num exemplo de resposta enquanto
        # o guia manda começar em `page=1`. Os dois não podem estar certos. Pedimos 1
        # e exigimos eco 1: se a API for mesmo 0-based, pedir 1 pula a página 0
        # inteira, e página não lida vira posição "ausente" → remoção de caixinha com
        # dinheiro dentro. Por isso eco divergente é LEITURA INCOMPLETA, não
        # adaptação silenciosa.
        # O LIMITE disto: se a API for 0-based de verdade, TODA conexão cai em
        # READ_FAILED — ninguém perde dado (nada é removido, o espelho anterior fica
        # de pé), mas a tela mostra erro e as caixinhas congelam no último saldo até
        # alguém remedir. Se isso aparecer em produção, o conserto é uma linha:
        # começar o laço em 0 e exigir o eco igual ao pedido. Não há flag de
        # ambiente para isso de propósito.
        if "page" in data and _inv_int(data.get("page")) != pagina:
            raise incompleta("page_incoerente")
        total_pages = _inv_int(data.get("totalPages"))
        if total_pages is None or total_pages < 0:
            raise incompleta("total_pages_ausente")
        out.extend(results)
        total = data.get("total")
        if pagina >= total_pages:
            break
        pagina += 1

    if total is not None and _inv_int(total) != len(out):
        raise incompleta("total_incoerente")
    return out
