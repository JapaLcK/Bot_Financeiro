"""
core/services/ai_chat/tools/__init__.py — registro central de tools da IA.

Cada módulo neste pacote exporta uma lista `TOOLS: list[Tool]` (ver `_base.py`).
Este `__init__` agrega todas e expõe utilitários comuns:

  SCHEMAS         — list[dict] no formato OpenAI, passada em tools=[]
  WRITE_TOOL_NAMES — set de nomes de tools que precisam de pending action
  get_tool(name)   — lookup; retorna Tool ou None

**Adicionar um domínio novo:** criar `tools/<dominio>.py` com `TOOLS = [...]`,
importar abaixo e estender `_ALL_TOOLS`. Nada mais.
"""
from __future__ import annotations

from typing import Any, Optional

from . import categories
from ._base import Tool


_ALL_TOOLS: list[Tool] = [
    *categories.TOOLS,
]


_BY_NAME: dict[str, Tool] = {t.name: t for t in _ALL_TOOLS}


SCHEMAS: list[dict[str, Any]] = [t.schema for t in _ALL_TOOLS]


WRITE_TOOL_NAMES: set[str] = {t.name for t in _ALL_TOOLS if t.is_write}


def get_tool(name: str) -> Optional[Tool]:
    return _BY_NAME.get(name)


__all__ = ["SCHEMAS", "WRITE_TOOL_NAMES", "get_tool", "Tool"]
