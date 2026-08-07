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
    create_pluggy_api_key,
    create_pluggy_connect_token,
    delete_pluggy_item,
    list_pluggy_connectors,
)
from core.services.plan_service import bank_list_ui_enabled, is_pro
from core.services.pluggy_sync import (
    refresh_and_sync_pluggy_user,
    sync_pluggy_item,
    sync_pluggy_user,
)
from db import (
    count_open_finance_connections,
    create_mock_open_finance_connection,
    delete_open_finance_transactions,
    disconnect_open_finance_connection,
    get_open_finance_connection_by_item_id,
    get_open_finance_snapshot,
    list_pluggy_item_ids,
    save_pluggy_open_finance_item,
    update_pluggy_open_finance_item_status,
)
from frontend.routes import shared

router = APIRouter()

PLUGGY_INCLUDE_SANDBOX = os.getenv("PLUGGY_INCLUDE_SANDBOX", "1") != "0"

# Eventos da Pluggy que disparam um sync (puxar contas/transações).
# transactions/deleted é tratado à parte (remove ids), não re-sincroniza.
PLUGGY_SYNC_EVENTS = {
    "item/created",
    "item/updated",
    "transactions/created",
    "transactions/updated",
}


async def _run_pluggy_sync_bg(item_id: str) -> None:
    """Roda o sync fora do request (fire-and-forget), logando falhas."""
    try:
        result = await asyncio.to_thread(sync_pluggy_item, item_id)
        # Atualização ao vivo (PWA): avisa o cliente conectado pra recarregar saldo/timeline.
        uid = result.get("user_id") if isinstance(result, dict) else None
        if uid:
            try:
                from frontend.finance_bot_websocket_custom import manager
                await manager.broadcast_to_user(
                    int(uid), json.dumps({"type": "open_finance_synced", "item_id": item_id})
                )
            except Exception:
                pass
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


def _bank_limit_enabled() -> bool:
    # Gate dormante: só bloqueia quando ligado no ambiente (como os outros gates do projeto).
    return (os.getenv("OF_BANK_LIMIT_ENABLED") or "").strip().lower() in ("1", "true", "yes", "on")


async def _enforce_bank_limit(user_id: int, new_item_id: str | None = None) -> None:
    """Teto de conexões OF por plano.

    v2 (PLANS_V2_ENABLED): teto vem do tier — of_banks_max da escada
    (trial 1 / Essencial 1 / Plus 2 / Pro 5 / None = ilimitado). Ativo sempre
    que a escada estiver ligada, sem env extra.
    v1 (flag off): gate Fase 7 legado, dormante atrás de OF_BANK_LIMIT_ENABLED.

    P1: reconectar/renovar um banco JÁ conectado (mesmo provider_item_id) NÃO conta como
    banco novo — senão o usuário no limite ficava travado de reautorizar o próprio
    banco. Só bloqueia banco realmente novo.
    """
    from core.services.plan_service import plans_v2_enabled, get_user_limits

    if plans_v2_enabled():
        limit = (await asyncio.to_thread(get_user_limits, user_id)).get("of_banks_max")
        if limit is None:
            return  # ilimitado (Premium futuro)
        if new_item_id:
            existing = await asyncio.to_thread(get_open_finance_connection_by_item_id, str(new_item_id))
            if existing and int(existing.get("user_id")) == int(user_id):
                return  # upsert de item existente: reconexão, não é banco novo
        if limit <= 0:
            raise HTTPException(
                status_code=402,
                detail={
                    "code": "OF_BANK_LIMIT",
                    "limit": 0,
                    "message": "Conectar banco faz parte dos planos pagos — no Grátis a conexão "
                               "vale durante os 30 dias de teste. Assine pra reativar: /precos",
                },
            )
        count = await asyncio.to_thread(count_open_finance_connections, user_id)
        if count >= limit:
            raise HTTPException(
                status_code=402,
                detail={
                    "code": "OF_BANK_LIMIT",
                    "limit": limit,
                    "message": f"Seu plano conecta até {limit} banco{'s' if limit > 1 else ''}. "
                               "Faça upgrade pra conectar mais: /precos",
                },
            )
        return

    if not _bank_limit_enabled():
        return
    if await asyncio.to_thread(is_pro, user_id):
        return
    if new_item_id:
        existing = await asyncio.to_thread(get_open_finance_connection_by_item_id, str(new_item_id))
        if existing and int(existing.get("user_id")) == int(user_id):
            return  # upsert de item existente: reconexão, não é banco novo
    limit = int(os.getenv("OF_FREE_BANK_LIMIT", "1"))
    count = await asyncio.to_thread(count_open_finance_connections, user_id)
    if count >= limit:
        raise HTTPException(
            status_code=402,
            detail={
                "code": "OF_BANK_LIMIT",
                "limit": limit,
                "message": f"No plano grátis você conecta {limit} banco. Assine o Pro para conectar mais.",
            },
        )


