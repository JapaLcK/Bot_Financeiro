"""Rotas de push notification do app iOS — registro/baixa de device token.

O app (AppDelegate) obtém o device token do APNs nativamente e o entrega pro
WebView, que chama estes endpoints com os cookies de sessão. Assim o token fica
amarrado ao usuário logado sem o app precisar lidar com autenticação.

Push é liberado pra todos os planos — por isso o auth aqui usa
`resolve_dashboard_user_id` (identidade) e NÃO `authorize_dashboard_access`
(que aplicaria o gate de assinatura).
"""
import asyncio

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from db import register_push_token, delete_push_token
from frontend.routes import shared

router = APIRouter()

_VALID_ENVS = {"sandbox", "production"}
_VALID_PLATFORMS = {"ios", "android"}


class PushRegisterPayload(BaseModel):
    token: str
    platform: str | None = "ios"
    environment: str | None = "production"


class PushUnregisterPayload(BaseModel):
    token: str


@router.post("/api/push/register")
@shared.limiter.limit("30/minute")
async def push_register_route(request: Request, payload: PushRegisterPayload):
    user_id = shared.resolve_dashboard_user_id(request)
    token = (payload.token or "").strip()
    if not token or len(token) > 512:
        raise HTTPException(status_code=400, detail="Token inválido.")
    platform = (payload.platform or "ios").strip().lower()
    if platform not in _VALID_PLATFORMS:
        platform = "ios"
    environment = (payload.environment or "production").strip().lower()
    if environment not in _VALID_ENVS:
        environment = "production"
    await asyncio.to_thread(
        register_push_token, user_id, token, platform, environment
    )
    return {"ok": True}


@router.post("/api/push/unregister")
@shared.limiter.limit("30/minute")
async def push_unregister_route(request: Request, payload: PushUnregisterPayload):
    # Exige sessão válida, mas a baixa é por token (idempotente).
    shared.resolve_dashboard_user_id(request)
    token = (payload.token or "").strip()
    if token:
        await asyncio.to_thread(delete_push_token, token)
    return {"ok": True}
