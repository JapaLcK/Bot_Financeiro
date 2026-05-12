"""
core/services/ai_chat/tools/cards.py — tools de cartões de crédito.

Read:
  - list_cards: cartões cadastrados (nome, fechamento, vencimento, default)
  - get_open_bill: fatura em aberto de um cartão (total, pagado, itens)
  - get_card_limit_usage: limite, usado e disponível de um cartão
  - get_total_debt: soma das faturas em aberto de TODOS os cartões
  - list_installments: parcelamentos ativos (com parcelas pendentes)
  - forecast_next_bill: projeção da próxima fatura (já considera parcelas
    futuras, que ficam materializadas no DB ao registrar uma compra)

Write:
  - add_credit_purchase (auto-execute): registra compra na fatura, opcionalmente
    parcelada. Delega pro handler tradicional (`add_credit_from_entities`).
  - pay_bill (com confirmação): paga (parcial ou total) a fatura em aberto.

NOTA: criar/apagar cartão e refunds seguem no fluxo do bot tradicional por
enquanto.
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


# ─── Read: get_card_limit_usage ─────────────────────────────────────────────

def _get_card_limit_usage(user_id: int, args: dict[str, Any]) -> dict[str, Any]:
    """Retorna limite, usado e livre de um cartão.

    `usado` é a soma das faturas em aberto/fechadas (status='open' OR 'closed')
    descontando o que já foi pago. Faturas 'paid' não comprometem limite. Se
    o cartão não tem limite registrado (`credit_limit IS NULL`), retorna
    apenas `used` e instrução pro user cadastrar via dashboard.
    """
    card_id = _resolve_card_id(user_id, args.get("card_name"))
    if not card_id:
        return {"error": "Nenhum cartão cadastrado ou nome não encontrado."}

    card = db.get_card_by_id(user_id, card_id)
    if not card:
        return {"error": "Cartão não encontrado."}

    name = card.get("name") or "cartão"
    limit_raw = card.get("credit_limit")
    used = float(db.get_card_credit_usage(user_id, card_id))

    if limit_raw is None:
        return {
            "card_name": name,
            "credit_limit": None,
            "used": used,
            "available": None,
            "note": "Limite não cadastrado. Pra ver quanto sobra, cadastre o limite do cartão no dashboard.",
        }

    limit = float(limit_raw)
    available = max(0.0, limit - used)
    return {
        "card_name": name,
        "credit_limit": limit,
        "used": used,
        "available": available,
        "used_pct": round(100 * used / limit, 1) if limit > 0 else 0.0,
    }


# ─── Read: get_total_debt ───────────────────────────────────────────────────

def _get_total_debt(user_id: int, args: dict[str, Any]) -> dict[str, Any]:
    """Soma o que falta pagar de TODAS as faturas em aberto.

    `remaining = total - paid_amount`, nunca negativo. Cartão sem fatura
    aberta simplesmente não aparece. Útil pra "quanto devo no cartão?"
    sem precisar perguntar cartão por cartão.
    """
    bills = db.list_open_bills(user_id)
    items = []
    total = 0.0
    for b in bills:
        remaining = max(0.0, float(b.get("total") or 0) - float(b.get("paid_amount") or 0))
        if remaining <= 0:
            continue
        total += remaining
        items.append({
            "card_name": b.get("card_name"),
            "bill_id": b.get("id"),
            "period_start": b["period_start"].isoformat() if b.get("period_start") else None,
            "period_end": b["period_end"].isoformat() if b.get("period_end") else None,
            "total": float(b.get("total") or 0),
            "paid": float(b.get("paid_amount") or 0),
            "remaining": remaining,
        })
    return {
        "total_debt": round(total, 2),
        "bills": items,
        "count": len(items),
    }


# ─── Read: list_installments ────────────────────────────────────────────────

def _list_installments(user_id: int, args: dict[str, Any]) -> dict[str, Any]:
    """Parcelamentos ativos do user (com pelo menos 1 parcela pendente).

    Por padrão filtra `only_pending=True` — só mostra os que ainda têm
    parcela a vencer. Útil pra "meus parcelamentos?", "o que tenho parcelado?".
    """
    only_pending = bool(args.get("only_pending", True))
    try:
        limit = int(args.get("limit") or 15)
    except (TypeError, ValueError):
        limit = 15
    limit = max(1, min(limit, 50))

    rows = db.list_installment_groups(user_id, limit=limit)
    groups = []
    for r in rows:
        n_pending = int(r.get("n_pending") or 0)
        if only_pending and n_pending == 0:
            continue
        groups.append({
            "group_id": r.get("group_id"),
            "card_name": r.get("card_name"),
            "nota": r.get("nota"),
            "n_total": int(r.get("n_total") or 0),
            "n_pending": n_pending,
            "total": float(r.get("total") or 0),
            "total_pending": float(r.get("total_pending") or 0),
            "last_purchase": r["last_purchase"].isoformat() if r.get("last_purchase") else None,
        })
    return {
        "groups": groups,
        "count": len(groups),
    }


# ─── Read: forecast_next_bill ───────────────────────────────────────────────

def _forecast_next_bill(user_id: int, args: dict[str, Any]) -> dict[str, Any]:
    """Projeção da PRÓXIMA fatura — a open bill com fechamento mais próximo.

    Parcelamentos já materializaram bills futuras quando a compra foi feita,
    então a "próxima fatura" do user é a open bill com `period_end` mais
    próximo de hoje. Lê de `list_open_bills` (já ordenada por period_end
    asc) e pega a 1ª de cada cartão. Se filtrar por `card_name`, retorna
    só esse cartão.

    Nota: NÃO cria bills futuras pra exibição — se não há open bill, retorna
    total=0. Pra agendar fatura inexistente, o user precisa registrar uma
    compra naquele período.
    """
    card_name = (args.get("card_name") or "").strip() or None

    target_card_id: int | None = None
    if card_name:
        cid = db.get_card_id_by_name(user_id, card_name)
        if not cid:
            return {"error": f"Não achei cartão com nome '{card_name}'."}
        target_card_id = int(cid)

    open_bills = db.list_open_bills(user_id)  # vem por period_end asc

    items = []
    total = 0.0
    seen_cards: set[int] = set()
    for b in open_bills:
        cid = int(b["card_id"])
        if target_card_id is not None and cid != target_card_id:
            continue
        if cid in seen_cards:
            continue  # já pegou a próxima desse cartão (mais cedo no tempo)
        seen_cards.add(cid)
        amount = float(b.get("total") or 0)
        total += amount
        items.append({
            "card_name": b.get("card_name"),
            "bill_id": b.get("id"),
            "period_start": b["period_start"].isoformat() if b.get("period_start") else None,
            "period_end": b["period_end"].isoformat() if b.get("period_end") else None,
            "total": amount,
        })

    return {
        "total": round(total, 2),
        "cards": items,
        "count": len(items),
    }


# ─── Write: add_credit_purchase (auto-execute) ──────────────────────────────

def _parse_iso_date_or_none(s: str | None) -> "date | None":
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def _add_credit_purchase_execute(user_id: int, args: dict[str, Any]) -> str:
    try:
        valor = float(args.get("valor") or 0)
    except (TypeError, ValueError):
        return "🐷 Valor inválido."
    if valor <= 0:
        return "🐷 O valor precisa ser maior que zero."

    parcelas_raw = args.get("parcelas")
    parcelas: int | None = None
    if parcelas_raw is not None:
        try:
            parcelas = int(parcelas_raw)
        except (TypeError, ValueError):
            return "🐷 Número de parcelas inválido."
        if parcelas < 1 or parcelas > 60:
            return "🐷 Número de parcelas inválido (1 a 60)."
        if parcelas == 1:
            parcelas = None  # à vista

    from core.handlers.credit import add_credit_from_entities

    return add_credit_from_entities(
        user_id,
        valor=valor,
        card_name=(args.get("card_name") or "").strip() or None,
        descricao=(args.get("descricao") or "").strip() or None,
        categoria=(args.get("categoria") or "").strip() or None,
        purchased_at=_parse_iso_date_or_none(args.get("data")),
        installments=parcelas,
    )


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

    card = db.get_card_by_id(user_id, card_id)
    card_name = card["name"] if card else "cartão"

    amount = args.get("amount")
    try:
        amount_f = float(amount) if amount is not None else None
    except (TypeError, ValueError):
        return "🐷 Valor inválido."
    if amount_f is None or amount_f <= 0:
        return "🐷 Informa o valor a pagar."

    try:
        db.pay_bill_amount(user_id, card_id, card_name, amount_f)
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
                "name": "get_card_limit_usage",
                "description": (
                    "Retorna limite, valor usado e disponível de um cartão "
                    "de crédito. Use pra 'quanto tenho livre no Nubank?', "
                    "'quanto já usei do limite?', 'qual meu limite disponível?'. "
                    "Se card_name não for passado, usa o cartão padrão. Se o "
                    "limite não estiver cadastrado, retorna apenas o usado e "
                    "instrui o user a cadastrar no dashboard."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "card_name": {
                            "type": "string",
                            "description": "Nome do cartão (ex: 'Nubank'). Omitir usa o padrão.",
                        },
                    },
                },
            },
        },
        is_write=False,
        execute=_get_card_limit_usage,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "get_total_debt",
                "description": (
                    "Soma o que falta pagar de TODAS as faturas em aberto, "
                    "agregando todos os cartões. Use SEMPRE pra 'quanto eu "
                    "devo?', 'qual minha dívida total no cartão?', 'quanto "
                    "tô devendo nas faturas?', 'minha dívida hoje'. Retorna "
                    "o total e o detalhe por cartão. Sem args."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        },
        is_write=False,
        execute=_get_total_debt,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "list_installments",
                "description": (
                    "Lista parcelamentos do user — por padrão só os com "
                    "parcelas ainda a vencer (`only_pending=true`). Use pra "
                    "'meus parcelamentos', 'o que tenho parcelado?', 'quais "
                    "parcelamentos ativos?'. Retorna cartão, descrição, "
                    "total, total pendente e quantas parcelas faltam."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "only_pending": {
                            "type": "boolean",
                            "default": True,
                            "description": "Se true (padrão), filtra os que ainda têm parcela a pagar.",
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 50,
                            "default": 15,
                            "description": "Quantos grupos retornar (1–50, padrão 15).",
                        },
                    },
                },
            },
        },
        is_write=False,
        execute=_list_installments,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "forecast_next_bill",
                "description": (
                    "Projeção da PRÓXIMA fatura — por cartão e total. "
                    "Já considera parcelas futuras (que foram registradas "
                    "na hora da compra). Use pra 'quanto vai vir na próxima "
                    "fatura?', 'projeção do próximo mês no cartão', 'qual "
                    "vai ser minha próxima fatura?'. Se card_name for "
                    "passado, retorna só esse cartão; senão, agrega todos."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "card_name": {
                            "type": "string",
                            "description": "Nome do cartão (ex: 'Nubank'). Omitir agrega todos.",
                        },
                    },
                },
            },
        },
        is_write=False,
        execute=_forecast_next_bill,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "add_credit_purchase",
                "description": (
                    "Registra uma compra no cartão de crédito (vai pra "
                    "fatura, NÃO debita a conta corrente). Use pra 'gastei "
                    "100 no cartão Nubank', 'paguei 50 no crédito', 'Crédito "
                    "44,90 Pagamento Claro', 'parcelei 300 em 3x'. Suporta "
                    "parcelamento via `parcelas`. NÃO use pra despesas que "
                    "saíram da conta corrente — pra essas use `add_launch`. "
                    "EXECUTA DIRETO (sem perguntar 'confirma?')."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "valor": {
                            "type": "number",
                            "minimum": 0.01,
                            "description": "Valor TOTAL da compra (não da parcela). Ex: 100, 44.90.",
                        },
                        "card_name": {
                            "type": "string",
                            "description": "Nome do cartão. Omita pra usar o cartão padrão.",
                        },
                        "descricao": {
                            "type": "string",
                            "description": "Descrição da compra. Ex: 'mercado', 'Pagamento Claro', 'Uber'.",
                        },
                        "categoria": {
                            "type": "string",
                            "description": "Categoria explícita. Omita pra inferir.",
                        },
                        "data": {
                            "type": "string",
                            "description": "Data da compra em ISO 8601 (YYYY-MM-DD). Omita pra usar hoje.",
                        },
                        "parcelas": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 60,
                            "description": "Número de parcelas (omita ou 1 = à vista).",
                        },
                    },
                    "required": ["valor"],
                },
            },
        },
        is_write=True,
        requires_confirmation=False,
        execute=_add_credit_purchase_execute,
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