class OpenFinanceMockConnectPayload(BaseModel):
    institution: str | None = None


class OpenFinancePluggyItemPayload(BaseModel):
    item: dict


@router.get("/open-finance/{user_id}")
async def open_finance_snapshot_route(request: Request, user_id: int):
    shared.authorize_dashboard_access(request, user_id)
    snapshot = await asyncio.to_thread(get_open_finance_snapshot, user_id)
    return json.loads(shared.jdump({"ok": True, **snapshot}))


# Só contas de pessoa física no modal (bate com o preview aprovado; evita os
# duplicados "… Empresas"). Pra incluir PJ, adicionar "BUSINESS_BANK".
_CONNECTABLE_TYPES = {"PERSONAL_BANK"}


@router.get("/open-finance/{user_id}/connectors")
async def open_finance_connectors_route(request: Request, user_id: int):
    """Catálogo completo de bancos da Pluggy pro modal 'Ver todos os bancos'.

    Gateado no beta (bank_list_ui_enabled): fora do allowlist devolve 403 e o front
    mantém a lista curta de sempre. Retorna dicts enxutos (id/name/type/color/inv)."""
    shared.authorize_dashboard_access(request, user_id)
    if not await asyncio.to_thread(bank_list_ui_enabled, user_id):
        raise HTTPException(status_code=403, detail="Recurso em beta.")
    try:
        raw = await asyncio.to_thread(
            list_pluggy_connectors, None, include_sandbox=PLUGGY_INCLUDE_SANDBOX
        )
    except PluggyConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PluggyApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    banks = []
    for c in raw:
        if str(c.get("type") or "") not in _CONNECTABLE_TYPES:
            continue
        products = [str(p).upper() for p in (c.get("products") or [])]
        banks.append({
            "id": c.get("id"),
            "name": (c.get("name") or "").strip(),
            "type": c.get("type"),
            "color": (c.get("primaryColor") or "").lstrip("#"),
            "logo": c.get("imageUrl") or "",
            "inv": "INVESTMENTS" in products,
        })
    banks.sort(key=lambda b: b["name"].lower())
    return {"ok": True, "connectors": banks}


@router.post("/open-finance/{user_id}/connect-token")
async def open_finance_connect_token_route(request: Request, user_id: int):
    shared.authorize_dashboard_access(request, user_id)
    # Não aplica o gate aqui: gerar um connect-token não conecta banco nenhum (e o widget
    # também é usado pra RECONECTAR um banco existente). O limite é cobrado no /pluggy-item,
    # onde já se sabe se o item é novo ou um upsert de um banco já conectado.

    webhook_url = (os.getenv("PLUGGY_WEBHOOK_URL") or "").strip()
    if not webhook_url and shared.DASHBOARD_URL.startswith("https://"):
        webhook_url = f"{shared.DASHBOARD_URL}/open-finance/pluggy/webhook"

    # Anexa o secret como token na URL (a Pluggy chama de volta preservando a query).
    # É como o webhook se autentica (a Pluggy não assina o corpo). Não duplica se já
    # veio com token (ex.: PLUGGY_WEBHOOK_URL setado à mão com o token).
    webhook_secret = (os.getenv("PLUGGY_WEBHOOK_SECRET") or "").strip()
    if webhook_url and webhook_secret and "token=" not in webhook_url:
        sep = "&" if "?" in webhook_url else "?"
        webhook_url = f"{webhook_url}{sep}token={webhook_secret}"

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
    item = payload.item if isinstance(payload.item, dict) else {}
    new_item_id = item.get("id") or item.get("itemId")
    await _enforce_bank_limit(user_id, str(new_item_id) if new_item_id else None)
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


@router.post("/open-finance/{user_id}/refresh")
async def open_finance_refresh_route(request: Request, user_id: int):
    """Refresh manual (botão "Atualizar"): pede pra Pluggy re-buscar do banco
    (PATCH /items), espera concluir e sincroniza — puxa transação nova, marca a
    conexão ACTIVE e limpa "Pendente". Difere do /sync, que só relê o que a Pluggy
    já tem sem forçar uma nova busca no banco.
    """
    shared.authorize_dashboard_access(request, user_id)
    try:
        result = await asyncio.to_thread(refresh_and_sync_pluggy_user, user_id)
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


# Headers de secret compartilhado aceitos (caso configurados no painel da Pluggy).
_PLUGGY_WEBHOOK_SECRET_HEADERS = ("x-webhook-token", "x-pluggy-token", "x-api-key")


