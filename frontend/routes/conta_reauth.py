"""POST /auth/account/reauth-code — manda por e-mail o código que autoriza excluir a conta.

Sem corpo e sem e-mail no pedido: o destino é SEMPRE o e-mail da conta da sessão
(nada a enumerar, e o 409 só diz à própria dona o que o /auth/me já diz). O
`user_id` vem só da sessão. O código nunca vai na resposta nem em log.
"""
import asyncio

from fastapi import APIRouter, HTTPException, Request

from core.services.retomada import mascarar_email
from db import get_user_email, reauth_codes
from db.privacy import PASSWORD_NOT_SET_MSG
from frontend.routes import shared

router = APIRouter()


@router.post("/auth/account/reauth-code")
@shared.limiter.limit("5/hour")
async def enviar_codigo_de_reautenticacao(request: Request):
    user_id = shared.resolve_dashboard_user_id(request)
    await asyncio.to_thread(shared.raise_if_account_scheduled_for_deletion, user_id)
    modo = await asyncio.to_thread(reauth_codes.modo, user_id)
    if modo is None:
        raise HTTPException(status_code=404, detail="Conta de login não encontrada.")
    if modo == "sem_credencial":   # e-mail não provado: não se manda código a ele
        raise HTTPException(status_code=409, detail=PASSWORD_NOT_SET_MSG)
    from frontend.finance_bot_websocket_custom import _check_persistent_rate_limit
    await _check_persistent_rate_limit("account-reauth-code", f"user:{user_id}", 3, 3600)
    email = await asyncio.to_thread(get_user_email, user_id)
    if not email:
        raise HTTPException(status_code=400, detail="E-mail não cadastrado para esta conta.")
    code = await asyncio.to_thread(reauth_codes.emitir, user_id, reauth_codes.EXCLUIR)
    from core.services.email_service import send_account_reauth_code_email
    if not await asyncio.to_thread(send_account_reauth_code_email, email, code):
        raise HTTPException(
            status_code=500,
            detail="Não foi possível enviar o código. Tente de novo em alguns minutos.",
        )
    return {"status": "sent", "expires_in_minutes": reauth_codes.MINUTOS,
            "email_mascarado": mascarar_email(email)}
