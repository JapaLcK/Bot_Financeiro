"""
core/services/ai_chat/tools/balance.py — tools de saldo.

Read:
  - get_balance: saldo atual da conta corrente do user.
"""
from __future__ import annotations

from typing import Any

import db

from core.services.funding import aviso_conferir

from ._base import Tool


def _reconciliation_floats(cb: dict) -> dict[str, Any]:
    # O runner serializa read tools com json.dumps(default=str) — Decimal cru
    # sairia como string, não número. Float aqui garante o tipo JSON certo.
    rec = cb.get("reconciliation") or {}
    return {
        "pending_count": int(rec.get("pending_count") or 0),
        "delta_se_confirmar": float(rec.get("delta_se_confirmar") or 0),
    }


def _get_balance(user_id: int, args: dict[str, Any]) -> dict[str, Any]:
    # Saldo verdadeiro: Carteira manual + saldos das contas bancárias conectadas
    # via Open Finance (autoritativos, atualizados pelo sync).
    # Em beta (consolidated_balance_enabled): fora do allowlist de teste, devolve
    # o formato antigo (só o saldo manual), sem citar bancos conectados.
    from core.services.plan_service import consolidated_balance_enabled

    cb = db.get_consolidated_balance(user_id)
    if not consolidated_balance_enabled(user_id):
        balance = float(cb["manual"] or 0)
        return {
            "balance": balance,
            "reconciliation": _reconciliation_floats(cb),
            "aviso_conferir": aviso_conferir(cb["manual"], cb.get("reconciliation")),
        }
    balance = float(cb["consolidated"] or 0)
    return {
        "bank_movements": cb.get("bank_movements") or {},
        "balance": balance,
        "wallet_balance": float(cb["manual"] or 0),
        "connected_banks_balance": float(cb["open_finance_bank"] or 0),
        "connected_bank_accounts": int(cb.get("of_bank_count") or 0),
        "reconciliation": _reconciliation_floats(cb),
        "aviso_conferir": aviso_conferir(cb["consolidated"], cb.get("reconciliation")),
    }


TOOLS: list[Tool] = [
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "get_balance",
                "description": "Retorna o saldo atual do usuário no PigBank, em reais. `balance` é o saldo total (Carteira manual + contas bancárias conectadas via Open Finance); `wallet_balance` é só a Carteira e `connected_banks_balance` é a soma dos bancos conectados (`connected_bank_accounts` = nº de contas conectadas, 0 = nenhum banco). Se bank_movements.pending_count for maior que zero, informe que há declarações não confirmadas e o patrimônio está a conferir; os saldos são observados, não prova dessas operações. Se `aviso_conferir` não vier vazio, REPITA esse texto na resposta ao usuário — é o aviso de que há lançamentos a conciliar com o banco e quanto o saldo pode virar se confirmados (`reconciliation.pending_count`/`delta_se_confirmar`). Use sempre que ele perguntar 'qual meu saldo?', 'quanto tenho?', 'quanto sobrou?' ou variações.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        is_write=False,
        execute=_get_balance,
    ),
]
