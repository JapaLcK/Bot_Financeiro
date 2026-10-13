"""
core/services/ai_chat/tools/__init__.py — registro central de tools da IA.

Cada módulo neste pacote exporta uma lista `TOOLS: list[Tool]` (ver `_base.py`).
Este `__init__` agrega todas e expõe utilitários comuns:

  SCHEMAS         — list[dict] no formato OpenAI, passada em tools=[]
  SCHEMAS_APP     — SCHEMAS sem OCULTAS_NO_APP + show_card (só o stream do app)
  WRITE_TOOL_NAMES — set de nomes de tools que precisam de pending action
  get_tool(name)   — lookup; retorna Tool ou None

**Adicionar um domínio novo:** criar `tools/<dominio>.py` com `TOOLS = [...]`,
importar abaixo e estender `_ALL_TOOLS`. Nada mais.
"""
from __future__ import annotations

from typing import Any, Optional

from . import balance, bills, budgets, cards, categories, investments, launches, meta, mostrar_card, pockets, reports, simulator
from ._base import Tool


_ALL_TOOLS: list[Tool] = [
    *balance.TOOLS,
    *bills.TOOLS,
    *budgets.TOOLS,
    *cards.TOOLS,
    *categories.TOOLS,
    *investments.TOOLS,
    *launches.TOOLS,
    *meta.TOOLS,
    *pockets.TOOLS,
    *reports.TOOLS,
    *simulator.TOOLS,
]


# `show_card` resolve em `get_tool` mas só o app a oferece (`SCHEMAS_APP`, com emissor).
_BY_NAME: dict[str, Tool] = {t.name: t for t in (*_ALL_TOOLS, *mostrar_card.TOOLS)}


SCHEMAS: list[dict[str, Any]] = [t.schema for t in _ALL_TOOLS]

# No app estas calculam por outra função o número que um card já mostra (o card ganha):
# somem de `SCHEMAS_APP` e o `runner._dispatch_tool` as recusa (histórico compartilhado
# com o WhatsApp). O WhatsApp e o /ai/chat seguem com todas.
OCULTAS_NO_APP = frozenset({
    "get_balance", "forecast_balance", "get_spending_trend", "get_card_limit_usage",
    "get_total_debt", "forecast_next_bill", "list_installments",
})

SCHEMAS_APP: list[dict[str, Any]] = [
    *(s for s in SCHEMAS if s["function"]["name"] not in OCULTAS_NO_APP),
    *(t.schema for t in mostrar_card.TOOLS),
]


WRITE_TOOL_NAMES: set[str] = {t.name for t in _ALL_TOOLS if t.is_write}


def get_tool(name: str) -> Optional[Tool]:
    return _BY_NAME.get(name)


__all__ = ["OCULTAS_NO_APP", "SCHEMAS", "SCHEMAS_APP", "WRITE_TOOL_NAMES", "get_tool", "Tool"]
