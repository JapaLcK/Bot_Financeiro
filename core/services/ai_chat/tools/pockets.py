"""
core/services/ai_chat/tools/pockets.py — tools de caixinhas.

Read:
  - list_pockets: lista caixinhas com saldo e descrição

Write (precisam de confirmação humana):
  - create_pocket
  - pocket_deposit  (Conta → Caixinha)
  - pocket_withdraw (Caixinha → Conta)
  - delete_pocket   (só se saldo == 0; valida em runtime)
"""
from __future__ import annotations

from typing import Any

import db

from ._base import Tool


# ─── Read ───────────────────────────────────────────────────────────────────

def _list_pockets(user_id: int, args: dict[str, Any]) -> dict[str, Any]:
    rows = db.list_pockets(user_id)
    return {
        "pockets": [
            {
                "id": r["id"],
                "name": r["name"],
                "balance": float(r["balance"] or 0),
                "description": r.get("description"),
                "interest_enabled": bool(r.get("interest_enabled")),
                "interest_rate": float(r.get("interest_rate") or 1),
                "interest_period": r.get("interest_period") or "cdi",
            }
            for r in rows
        ]
    }


# ─── Write: create_pocket ───────────────────────────────────────────────────

def _create_pocket_summary(args: dict[str, Any]) -> str:
    name = (args.get("name") or "").strip()
    desc = (args.get("description") or "").strip()
    if desc:
        return f'criar a caixinha "{name}" ({desc})'
    return f'criar a caixinha "{name}"'


def _create_pocket_execute(user_id: int, args: dict[str, Any]) -> str:
    name = (args.get("name") or "").strip()
    description = (args.get("description") or "").strip() or None
    if not name:
        return "🐷 Faltou o nome da caixinha."
    try:
        db.create_pocket(user_id, name, description=description)
        return f'✅ Caixinha "{name}" criada.'
    except Exception as e:
        from core.services.plan_limits import PlanLimitExceeded
        if isinstance(e, PlanLimitExceeded):
            return e.message
        return f"🐷 Não consegui criar a caixinha: {e}"


# ─── Write: pocket_deposit ──────────────────────────────────────────────────

def _pocket_deposit_summary(args: dict[str, Any]) -> str:
    return (
        f'depositar R$ {args.get("amount"):.2f} na caixinha '
        f'"{args.get("pocket_name")}"'
        if isinstance(args.get("amount"), (int, float))
        else f'depositar na caixinha "{args.get("pocket_name")}"'
    )


def _pocket_deposit_execute(user_id: int, args: dict[str, Any]) -> str:
    pocket_name = (args.get("pocket_name") or "").strip()
    amount = float(args.get("amount") or 0)
    if not pocket_name or amount <= 0:
        return "🐷 Faltou o nome da caixinha ou o valor."
    try:
        db.pocket_deposit_from_account(user_id, pocket_name, amount)
        return f'✅ Depositado R$ {amount:.2f} na caixinha "{pocket_name}".'
    except Exception as e:
        return f"🐷 Não consegui depositar: {e}"


# ─── Write: pocket_withdraw ─────────────────────────────────────────────────

def _pocket_withdraw_summary(args: dict[str, Any]) -> str:
    return (
        f'sacar R$ {args.get("amount"):.2f} da caixinha '
        f'"{args.get("pocket_name")}"'
        if isinstance(args.get("amount"), (int, float))
        else f'sacar da caixinha "{args.get("pocket_name")}"'
    )


def _pocket_withdraw_execute(user_id: int, args: dict[str, Any]) -> str:
    pocket_name = (args.get("pocket_name") or "").strip()
    amount = float(args.get("amount") or 0)
    if not pocket_name or amount <= 0:
        return "🐷 Faltou o nome da caixinha ou o valor."
    try:
        db.pocket_withdraw_to_account(user_id, pocket_name, amount)
        return f'✅ Sacado R$ {amount:.2f} da caixinha "{pocket_name}".'
    except Exception as e:
        return f"🐷 Não consegui sacar: {e}"


# ─── Write: delete_pocket ───────────────────────────────────────────────────

def _delete_pocket_summary(args: dict[str, Any]) -> str:
    return f'apagar a caixinha "{args.get("pocket_name")}"'


def _delete_pocket_execute(user_id: int, args: dict[str, Any]) -> str:
    pocket_name = (args.get("pocket_name") or "").strip()
    if not pocket_name:
        return "🐷 Faltou o nome da caixinha."
    try:
        db.delete_pocket(user_id, pocket_name)
        return f'✅ Caixinha "{pocket_name}" apagada.'
    except ValueError as e:
        if "POCKET_NOT_ZERO" in str(e):
            return (
                f'🐷 A caixinha "{pocket_name}" ainda tem saldo. '
                "Saca o que tem dentro antes de apagar."
            )
        return f"🐷 {e}"
    except LookupError:
        return f'🐷 Não achei a caixinha "{pocket_name}".'


# ─── Tools registry ─────────────────────────────────────────────────────────

TOOLS: list[Tool] = [
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "list_pockets",
                "description": "Lista as caixinhas (cofrinhos) do usuário com nome, saldo e descrição. Use pra 'quais minhas caixinhas?', 'mostra meus cofrinhos', 'quanto tem na caixinha de viagem?'.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        is_write=False,
        execute=_list_pockets,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "create_pocket",
                "description": "Cria uma nova caixinha (cofrinho) com nome e descrição opcional. Ação de ESCRITA — pede confirmação.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Nome da caixinha (ex: 'Viagem', 'Reserva')."},
                        "description": {"type": "string", "description": "Descrição/objetivo opcional."},
                    },
                    "required": ["name"],
                },
            },
        },
        is_write=True,
        summary=_create_pocket_summary,
        execute=_create_pocket_execute,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "pocket_deposit",
                "description": "Move dinheiro da conta corrente para uma caixinha (cofrinho). Use pra 'guarda 100 na viagem', 'manda 50 pra reserva'. ESCRITA — pede confirmação.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pocket_name": {"type": "string"},
                        "amount": {"type": "number", "minimum": 0.01},
                    },
                    "required": ["pocket_name", "amount"],
                },
            },
        },
        is_write=True,
        summary=_pocket_deposit_summary,
        execute=_pocket_deposit_execute,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "pocket_withdraw",
                "description": "Retira dinheiro de uma caixinha (cofrinho) e devolve pra conta corrente. ESCRITA — pede confirmação.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pocket_name": {"type": "string"},
                        "amount": {"type": "number", "minimum": 0.01},
                    },
                    "required": ["pocket_name", "amount"],
                },
            },
        },
        is_write=True,
        summary=_pocket_withdraw_summary,
        execute=_pocket_withdraw_execute,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "delete_pocket",
                "description": "Apaga uma caixinha (cofrinho). SÓ funciona se o saldo for zero — o sistema vai recusar caso tenha dinheiro dentro. ESCRITA — pede confirmação.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pocket_name": {"type": "string"},
                    },
                    "required": ["pocket_name"],
                },
            },
        },
        is_write=True,
        summary=_delete_pocket_summary,
        execute=_delete_pocket_execute,
    ),
]
