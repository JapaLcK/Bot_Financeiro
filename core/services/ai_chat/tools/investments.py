"""
core/services/ai_chat/tools/investments.py — tools de investimentos.

Read:
  - list_investments: lista investimentos cadastrados com saldo, taxa, vencimento
  - get_investment_summary: totais agregados (valor aplicado, número de ativos)

Write (precisam de confirmação humana):
  - create_investment:     cadastra um investimento novo
  - investment_deposit:    aporta na conta corrente → investimento
  - investment_withdraw:   resgata investimento → conta corrente
  - delete_investment:     apaga (só com saldo zero, regra do db)

Importante: a regra "NUNCA dê conselho de investimento específico" (regra 4
do system prompt) continua valendo. Listar carteira, ver saldo, aportar
quantia que o user pediu — nada disso é dar conselho. Conselho seria
"compre Petrobras". As tools aqui são operações sobre os dados do user.
"""
from __future__ import annotations

from typing import Any

import db

from ._base import Tool


_PERIODS = ("daily", "monthly", "yearly")


# ─── Read ───────────────────────────────────────────────────────────────────

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


def _get_investment_summary(user_id: int, args: dict[str, Any]) -> dict[str, Any]:
    rows = db.list_investments(user_id)
    total = sum(float(r["balance"] or 0) for r in rows)
    return {
        "total_invested": total,
        "investment_count": len(rows),
        "investments_with_balance": sum(1 for r in rows if float(r["balance"] or 0) > 0),
    }


# ─── Write: create_investment ───────────────────────────────────────────────

def _create_investment_summary(args: dict[str, Any]) -> str:
    rate = args.get("rate")
    period = args.get("period")
    period_pt = {"daily": "ao dia", "monthly": "ao mês", "yearly": "ao ano"}.get(period or "", period)
    rate_part = f" a {float(rate):.2f}% {period_pt}" if rate is not None else ""
    return f'criar o investimento "{args.get("name")}"{rate_part}'


def _create_investment_execute(user_id: int, args: dict[str, Any]) -> str:
    name = (args.get("name") or "").strip()
    period = (args.get("period") or "").strip()
    try:
        rate = float(args.get("rate"))
    except (TypeError, ValueError):
        return "🐷 Informa uma taxa numérica."
    if not name:
        return "🐷 Faltou o nome do investimento."
    if period not in _PERIODS:
        return '🐷 O period precisa ser "daily", "monthly" ou "yearly".'
    if rate <= 0:
        return "🐷 A taxa precisa ser maior que zero."
    try:
        db.create_investment(user_id, name, rate, period)
        return f'✅ Investimento "{name}" criado.'
    except ValueError as e:
        return f"🐷 Não consegui criar: {e}"
    except Exception as e:
        return f"🐷 Não consegui criar: {e}"


# ─── Write: investment_deposit (aporte) ─────────────────────────────────────

def _investment_deposit_summary(args: dict[str, Any]) -> str:
    name = args.get("name")
    amount = args.get("amount")
    if isinstance(amount, (int, float)):
        return f'aportar R$ {amount:.2f} no investimento "{name}"'
    return f'aportar no investimento "{name}"'


def _investment_deposit_execute(user_id: int, args: dict[str, Any]) -> str:
    name = (args.get("name") or "").strip()
    try:
        amount = float(args.get("amount") or 0)
    except (TypeError, ValueError):
        return "🐷 Valor inválido."
    if not name or amount <= 0:
        return "🐷 Faltou o nome do investimento ou o valor."
    try:
        db.investment_deposit_from_account(user_id, name, amount)
        return f'✅ Aporte de R$ {amount:.2f} no "{name}" registrado.'
    except Exception as e:
        return f"🐷 Não consegui aportar: {e}"


# ─── Write: investment_withdraw (resgate) ───────────────────────────────────

def _investment_withdraw_summary(args: dict[str, Any]) -> str:
    name = args.get("name")
    amount = args.get("amount")
    if isinstance(amount, (int, float)):
        return f'resgatar R$ {amount:.2f} do investimento "{name}"'
    return f'resgatar do investimento "{name}"'


def _investment_withdraw_execute(user_id: int, args: dict[str, Any]) -> str:
    name = (args.get("name") or "").strip()
    try:
        amount = float(args.get("amount") or 0)
    except (TypeError, ValueError):
        return "🐷 Valor inválido."
    if not name or amount <= 0:
        return "🐷 Faltou o nome do investimento ou o valor."
    try:
        db.investment_withdraw_to_account(user_id, name, amount)
        return f'✅ Resgate de R$ {amount:.2f} do "{name}" registrado.'
    except Exception as e:
        return f"🐷 Não consegui resgatar: {e}"


# ─── Write: delete_investment ───────────────────────────────────────────────

def _delete_investment_summary(args: dict[str, Any]) -> str:
    return f'apagar o investimento "{args.get("name")}"'


def _delete_investment_execute(user_id: int, args: dict[str, Any]) -> str:
    name = (args.get("name") or "").strip()
    if not name:
        return "🐷 Faltou o nome do investimento."
    try:
        db.delete_investment(user_id, name)
        return f'✅ Investimento "{name}" apagado.'
    except Exception as e:
        return f"🐷 Não consegui apagar: {e}"


# ─── Tools registry ─────────────────────────────────────────────────────────

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
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "get_investment_summary",
                "description": "Retorna totais agregados da carteira: valor total aplicado, número de investimentos, quantos têm saldo > 0. Use pra 'quanto eu tenho investido no total?', 'quantos ativos eu tenho?'.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        is_write=False,
        execute=_get_investment_summary,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "create_investment",
                "description": "Cria um investimento novo (sem aporte inicial — saldo começa em 0). ESCRITA — pede confirmação. Pra aportar use investment_deposit depois.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Nome do investimento (ex: 'Tesouro Selic 2029', 'CDB Nubank')."},
                        "rate": {"type": "number", "minimum": 0.01, "description": "Taxa em % (ex: 14.25 para 14,25%)."},
                        "period": {"type": "string", "enum": list(_PERIODS), "description": "Periodicidade da taxa: daily, monthly ou yearly."},
                    },
                    "required": ["name", "rate", "period"],
                },
            },
        },
        is_write=True,
        summary=_create_investment_summary,
        execute=_create_investment_execute,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "investment_deposit",
                "description": "Aporta dinheiro da conta corrente num investimento existente. Use pra 'aportar 1000 no Tesouro Selic', 'invest 500 no CDB'. ESCRITA — pede confirmação.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Nome do investimento (deve já existir)."},
                        "amount": {"type": "number", "minimum": 0.01, "description": "Valor em reais."},
                    },
                    "required": ["name", "amount"],
                },
            },
        },
        is_write=True,
        summary=_investment_deposit_summary,
        execute=_investment_deposit_execute,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "investment_withdraw",
                "description": "Resgata dinheiro de um investimento de volta pra conta corrente. FIFO por lotes. Use pra 'resgatar 500 do CDB Nubank'. ESCRITA — pede confirmação.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "amount": {"type": "number", "minimum": 0.01},
                    },
                    "required": ["name", "amount"],
                },
            },
        },
        is_write=True,
        summary=_investment_withdraw_summary,
        execute=_investment_withdraw_execute,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "delete_investment",
                "description": "Apaga um investimento do cadastro. Só funciona se saldo for zero — se houver saldo, resgata primeiro. ESCRITA — pede confirmação.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                    },
                    "required": ["name"],
                },
            },
        },
        is_write=True,
        summary=_delete_investment_summary,
        execute=_delete_investment_execute,
    ),
]
