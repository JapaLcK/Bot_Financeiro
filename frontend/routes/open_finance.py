"""Rotas de Open Finance (Pluggy + mock) — conexão, snapshot e webhook.

Etapa 4 do refactor Fase 1 (docs/refactor_plan.md): movidas de
finance_bot_websocket_custom.py sem mudança de comportamento.

O webhook /open-finance/pluggy/webhook está em CSRF_EXEMPT_PATHS no app —
o middleware CSRF compara o path da request, então a isenção segue valendo
com a rota registrada via router.
"""

import asyncio
import hashlib
import hmac
import json
import os

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core.admin_dashboard import log_system_event
from core.audit import AuditEvent, record_audit_event
from core.services.pluggy import (
    PluggyApiError,
    PluggyConfigError,
    create_pluggy_connect_token,
)
from core.services.pluggy_sync import sync_pluggy_item, sync_pluggy_user
from db import (
    create_mock_open_finance_connection,
    disconnect_open_finance_connection,
    get_open_finance_snapshot,
    save_pluggy_open_finance_item,
    update_pluggy_open_finance_item_status,
)
from frontend.routes import shared

router = APIRouter()

PLUGGY_INCLUDE_SANDBOX = os.getenv("PLUGGY_INCLUDE_SANDBOX", "1") != "0"

# Eventos da Pluggy que devem disparar um sync de contas/transações.
PLUGGY_SYNC_EVENTS = {
    "item/created",
    "item/updated",
    "transactions/created",
    "transactions/updated",
    "transactions/deleted",
}


async def _run_pluggy_sync_bg(item_id: str) -> None:
    """Roda o sync fora do request (fire-and-forget), logando falhas."""
    try:
        result = await asyncio.to_thread(sync_pluggy_item, item_id)
        await log_system_event(
            "info",
            "pluggy_sync_done",
            f"Sync Pluggy concluído: {item_id}",
            source="open_finance",
            details=result,
        )
    except Exception as exc:  # noqa: BLE001 — background, não pode derrubar nada
        await log_system_event(
            "error",
            "pluggy_sync_failed",
            f"Sync Pluggy falhou: {item_id}: {exc}",
            source="open_finance",
            details={"item_id": item_id, "error": str(exc)},
        )


def _schedule_pluggy_sync(item_id: str) -> None:
    if item_id:
        asyncio.create_task(_run_pluggy_sync_bg(item_id), name=f"pluggy_sync_{item_id}")


class OpenFinanceMockConnectPayload(BaseModel):
    institution: str | None = None


class OpenFinancePluggyItemPayload(BaseModel):
    item: dict


@router.get("/open-finance/{user_id}")
async def open_finance_snapshot_route(request: Request, user_id: int):
    shared.authorize_dashboard_access(request, user_id)
    snapshot = await asyncio.to_thread(get_open_finance_snapshot, user_id)
    return json.loads(shared.jdump({"ok": True, **snapshot}))


@router.post("/open-finance/{user_id}/connect-token")
async def open_finance_connect_token_route(request: Request, user_id: int):
    shared.authorize_dashboard_access(request, user_id)

    webhook_url = (os.getenv("PLUGGY_WEBHOOK_URL") or "").strip()
    if not webhook_url and shared.DASHBOARD_URL.startswith("https://"):
        webhook_url = f"{shared.DASHBOARD_URL}/open-finance/pluggy/webhook"

    try:
        token_data = await asyncio.to_thread(
            create_pluggy_connect_token,
            user_id,
            webhook_url or None,
        )
    except PluggyConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PluggyApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "ok": True,
        "accessToken": token_data["accessToken"],
        "includeSandbox": PLUGGY_INCLUDE_SANDBOX,
        "provider": "pluggy",
    }


@router.post("/open-finance/{user_id}/pluggy-item")
async def open_finance_pluggy_item_route(request: Request, user_id: int, payload: OpenFinancePluggyItemPayload):
    shared.authorize_dashboard_access(request, user_id)
    try:
        connection = await asyncio.to_thread(save_pluggy_open_finance_item, user_id, payload.item)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await asyncio.to_thread(
        record_audit_event,
        user_id,
        AuditEvent.OPEN_FINANCE_CONNECTED,
        request=request,
        details={"provider": "pluggy", "item_id": (connection or {}).get("provider_item_id")},
    )

    # Sync inicial: puxa contas + transações do banco recém-conectado.
    _schedule_pluggy_sync(str((connection or {}).get("provider_item_id") or ""))

    snapshot = await asyncio.to_thread(get_open_finance_snapshot, user_id)
    return json.loads(shared.jdump({"ok": True, "connection": connection, **snapshot}))