def _authorize_pluggy_webhook(request: Request, raw_body: bytes, secret: str) -> bool:
    """Autentica o webhook da Pluggy.

    ⚠️ A Pluggy NÃO assina o corpo com HMAC (a doc dela só oferece IP fixo +
    header custom opcional). Então NÃO dá pra exigir `X-Pluggy-Signature` — isso
    rejeitava todo evento real com 401. Aceitamos um secret compartilhado que a
    GENTE controla ao registrar o webhook:

    1. token na URL (`?token=<secret>`) — a URL do webhook é registrada por nós, no
       connect-token e no painel; é o caminho principal e não depende de o painel
       suportar header custom.
    2. header com o secret (`X-Webhook-Token`/`X-Pluggy-Token`/`X-Api-Key`) — caso
       você prefira configurar um header no painel.
    3. assinatura HMAC (`X-Pluggy-Signature`) — mantida por compat/futuro; hoje a
       Pluggy não manda, mas se um dia mandar, continua valendo.

    Comparações em tempo constante. Sem secret configurado, o chamador já barra (503).
    """
    # 1. token na query string
    token = request.query_params.get("token") or ""
    if token and hmac.compare_digest(token, secret):
        return True
    # 2. header com o secret
    for header_name in _PLUGGY_WEBHOOK_SECRET_HEADERS:
        value = (request.headers.get(header_name) or "").strip()
        if value and hmac.compare_digest(value, secret):
            return True
    # 3. assinatura HMAC do corpo (compat)
    signature = request.headers.get("X-Pluggy-Signature") or ""
    if signature and _verify_pluggy_webhook_signature(raw_body, signature, secret):
        return True
    return False


@router.post("/open-finance/pluggy/webhook")
async def open_finance_pluggy_webhook(request: Request):
    """
    Recebe eventos da Pluggy e responde rapido.
    Trabalho pesado de sync deve rodar fora do request.

    Autenticação: secret compartilhado via token na URL / header (a Pluggy não
    assina o corpo com HMAC). Ver `_authorize_pluggy_webhook`.
    """
    secret = (os.getenv("PLUGGY_WEBHOOK_SECRET") or "").strip()
    if not secret:
        raise HTTPException(status_code=503, detail="Webhook não configurado.")

    raw_body = await request.body()
    if not _authorize_pluggy_webhook(request, raw_body, secret):
        raise HTTPException(status_code=401, detail="Não autorizado.")

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

    # transactions/deleted: a Pluggy manda os ids removidos — apaga direto, senão ficam
    # órfãos (um re-sync não os removeria, pois não voltam no list_transactions).
    if item_id and event_name == "transactions/deleted":
        deleted_ids = event.get("transactionIds") or event.get("transactionsIds") or []
        if isinstance(deleted_ids, list) and deleted_ids:
            await asyncio.to_thread(delete_open_finance_transactions, item_id, deleted_ids)
    # Demais eventos com dado novo: dispara o sync pesado fora do request.
    elif item_id and event_name in PLUGGY_SYNC_EVENTS:
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

    # Deleta os items na Pluggy ANTES do disconnect local (best-effort). Sem isso, remover
    # a conexão apagava só o nosso registro e o item ficava órfão na Pluggy, bloqueando a
    # reconexão ("já possui conexão com este acesso"). Falha por item não impede o
    # disconnect local (não pioramos o comportamento antigo).
    pluggy_item_ids = await asyncio.to_thread(list_pluggy_item_ids, user_id)
    if pluggy_item_ids:
        api_key = None
        try:
            api_key = await asyncio.to_thread(create_pluggy_api_key)
        except Exception as exc:  # noqa: BLE001 — best-effort; segue pro disconnect local
            await log_system_event(
                "warning", "pluggy_disconnect_auth_failed",
                f"Sem apiKey pra deletar items no disconnect do user {user_id}: {exc}",
                source="open_finance", details={"user_id": user_id, "error": str(exc)},
            )
        if api_key:
            for item_id in pluggy_item_ids:
                try:
                    await asyncio.to_thread(delete_pluggy_item, item_id, api_key)
                except Exception as exc:  # noqa: BLE001 — best-effort por item
                    await log_system_event(
                        "warning", "pluggy_item_delete_failed",
                        f"Falha ao deletar item {item_id} na Pluggy no disconnect: {exc}",
                        source="open_finance",
                        details={"item_id": item_id, "error": str(exc)},
                    )

    deleted = await asyncio.to_thread(disconnect_open_finance_connection, user_id)

    if deleted:
        await asyncio.to_thread(
            record_audit_event,
            user_id,
            AuditEvent.OPEN_FINANCE_DISCONNECTED,
            request=request,
        )

    return {"ok": True, "deleted": deleted}
