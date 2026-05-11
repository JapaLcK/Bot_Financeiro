"""
core/services/ai_chat/tools/cards.py — tools de cartões de crédito.

Read:
  - list_cards: cartões cadastrados (nome, fechamento, vencimento, default)
  - get_open_bill: fatura em aberto de um cartão (total, pagado, itens)

Write (precisam de confirmação humana):
  - pay_bill: paga (parcial ou total) a fatura em aberto

NOTA: criar/apagar cartão, lançar compras parceladas, refunds etc seguem no
fluxo do bot tradicional por enquanto — esses fluxos têm parsing complexo
(OFX, parcelamento) que não compensa duplicar via IA agora.
"""
from __future__ import annotations

from datetime import date
from typing import Any

import db

from ._base import Tool


# ─── Read ───────────────────────────────────────────────────────────────────

def _list_cards(user_id: int, args: dict[str, Any]) -> dict[str, Any]:
    rows = db.list_cards(user_id)
    return {
        "cards": [
            {
                "id": r["id"],
                "name": r["name"],
                "closing_day": r["closing_day"],
                "due_day": r["due_day"],
                "credit_limit": float(r["credit_limit"]) if r.get("credit_limit") is not None else None,
                "is_default": bool(r.get("is_default")),
            }
            for r in rows
        ]
    }


def _resolve_card_id(user_id: int, card_name: str | None) -> int | None:
    if card_name:
        cid = db.get_card_id_by_name(user_id, card_name.strip())
        if cid:
            return int(cid)
    return db.get_default_card_id(user_id)


def _get_open_bill(user_id: int, args: dict[str, Any]) -> dict[str, Any]:
    card_id = _resolve_card_id(user_id, args.get("card_name"))
    if not card_id:
        return {"error": "Nenhum cartão cadastrado ou nome não encontrado."}

    result = db.get_open_bill_summary(user_id, card_id)
    if not result:
        return {"error": "Fatura em aberto não encontrada pra esse cartão."}

    bill, items = result
    return {
        "card_id": card_id,
        "bill_id": bill["id"],
        "period_start": bill["period_start"].isoformat(),
        "period_end": bill["period_end"].isoformat(),
        "total": float(bill["total"] or 0),
        "paid_amount": float(bill["paid_amount"] or 0),
        "remaining": float((bill["total"] or 0) - (bill["paid_amount"] or 0)),
        "status": bill["status"],
        "items": [
            {
                "id": it["id"],
                "valor": float(it["valor"] or 0),
                "categoria": it.get("categoria"),
                "nota": it.get("nota"),
                "purchased_at": it["purchased_at"].isoformat() if it.get("purchased_at") else None,
                "installment": (
                    f"{it.get('installment_no')}/{it.get('installments_total')}"
                    if it.get("installments_total") and int(it.get("installments_total") or 0) > 1
                    else None
                ),
                "is_refund": bool(it.get("is_refund")),
            }
            for it in items
        ],
    }


# ─── Write: pay_bill ────────────────────────────────────────────────────────

def _pay_bill_summary(args: dict[str, Any]) -> str:
    card = args.get("card_name") or "cartão padrão"
    if isinstance(args.get("amount"), (int, float)):
        return f'pagar R$ {args.get("amount"):.2f} da fatura do {card}'
    return f'pagar a fatura do {card}'


def _pay_bill_execute(user_id: int, args: dict[str, Any]) -> str:
    card_id = _resolve_card_id(user_id, args.get("card_name"))
    if not card_id:
        return "🐷 Não achei o cartão informado."

    amount = args.get("amount")
    try:
        amount_f = float(amount) if amount is not None else None
    except (TypeError, ValueError):
        return "🐷 Valor inválido."
    if amount_f is None or amount_f <= 0:
        return "🐷 Informa o valor a pagar."

    try:
        db.pay_bill_amount(user_id, card_id, amount_f, as_of=date.today())
        return f"✅ Pagamento de R$ {amount_f:.2f} registrado."
    except Exception as e:
        return f"🐷 Não consegui registrar o pagamento: {e}"


# ─── Tools registry ─────────────────────────────────────────────────────────

TOOLS: list[Tool] = [
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "list_cards",
                "description": "Lista os cartões de crédito cadastrados, com dia de fechamento, vencimento e limite. Use pra 'quais cartões tenho?', 'meus cartões'.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        is_write=False,
        execute=_list_cards,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "get_open_bill",
                "description": "Retorna a fatura em aberto de um cartão (total, pago, restante, itens). Se card_name não for passado, usa o cartão padrão do user.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "card_name": {"type": "string", "description": "Nome do cartão (ex: 'Nubank'). Omitir usa o padrão."},
                    },
                },
            },
        },
        is_write=False,
        execute=_get_open_bill,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "pay_bill",
                "description": "Registra pagamento (parcial ou total) da fatura em aberto. Debita a conta corrente. ESCRITA — pede confirmação.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "card_name": {"type": "string", "description": "Nome do cartão. Omitir usa o padrão."},
                        "amount": {"type": "number", "minimum": 0.01, "description": "Valor a pagar em reais."},
                    },
                    "required": ["amount"],
                },
            },
        },
        is_write=True,
        summary=_pay_bill_summary,
        execute=_pay_bill_execute,
    ),
]