@router.post("/open-finance/{user_id}/sync")
async def open_finance_sync_route(request: Request, user_id: int):
    """Força um sync de todos os bancos Pluggy do usuário (leitura sob demanda)."""
    shared.authorize_dashboard_access(request, user_id)
    try:
        result = await asyncio.to_thread(sync_pluggy_user, user_id)
    except PluggyConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PluggyApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    snapshot = await asyncio.to_thread(get_open_finance_snapshot, user_id)
    return json.loads(shared.jdump({"ok": True, "sync": result, **snapshot}))


def _verify_pluggy_webhook_signature(raw_body: bytes, signature_header: str, secret: str) -> bool:
    signature = (signature_header or "").strip()
    if signature.startswith("sha256="):
        signature = signature.split("=", 1)[1]
    if not signature:
        return False

    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


@router.post("/open-finance/pluggy/webhook")
async def open_finance_pluggy_webhook(request: Request):
    """
    Recebe eventos da Pluggy e responde rapido.
    Trabalho pesado de sync deve rodar fora do request.
    """
    secret = (os.getenv("PLUGGY_WEBHOOK_SECRET") or "").strip()
    if not secret:
        raise HTTPException(status_code=503, detail="Webhook não configurado.")

    raw_body = await request.body()
    received_sig = request.headers.get("X-Pluggy-Signature") or ""
    if not _verify_pluggy_webhook_signature(raw_body, received_sig, secret):
        raise HTTPException(status_code=401, detail="Assinatura inválida.")

    try:
        event = json.loads(raw_body)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Webhook inválido.") from exc

    event_name = str(event.get("event") or event.get("type") or "")
    item_id = str(event.get("itemId") or event.get("item_id") or event.get("item", {}).get("id") or "")
    status_by_event = {
        "item/created": "UPDATING",
        "item/updated": "ACTIVE",
        "item/error": "ERROR",
        "item/deleted": "DELETED",
    }
    status = status_by_event.get(event_name)
    if item_id and status:
        await asyncio.to_thread(update_pluggy_open_finance_item_status, item_id, status, event)

    # Dispara o sync pesado fora do request pros eventos que trazem dado novo.
    if item_id and event_name in PLUGGY_SYNC_EVENTS:
        _schedule_pluggy_sync(item_id)

    await log_system_event(
        "info" if event_name != "item/error" else "warning",
        "pluggy_webhook_received",
        f"Webhook Pluggy recebido: {event_name or 'evento desconhecido'}",
        source="open_finance",
        details={"event": event_name, "item_id": item_id},
    )
    return {"received": True}


@router.post("/open-finance/{user_id}/mock-connect")
async def open_finance_mock_connect_route(request: Request, user_id: int, payload: OpenFinanceMockConnectPayload):
    shared.authorize_dashboard_access(request, user_id)
    result = await asyncio.to_thread(
        create_mock_open_finance_connection,
        user_id,
        payload.institution or "nubank",
    )

    await asyncio.to_thread(
        record_audit_event,
        user_id,
        AuditEvent.OPEN_FINANCE_CONNECTED,
        request=request,
        details={"provider": "mock", "institution": payload.institution or "nubank"},
    )

    snapshot = await asyncio.to_thread(get_open_finance_snapshot, user_id)
    return json.loads(shared.jdump({"ok": True, "sync": result, **snapshot}))


@router.delete("/open-finance/{user_id}")
async def open_finance_disconnect_route(request: Request, user_id: int):
    shared.authorize_dashboard_access(request, user_id)
    deleted = await asyncio.to_thread(disconnect_open_finance_connection, user_id)

    if deleted:
        await asyncio.to_thread(
            record_audit_event,
            user_id,
            AuditEvent.OPEN_FINANCE_DISCONNECTED,
            request=request,
        )

    return {"ok": True, "deleted": deleted}
