"""Sync real Open Finance/Pluggy — puxa contas + transações e grava nas tabelas OF.

Fase 0 do plano de Open Finance: substitui o gerador mock por dados reais.
NÃO toca no saldo manual nem em `launches` — isso é a Fase 1 (import + conciliação).

O trabalho aqui é bloqueante (httpx + DB); chame via asyncio.to_thread a partir das rotas.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from dateutil import parser as _dateutil_parser

from utils_date import _tz

from core.services.pluggy import (
    create_pluggy_api_key,
    list_pluggy_accounts,
    list_pluggy_investments,
    list_pluggy_transactions,
    update_pluggy_item,
)
from db import (
    get_open_finance_connection_by_item_id,
    get_open_finance_snapshot,
    import_open_finance_credit,
    import_open_finance_launches,
    list_pluggy_item_ids,
    save_open_finance_investments,
    save_open_finance_sync,
    sync_imported_open_finance_updates,
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


def _parse_datetime(value: Any) -> datetime | None:
    """Extrai o instante COMPLETO (com hora) de uma transação do Pluggy.

    Retorna um datetime timezone-aware quando o banco envia hora real; retorna
    None quando só há data (hora == 00:00) — nesse caso o import cai no fallback
    de data pura, sem inventar horário. O `date` do Pluggy é ISO 8601, ex.:
    "2026-08-14T15:30:00.000-03:00" (hora real) ou "2026-08-14T00:00:00.000Z"
    (placeholder de só-data). A meia-noite é tratada como "sem hora".
    """
    dt: datetime | None = None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        # "YYYY-MM-DD" puro não carrega hora → sem horário real
        if len(text) == 10 and text.count("-") == 2:
            return None
        try:
            dt = _dateutil_parser.isoparse(text)
        except (ValueError, TypeError, OverflowError):
            return None

    if dt is None:
        return None

    # meia-noite exata = placeholder de "só data" do provedor → sem hora real
    if dt.hour == 0 and dt.minute == 0 and dt.second == 0 and dt.microsecond == 0:
        return None

    # garante timezone-aware; se vier naive, assume o fuso do bot
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_tz())
    return dt


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
        "transacted_at": _parse_datetime(raw.get("date")),
        "category": (str(raw["category"]) if raw.get("category") else None),
        "raw": raw,
    }


def normalize_pluggy_investment(raw: dict) -> dict:
    """Converte o investimento cru da Pluggy pro formato de `save_open_finance_investments`."""
    return {
        "provider_investment_id": str(raw.get("id") or ""),
        "name": str(raw.get("name") or raw.get("type") or "Investimento"),
        "type": str(raw.get("type") or ""),
        "subtype": (str(raw["subtype"]) if raw.get("subtype") else None),
        "currency": str(raw.get("currencyCode") or "BRL"),
        "balance": _to_decimal(raw.get("balance")),
        "raw": raw,
    }


def is_caixinha(investment: dict) -> bool:
    """Caixinha do Nubank / Cofrinho do PicPay = CDB de renda fixa (doc Pluggy)."""
    return (str(investment.get("type") or "").upper() == "FIXED_INCOME"
            and str(investment.get("subtype") or "").upper() == "CDB")


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

    # #10: investimentos (inclui Caixinha/CDB) — espelho, não vira pocket ainda.
    investments = [normalize_pluggy_investment(i) for i in list_pluggy_investments(provider_item_id, api_key)]
    inv_result = save_open_finance_investments(connection["id"], investments)

    # Fase 1: conta BANK → launches (analytics, sem mover saldo); cartão → faturas (opção a).
    imported = import_open_finance_launches(connection["user_id"], connection["id"])
    imported_credit = import_open_finance_credit(connection["user_id"], connection["id"])
    # Propaga correções da Pluggy (transactions/updated) pros já importados (não deixa stale).
    updated = sync_imported_open_finance_updates(connection["user_id"], connection["id"])

    return {
        "ok": True,
        "item_id": provider_item_id,
        "connection_id": connection["id"],
        "user_id": connection["user_id"],
        **result,
        **inv_result,
        "imported": imported,
        "imported_credit": imported_credit,
        "updated": updated,
    }


def refresh_all_pluggy_items(user_id: int | None = None) -> dict:
    """Dispara update na Pluggy pra cada item ativo (Pluggy re-busca do banco e manda
    webhook → sync). Usado pelo tick de refresh periódico. Falhas por item são engolidas."""
    items = list_pluggy_item_ids(user_id)
    triggered = 0
    for item_id in items:
        try:
            update_pluggy_item(item_id)
            triggered += 1
        except Exception:
            pass
    return {"triggered": triggered, "total": len(items)}


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
        "launches_imported": sum((r.get("imported") or {}).get("inserted", 0) for r in results),
        "results": results,
    }
