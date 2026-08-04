"""Sync real Open Finance/Pluggy — puxa contas + transações e grava nas tabelas OF.

Fase 0 do plano de Open Finance: substitui o gerador mock por dados reais.
NÃO toca no saldo manual nem em `launches` — isso é a Fase 1 (import + conciliação).

O trabalho aqui é bloqueante (httpx + DB); chame via asyncio.to_thread a partir das rotas.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from utils_date import _tz

from core.services.pluggy import (
    create_pluggy_api_key,
    list_pluggy_accounts,
    list_pluggy_transactions,
)
from db import (
    get_open_finance_connection_by_item_id,
    get_open_finance_snapshot,
    save_open_finance_sync,
)


def _to_decimal(value: Any, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def _parse_date(value: Any) -> date:
    """Aceita 'YYYY-MM-DD', ISO com hora, ou datetime. Cai pra hoje se não der."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if text:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").date()
        except ValueError:
            pass
    return datetime.now(_tz()).date()


def normalize_pluggy_account(raw: dict) -> dict:
    """Converte a conta crua da Pluggy no formato que `save_open_finance_sync` espera.

    Confia no `balance` como o Pluggy manda (cartão já vem negativo = valor devido).
    Não mexe no sinal — a semântica de consolidação fica pra Fase 1.
    """
    return {
        "provider_account_id": str(raw.get("id") or ""),
        "name": str(raw.get("marketingName") or raw.get("name") or raw.get("type") or "Conta"),
        "type": str(raw.get("type") or "BANK"),
        "subtype": (str(raw["subtype"]) if raw.get("subtype") else None),
        "currency": str(raw.get("currencyCode") or "BRL"),
        "balance": _to_decimal(raw.get("balance")),
        "raw": raw,
    }


def normalize_pluggy_transaction(raw: dict) -> dict:
    """Confia no `amount` do Pluggy, que já vem assinado (negativo = saída, positivo = entrada).

    NÃO deriva o sinal do campo `type`: no cartão, uma compra vem como type=CREDIT com
    amount negativo — inverter pelo type transformaria compra em receita (bug pego no E2E
    sandbox). O `type` fica só no `raw`.
    """
    return {
        "provider_transaction_id": str(raw.get("id") or ""),
        "description": str(raw.get("description") or raw.get("descriptionRaw") or "Transação"),
        "amount": _to_decimal(raw.get("amount")),
        "transaction_date": _parse_date(raw.get("date")),
        "category": (str(raw["category"]) if raw.get("category") else None),
        "raw": raw,
    }


def sync_pluggy_item(provider_item_id: str) -> dict:
    """Sincroniza um item Pluggy: contas + transações → tabelas OF. Idempotente."""
    connection = get_open_finance_connection_by_item_id(provider_item_id)
    if not connection:
        return {"ok": False, "reason": "connection_not_found", "item_id": provider_item_id}

    api_key = create_pluggy_api_key()

    accounts: list[dict] = []
    for raw_account in list_pluggy_accounts(provider_item_id, api_key):
        account = normalize_pluggy_account(raw_account)
        if not account["provider_account_id"]:
            continue
        raw_txs = list_pluggy_transactions(account["provider_account_id"], api_key)
        account["transactions"] = [
            tx for tx in (normalize_pluggy_transaction(t) for t in raw_txs) if tx["provider_transaction_id"]
        ]
        accounts.append(account)

    result = save_open_finance_sync(connection["id"], accounts)
    return {
        "ok": True,
        "item_id": provider_item_id,
        "connection_id": connection["id"],
        "user_id": connection["user_id"],
        **result,
    }


def sync_pluggy_user(user_id: int) -> dict:
    """Sincroniza todos os itens Pluggy de um usuário (útil pra sync manual/testes)."""
    snapshot = get_open_finance_snapshot(user_id)
    items = [
        c["provider_item_id"]
        for c in snapshot.get("connections", [])
        if (c.get("provider") == "pluggy" and c.get("provider_item_id"))
    ]
    results = [sync_pluggy_item(item_id) for item_id in items]
    return {
        "ok": True,
        "items_synced": len(results),
        "accounts_synced": sum(r.get("accounts_synced", 0) for r in results),
        "transactions_synced": sum(r.get("transactions_synced", 0) for r in results),
        "results": results,
    }
