"""
frontend/routes/billing_bump.py — `POST /billing/checkout/bump`, o order bump
da página própria: troca os cadernos do carrinho de uma sessão `elements` aberta.

O corpo é o CONJUNTO desejado inteiro (`[]` = nenhum), em POSIÇÕES da foto. O
preço sai da foto gravada na criação da sessão, nunca do cliente, e a metadata
não é tocada: entrega e fatura leem a foto e o carrinho real
(`core/services/extras_assinar.py`). Sessão de outra conta, de outro modo ou de
outra origem é 404 indistinguível de "não existe", sem `modify`.

A rota não olha a flag `CHECKOUT_PAGINA_PROPRIA`: com ela desligada não nasce
sessão `elements`, e a página já aberta segue pagável.
"""

# Sem `from __future__ import annotations`: o FastAPI resolve o corpo pela
# anotação (ver o mesmo aviso em `billing_pix.py`).
import asyncio
import re
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, StrictInt

from core.services.extras_assinar import SLOTS, _recusa, da_metadata, linhas_do_bump
from frontend.routes import shared

router = APIRouter()

_SID = re.compile(r"cs_(test|live)_[A-Za-z0-9]{1,200}")
_ORIGENS = ("assinar", "precos")


class BumpBody(BaseModel):
    # `extra="forbid"`: um `price` no corpo é 422, nunca ignorado em silêncio.
    model_config = ConfigDict(extra="forbid")
    sid: str
    posicoes: list[StrictInt]


def _nao_achou() -> HTTPException:
    return HTTPException(status_code=404, detail="Sessão de pagamento não encontrada.")


def _bump(stripe, sg, user_id: int, customer: str | None, sid: str, posicoes: list[int]) -> dict:
    """Roda sob `_billing_user_lock`: dois pedidos do mesmo usuário não leem o
    mesmo carrinho para escrever por cima um do outro (o último vence)."""
    try:
        session = stripe.checkout.Session.retrieve(sid)
    except stripe.error.InvalidRequestError:
        raise _nao_achou()
    meta = sg(session, "metadata", {})
    # Isolamento: as quatro condições, e a mesma resposta para qualquer uma.
    if not (customer and sg(session, "customer") == customer
            and str(sg(meta, "finbot_user_id", "")) == str(user_id)
            and sg(session, "ui_mode") == "elements" and sg(meta, "origem") in _ORIGENS):
        raise _nao_achou()
    # `open` com `expires_at` vencido: o Stripe ainda não varreu, mas o modify
    # seria recusado e viraria um `ebook_oferta_recusada` falso. Só DEPOIS do
    # dono: antes dele, o 409 diria o estado de uma sessão alheia.
    if (sg(session, "status") != "open"
            or (sg(session, "expires_at") or float("inf")) <= time.time()):
        raise HTTPException(status_code=409, detail={"error": "sessao_fechada"})
    foto = [preco for preco, _ in da_metadata(meta)]
    if any(p > len(foto) for p in posicoes):
        raise HTTPException(status_code=400, detail="Posição fora da oferta.")
    desejados = [foto[p - 1] for p in posicoes]
    atuais = sg(stripe.checkout.Session.list_line_items(sid, limit=100), "data", [])
    linhas = linhas_do_bump(atuais, foto, desejados)
    if linhas is not None:
        try:
            stripe.checkout.Session.modify(sid, line_items=linhas)
        except stripe.error.InvalidRequestError as exc:
            _recusa(user_id, "Stripe recusou o carrinho do order bump.", desejados, exc)
            raise HTTPException(status_code=409, detail={"error": "extra_recusado"}) from exc
    return {"ok": True}


@router.post("/billing/checkout/bump")
@shared.limiter.limit("120/hour")   # alto: no Instagram muita gente sai do mesmo IP
async def billing_checkout_bump(request: Request, payload: BumpBody):
    # Import tardio, como o `billing_pix.py`: o monólito importa este módulo.
    from frontend.finance_bot_websocket_custom import (  # noqa: PLC0415
        STRIPE_SECRET_KEY, _billing_user_lock, _get_current_user, _sg,
    )
    from db import get_auth_user  # noqa: PLC0415

    user_id = await _get_current_user(request, None)
    posicoes = payload.posicoes
    if (not _SID.fullmatch(payload.sid) or len(posicoes) > SLOTS
            or len(set(posicoes)) != len(posicoes) or any(not 1 <= p <= SLOTS for p in posicoes)):
        raise HTTPException(status_code=400, detail="Pedido inválido.")
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Pagamentos ainda não configurados.")

    import stripe  # noqa: PLC0415
    stripe.api_key = STRIPE_SECRET_KEY
    user = await asyncio.to_thread(get_auth_user, user_id) or {}
    try:
        async with _billing_user_lock(user_id):
            return await asyncio.to_thread(_bump, stripe, _sg, user_id,
                                           user.get("stripe_customer_id"), payload.sid, posicoes)
    except stripe.error.StripeError as exc:
        raise HTTPException(status_code=502, detail="O Stripe não respondeu. Tente de novo.") from exc
