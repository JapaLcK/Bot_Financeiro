"""
frontend/routes/retomada.py — o link de retomada do checkout abandonado
(docs/plano-remarketing.md §3, PR 4). Inerte até haver um token emitido (PR 6).

O cookie `pb_retomada` (token próprio, 2 h, emitido no clique; `core/services/retomada.py`)
tem ESCOPO RESTRITO: só as rotas de compra o aceitam, pela dependência `comprador`; as outras
rotas continuam com o resolver de sessão delas e dão 401 só com ele. Não é sessão e nunca vira uma.

Quem vence, em `_comprador`: sessão válida vence; o cookie só é lido se o pedido NÃO traz
credencial de sessão nenhuma (cookie de acesso, cookie do painel ou `Authorization`);
credencial de sessão inválida continua 401, para o `auth-refresh.js` renovar. O refresh
cookie (path `/auth/refresh`) não viaja aqui e não conta (resíduo declarado, plano §3).
"""
import asyncio
import logging
from typing import NamedTuple
from urllib.parse import urlencode

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from core.services import retomada as tokens
from frontend.routes import shared

router = APIRouter()

COOKIE = "pb_retomada"


class Comprador(NamedTuple):
    user_id: int
    etapa: int | None = None   # None = sessão normal; 1..3 = retomada daquela etapa


def _cookie_lido(request: Request) -> tuple[int, int] | None:
    return tokens.ler_token(request.cookies.get(COOKIE), cookie=True)


def traz_sessao(request: Request) -> bool:
    """Credencial de sessão no pedido, válida ou não. Cookie pela PRESENÇA da chave (um
    duplicado vazio ainda é credencial no jar); `Authorization` por qualquer valor."""
    return bool((request.headers.get("authorization") or "").strip()) or any(
        c in request.cookies for c in (shared.AUTH_COOKIE_NAME, shared.DASHBOARD_COOKIE_NAME))


async def _comprador(request: Request, sessao) -> Comprador:
    if not traz_sessao(request):
        lido = _cookie_lido(request)
        if lido is not None:
            await asyncio.to_thread(shared.raise_if_account_scheduled_for_deletion, lido[0])
            return Comprador(*lido)
    # Sessão presente (válida ou 401 aqui dentro) ou nenhuma credencial: o resolver de
    # sempre decide, com o mesmo erro de sempre.
    return Comprador(await sessao(request))


async def _sessao_stripe(request: Request) -> int:
    from frontend.finance_bot_websocket_custom import _get_current_user  # noqa: PLC0415
    return await _get_current_user(request, None)


async def _sessao_painel(request: Request) -> int:
    return shared.resolve_dashboard_user_id(request)


async def comprador(request: Request) -> Comprador:
    """Rotas que hoje usam `_get_current_user`: create-checkout e bump."""
    return await _comprador(request, _sessao_stripe)


async def comprador_pix(request: Request) -> Comprador:
    """Rotas do Pix, que hoje usam `resolve_dashboard_user_id` (cookie do painel)."""
    return await _comprador(request, _sessao_painel)


def email_mascarado(request: Request) -> str | None:
    """Para o `plans-config`: só com cookie válido e sem sessão; nunca o e-mail inteiro."""
    from db.privacy import get_user_email, is_account_scheduled_for_deletion  # noqa: PLC0415

    lido = None if traz_sessao(request) else _cookie_lido(request)
    if not lido:
        return None
    try:   # a máscara é enfeite: falhar aqui não derruba a config pública
        if is_account_scheduled_for_deletion(lido[0]):   # o checkout daria 403
            return None
        email = get_user_email(lido[0])
    except Exception as exc:
        logging.getLogger(__name__).warning("retomada_mascara_falhou: %s", type(exc).__name__)
        return None
    return tokens.mascarar_email(email) if email else None


