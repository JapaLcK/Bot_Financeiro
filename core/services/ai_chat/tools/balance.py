"""
core/services/ai_chat/tools/balance.py — tools de saldo.

Read:
  - get_balance: saldo atual da conta corrente do user.
"""
from __future__ import annotations

from typing import Any

import db

from ._base import Tool


def _get_balance(user_id: int, args: dict[str, Any]) -> dict[str, Any]:
    balance = db.get_balance(user_id)
    return {"balance": float(balance)}


TOOLS: list[Tool] = [
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "get_balance",
                "description": "Retorna o saldo atual da conta corrente do usuário no PigBank, em reais. Use sempre que ele perguntar 'qual meu saldo?', 'quanto tenho?', 'quanto sobrou?' ou variações.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        is_write=False,
        execute=_get_balance,
    ),
]
