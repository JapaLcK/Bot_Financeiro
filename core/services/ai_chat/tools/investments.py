"""
core/services/ai_chat/tools/investments.py — tools de investimentos.

Read:
  - list_investments: lista investimentos cadastrados com saldo, taxa, vencimento

ESCRITA NÃO IMPLEMENTADA AQUI: aporte/resgate/criação de investimento
seguem no fluxo do bot tradicional. Razões:
  - O parsing de aporte ("inverti 1000 no CDB X a 110% CDI") tem entidades
    complexas que o classifier já trata.
  - Lucas tem regra dura: "NUNCA dê conselho de investimento específico".
    Tools de leitura são seguras; writes invitam o LLM a "ajudar a decidir"
    e podem cruzar essa linha. Quando precisar, criamos com guardrails.
"""
from __future__ import annotations

from typing import Any

import db

from ._base import Tool


def _list_investments(user_id: int, args: dict[str, Any]) -> dict[str, Any]:
    rows = db.list_investments(user_id)
    return {
        "investments": [
            {
                "id": r["id"],
                "name": r["name"],
                "balance": float(r["balance"] or 0),
                "rate": float(r["rate"]) if r.get("rate") is not None else None,
                "period": r.get("period"),
                "asset_type": r.get("asset_type"),
                "indexer": r.get("indexer"),
                "issuer": r.get("issuer"),
                "purchase_date": r["purchase_date"].isoformat() if r.get("purchase_date") else None,
                "maturity_date": r["maturity_date"].isoformat() if r.get("maturity_date") else None,
            }
            for r in rows
        ]
    }


TOOLS: list[Tool] = [
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "list_investments",
                "description": "Lista os investimentos cadastrados do usuário com saldo, taxa, emissor, vencimento. Use pra 'quais meus investimentos?', 'minha carteira', 'quanto tenho aplicado'.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        is_write=False,
        execute=_list_investments,
    ),
]
