"""Saque/depósito em espécie do Open Finance (Q41): a lista de perguntas e avisos
e as respostas do painel. As regras moram em db/open_finance_cash_answers.py.

Sem `OF_CASH_ENABLED` de propósito: o switch só impede vínculo NOVO, e o que já
existe tem de continuar respondível e desfazível com ele desligado.
"""
import asyncio
from typing import Literal

from fastapi import APIRouter, HTTPException, Request

from db.open_finance_cash_answers import answer_link, list_pending, undo_link
from frontend.routes import shared

router = APIRouter()

MANUAL_MUDOU = ("O lançamento que você anotou mudou e não bate mais com este. "
                "Se forem diferentes, toque em “São diferentes”.")


# `shared_limit(scope=)` e não `limit()`: com o `key_style="url"` do slowapi cada
# `link_id`/`action` abriria um balde próprio e o teto seria decorativo.
@router.get("/open-finance/{user_id}/cash-transfers")
@shared.limiter.shared_limit("60/minute", scope="cash_transfers_list")
async def cash_transfers_route(request: Request, user_id: int):
    shared.authorize_dashboard_access(request, user_id)
    return {"ok": True, "items": await asyncio.to_thread(list_pending, user_id)}


@router.post("/open-finance/{user_id}/cash-transfers/{link_id}/{action}")
@shared.limiter.shared_limit("30/minute", scope="cash_transfers_action")
async def cash_transfer_action_route(
    request: Request, user_id: int, link_id: int,
    action: Literal["undo", "seen", "same", "different", "already", "credit", "cash", "not_cash"],
):
    shared.authorize_dashboard_access(request, user_id)
    try:
        if action == "undo":
            result = await asyncio.to_thread(undo_link, user_id, link_id)
        else:
            result = await asyncio.to_thread(answer_link, user_id, link_id, action)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Aviso não encontrado.") from exc
    if result.get("reason") == "MANUAL_NOT_AVAILABLE":
        raise HTTPException(status_code=409, detail=MANUAL_MUDOU)
    shared.invalidate_dashboard_current_cache(user_id)
    return {"ok": True, "changed": result["changed"]}
