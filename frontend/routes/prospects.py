"""Rotas do funil de prospecção (lead engine do Instagram).

Público:  GET /i/{code}              → seta cookie prospect_code (30d) e manda pra landing.
Serviço:  POST /api/prospect/status  → status dos códigos (sem PII), autenticado
          por X-Prospect-Key = env PROSPECT_API_KEY (secrets.compare_digest).

Espelho de frontend/routes/affiliates.py, sem lookup no banco no /i/{code}:
o código não é pré-registrado (o lead engine o gera), então não há o que
consultar — e não consultar também não vaza a existência de código.
"""
import asyncio
import os
import secrets

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from db.prospects import (
    PROSPECT_COOKIE_MAX_AGE_DAYS,
    is_valid_prospect_code,
    list_prospect_status,
)
from frontend.routes import shared

router = APIRouter()

PROSPECT_COOKIE_NAME = "prospect_code"
_STATUS_CODES_CAP = 500


@router.get("/i/{code}")
async def prospect_link(code: str):
    """Link de prospecção. Código bem-formado → cookie de atribuição;
    malformado → só redireciona (sem cookie)."""
    response = RedirectResponse(url="/", status_code=302)
    if is_valid_prospect_code(code):
        # COOKIE_SECURE do app inclui a blindagem APP_ENV=prod (Secure mesmo
        # com DASHBOARD_URL http por engano). Import tardio porque o monólito
        # importa este router antes de definir a constante (precedente:
        # open_finance.py importa `manager` do mesmo jeito).
        from frontend.finance_bot_websocket_custom import COOKIE_SECURE
        response.set_cookie(
            PROSPECT_COOKIE_NAME,
            code,
            max_age=PROSPECT_COOKIE_MAX_AGE_DAYS * 24 * 3600,
            httponly=True,
            secure=COOKIE_SECURE,
            samesite="lax",
        )
    return response


class ProspectStatusBody(BaseModel):
    codes: list[str]


@router.post("/api/prospect/status")
@shared.limiter.limit("60/minute")
async def prospect_status(request: Request, body: ProspectStatusBody):
    """Consulta do lead engine: quais códigos viraram cadastro e se a conta
    está ativa (pagante ou trial). Resposta sem PII por contrato.

    Code repetido → 1 entrada, do PRIMEIRO cadastro (list_prospect_status).
    Máximo 500 codes por chamada; excedente é ignorado — o consumidor pagina.
    """
    expected = (os.getenv("PROSPECT_API_KEY") or "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="Serviço não configurado.")
    provided = request.headers.get("X-Prospect-Key") or ""
    # compare em bytes: compare_digest com str levanta TypeError se o header
    # vier não-ASCII (viraria 500 sem autenticação; tem de ser 401).
    if not secrets.compare_digest(
        provided.encode("utf-8", "replace"), expected.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="Chave inválida.")

    codes = (body.codes or [])[:_STATUS_CODES_CAP]
    referrals = await asyncio.to_thread(list_prospect_status, codes)
    return {"referrals": referrals}