def _precos(request: Request) -> str:
    """`/precos` repassando só os `utm_*`: o token NUNCA chega à URL da /precos."""
    utm = [(k, v[:200]) for k, v in request.query_params.multi_items() if k.startswith("utm_")]
    return "/precos?" + urlencode(utm) if utm else "/precos"


def _ir(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303, headers={"Cache-Control": "no-store"})


@router.get("/retomar")
async def retomar(request: Request, t: str = ""):
    """Sem HTML: só 303. Os quatro destinos estão no plano §3."""
    from db.privacy import get_user_email, is_account_scheduled_for_deletion  # noqa: PLC0415
    from db.remarketing import ja_comprou, marcar_clique  # noqa: PLC0415

    lido = tokens.ler_token(t)
    if lido is None:
        return _ir(_precos(request))
    uid, etapa = lido
    try:
        if (not await asyncio.to_thread(get_user_email, uid)
                or await asyncio.to_thread(is_account_scheduled_for_deletion, uid)):
            return _ir(_precos(request))
        if await asyncio.to_thread(ja_comprou, uid):
            return _ir("/login")
    except Exception as exc:   # fail closed: sem cookie e sem clique
        logging.getLogger(__name__).warning("retomada_leitura_falhou: %s", type(exc).__name__)
        return _ir(_precos(request))
    if traz_sessao(request):   # a sessão é de alguém: não se troca de comprador por link
        return _ir(_precos(request))
    try:
        await asyncio.to_thread(marcar_clique, uid, etapa)
    except Exception as exc:   # métrica: não segura a compra
        logging.getLogger(__name__).warning("retomada_clique_falhou: %s", type(exc).__name__)
    from frontend.finance_bot_websocket_custom import COOKIE_SECURE  # noqa: PLC0415

    resp = _ir(_precos(request))
    # Token PRÓPRIO do cookie (rótulo e validade de 2 h no servidor): o do link não serve aqui.
    resp.set_cookie(COOKIE, tokens.emitir_token(uid, etapa, cookie=True),
                    max_age=tokens.COOKIE_MAX_AGE, path="/", httponly=True,
                    secure=COOKIE_SECURE, samesite="strict")
    return resp


@router.get("/retomar/pronto")
async def retomar_pronto():
    # Provisório e inerte: a página de "Pagamento recebido" é do PR 5, que substitui esta
    # rota. Até lá ninguém chega aqui (nenhum token foi emitido).
    return _ir("/login")


@router.post("/billing/retomada/senha")
@shared.limiter.limit("3/hour")
async def retomada_senha(request: Request, background_tasks: BackgroundTasks):
    """"Enviar link para criar sua senha", pelo cookie e SEM sessão. A resposta é a mesma
    com e sem credencial na conta, e as duas leituras acontecem sempre (nada de oráculo
    de tempo). Conta com senha não recebe nada."""
    from db.google_auth import conta_sem_credencial  # noqa: PLC0415
    from db.privacy import get_user_email  # noqa: PLC0415
    from frontend.finance_bot_websocket_custom import (  # noqa: PLC0415
        COOKIE_SECURE, _reset_de_senha_em_segundo_plano,
    )

    lido = None if traz_sessao(request) else _cookie_lido(request)
    if lido is None:
        raise HTTPException(status_code=401, detail="Link de compra inválido ou expirado.")
    uid = lido[0]
    await asyncio.to_thread(shared.raise_if_account_scheduled_for_deletion, uid)
    sem_credencial = await asyncio.to_thread(conta_sem_credencial, uid)
    email = await asyncio.to_thread(get_user_email, uid)
    if sem_credencial and email:
        background_tasks.add_task(_reset_de_senha_em_segundo_plano, email)
    resp = JSONResponse({"message": "Se a conta precisar, o link para criar a senha vai para o e-mail dela."},
                        headers={"Cache-Control": "no-store"})
    resp.delete_cookie(COOKIE, path="/", httponly=True, secure=COOKIE_SECURE, samesite="strict")
    return resp
