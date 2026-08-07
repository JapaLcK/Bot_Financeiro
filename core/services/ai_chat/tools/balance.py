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
    # Saldo verdadeiro: Carteira manual + saldos das contas bancárias conectadas
    # via Open Finance (autoritativos, atualizados pelo sync).
    cb = db.get_consolidated_balance(user_id)
    return {
        "balance": float(cb["consolidated"] or 0),
        "wallet_balance": float(cb["manual"] or 0),
        "connected_banks_balance": float(cb["open_finance_bank"] or 0),
        "connected_bank_accounts": int(cb.get("of_bank_count") or 0),
    }


TOOLS: list[Tool] = [
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "get_balance",
                "description": "Retorna o saldo atual do usuário no PigBank, em reais. `balance` é o saldo total (Carteira manual + contas bancárias conectadas via Open Finance); `wallet_balance` é só a Carteira e `connected_banks_balance` é a soma dos bancos conectados (`connected_bank_accounts` = nº de contas conectadas, 0 = nenhum banco). Use sempre que ele perguntar 'qual meu saldo?', 'quanto tenho?', 'quanto sobrou?' ou variações.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        is_write=False,
        execute=_get_balance,
    ),
]
