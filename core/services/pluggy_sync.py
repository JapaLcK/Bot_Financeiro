"""Sync real Open Finance/Pluggy — puxa contas + transações e grava nas tabelas OF.

Fase 0 do plano de Open Finance: substitui o gerador mock por dados reais.
NÃO toca no saldo manual nem em `launches` — isso é a Fase 1 (import + conciliação).

O trabalho aqui é bloqueante (httpx + DB); chame via asyncio.to_thread a partir das rotas.
"""

from __future__ import annotations

import time
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from utils_date import _tz

from core.services.pluggy import (
    create_pluggy_api_key,
    get_pluggy_item,
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
    sync_open_finance_caixinhas,
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

    # Trial venceu sem virar assinatura: dados importados ficam, mas o sync PARA
    # (o item nem existe mais na Pluggy). Barra webhook atrasado/replay.
    if str(connection.get("status") or "").upper() == "PAUSED":
        return {"ok": False, "reason": "connection_paused", "item_id": provider_item_id}

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

    # #10: investimentos (inclui Caixinha/CDB) — espelho em open_finance_investments.
    investments = [normalize_pluggy_investment(i) for i in list_pluggy_investments(provider_item_id, api_key)]
    inv_result = save_open_finance_investments(connection["id"], investments)

    # Caixinhas do OF viram caixinhas do Pig automaticamente (auto-create + dedup) e o
    # saldo do banco é espelhado nas vinculadas — mas SÓ pra planos pagos (Essencial+).
    # No Grátis (pós-trial) o OF nem sincroniza (conexão PAUSED barra acima); este gate é
    # a segunda trava: se o usuário caiu de plano, as caixinhas congelam (não atualizam).
    # Renda variável (ações/FIIs) é lida à parte no snapshot, também gated. Fail-soft.
    caixinha_result = {"caixinhas_created": 0, "caixinhas_linked": 0, "caixinhas_mirrored": 0}
    try:
        from core.services.plan_service import require_min_tier
        if require_min_tier(connection["user_id"], "essencial"):
            caixinha_result = sync_open_finance_caixinhas(connection["id"], connection["user_id"])
    except Exception as exc:
        print(f"[pluggy_sync] caixinha auto-import: {exc}")

    # Fase 1: conta BANK → launches (analytics, sem mover saldo); cartão → faturas (opção a).
    imported = import_open_finance_launches(connection["user_id"], connection["id"])
    imported_credit = import_open_finance_credit(connection["user_id"], connection["id"])
    # Propaga correções da Pluggy (transactions/updated) pros já importados (não deixa stale).
    updated = sync_imported_open_finance_updates(connection["user_id"], connection["id"])

    # Agentes do Piggy — gatilho pós-sync: Xerife roda só sobre o delta deste
    # usuário. IMPORTANTE: depois da reconciliação/import acima (merge-silencioso
    # primeiro, senão duplicata manual+OF viraria falso positivo). Fail-soft.
    if imported.get("inserted") or (imported_credit or {}).get("inserted"):
        try:
            from core.services.piggy_agents import run_agents_for_user
            run_agents_for_user(connection["user_id"], trigger="of_sync")
        except Exception as exc:  # nunca derruba o sync por causa de agente
            print(f"[pluggy_sync] agents hook: {exc}")

    return {
        "ok": True,
        "item_id": provider_item_id,
        "connection_id": connection["id"],
        "user_id": connection["user_id"],
        **result,
        **inv_result,
        **caixinha_result,
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


# Status de item Pluggy que significam "ainda buscando no banco" — enquanto o item
# está nesses estados, os dados novos ainda não chegaram, então esperamos antes de sync.
_PLUGGY_ITEM_UPDATING = {"UPDATING", "CREATED", "WAITING_USER_ACTION"}


def refresh_and_sync_pluggy_user(
    user_id: int,
    *,
    wait_seconds: int | None = None,
    poll_interval: float | None = None,
) -> dict:
    """Refresh sob demanda: pede pra Pluggy re-buscar do banco (PATCH /items),
    espera a atualização concluir e então sincroniza. É o que o botão "Atualizar"
    do Open Finance chama — puxa transação nova, marca a conexão ACTIVE e limpa
    o status "Pendente".

    Por que não é só sincronizar: sem webhook (ambiente de dev), o PATCH é
    ASSÍNCRONO na Pluggy — ela re-busca do banco e só depois os dados novos ficam
    disponíveis. Se sincronizássemos na hora, leríamos o snapshot antigo (a
    transação que acabou de acontecer não estaria lá). Por isso esperamos o item
    sair de UPDATING (com teto de `wait_seconds`) antes de sincronizar.

    Em produção o webhook `transactions/updated` já dispara o sync sozinho; este
    fluxo é o fallback manual e cobre o dev.

    `wait_seconds` fica curto de propósito (teto abaixo do timeout do proxy) — se a
    Pluggy demorar mais, sincronizamos o que já tem e devolvemos still_updating>0 pro
    front pedir pra tocar "Atualizar" de novo. Configurável via OF_REFRESH_WAIT_SEC.
    """
    import os

    if wait_seconds is None:
        try:
            wait_seconds = int(os.getenv("OF_REFRESH_WAIT_SEC", "18"))
        except ValueError:
            wait_seconds = 18
    if poll_interval is None:
        try:
            poll_interval = float(os.getenv("OF_REFRESH_POLL_SEC", "2.5"))
        except ValueError:
            poll_interval = 2.5

    snapshot = get_open_finance_snapshot(user_id)
    items = [
        c["provider_item_id"]
        for c in snapshot.get("connections", [])
        if (c.get("provider") == "pluggy" and c.get("provider_item_id"))
    ]
    if not items:
        # Sem banco Pluggy (ex.: só conexão mock): nada a refrescar, só sincroniza.
        result = sync_pluggy_user(user_id)
        return {"ok": True, "refreshed": 0, "waited": False, "still_updating": 0, **result}

    api_key = create_pluggy_api_key()

    # 1. Dispara o refresh na Pluggy pra cada item (falha por item não trava o resto).
    triggered = 0
    for item_id in items:
        try:
            update_pluggy_item(item_id, api_key)
            triggered += 1
        except Exception:
            pass

    # 2. Espera os itens saírem de UPDATING (Pluggy terminou de re-buscar do banco).
    #    time.sleep aqui é ok: a rota chama isto via asyncio.to_thread (fora do loop).
    deadline = time.monotonic() + max(0, wait_seconds)
    pending = set(items)
    while pending and time.monotonic() < deadline:
        time.sleep(poll_interval)
        for item_id in list(pending):
            try:
                item = get_pluggy_item(item_id, api_key)
            except Exception:
                pending.discard(item_id)  # erro de leitura não trava o fluxo
                continue
            status = str(item.get("status") or "").upper()
            if status not in _PLUGGY_ITEM_UPDATING:
                pending.discard(item_id)

    # 3. Sincroniza (idempotente): puxa contas/transações novas e marca ACTIVE.
    result = sync_pluggy_user(user_id)
    return {
        "ok": True,
        "refreshed": triggered,
        "waited": True,
        "still_updating": len(pending),
        **result,
    }
