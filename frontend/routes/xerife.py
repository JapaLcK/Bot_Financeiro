"""Controles e regras recorrentes do Xerife (PL-04)."""
from __future__ import annotations

import asyncio
from fastapi import APIRouter, HTTPException, Request
from core.services.xerife_config import XerifeConfig, RegraEsperado, config_publica
from frontend.routes import shared

router = APIRouter()


async def _xerife_access(request: Request, user_id: int) -> None:
    from frontend.routes.agents import _require_agents_beta, _v2_agents_gate
    shared.authorize_dashboard_access(request, user_id)
    _require_agents_beta(user_id)
    v2_on, allowed = await asyncio.to_thread(_v2_agents_gate, user_id, "xerife")
    if v2_on and not allowed:
        raise HTTPException(status_code=403, detail={"error": "pro_required", "feature": "agents"})


@router.patch("/agents/{user_id}/xerife/config")
async def xerife_config_route(request: Request, user_id: int, body: XerifeConfig):
    await _xerife_access(request, user_id)
    from db.anomalias import atualizar_config_xerife
    config = await asyncio.to_thread(atualizar_config_xerife, user_id, body.model_dump(exclude_unset=True))
    if config is None:
        raise HTTPException(status_code=404, detail="Ative o Xerife antes de configurar.")
    return {"ok": True, "config": config_publica(config)}


@router.get("/agents/{user_id}/xerife/esperados")
async def xerife_esperados_route(request: Request, user_id: int, limit: int = 50, offset: int = 0):
    await _xerife_access(request, user_id)
    from db.anomalias import listar_esperados_xerife
    result = await asyncio.to_thread(listar_esperados_xerife, user_id, max(1, min(limit, 50)), max(0, offset))
    if result is None:
        raise HTTPException(status_code=404, detail="Ative o Xerife antes de configurar.")
    return {"ok": True, **result}


@router.post("/agents/{user_id}/xerife/regras")
async def xerife_regra_route(request: Request, user_id: int, body: RegraEsperado):
    await _xerife_access(request, user_id)
    from db.anomalias import alterar_regra_xerife
    try:
        regra = await asyncio.to_thread(alterar_regra_xerife, user_id, body.model_dump(mode="json"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if regra is None:
        raise HTTPException(status_code=404, detail="Ative o Xerife antes de criar uma regra.")
    return {"ok": True, "regra": regra}


@router.delete("/agents/{user_id}/xerife/regras/{regra_id}")
async def xerife_desfazer_regra_route(request: Request, user_id: int, regra_id: str):
    await _xerife_access(request, user_id)
    from db.anomalias import alterar_regra_xerife
    regra = await asyncio.to_thread(alterar_regra_xerife, user_id, regra_id=regra_id)
    if regra is None:
        raise HTTPException(status_code=404, detail="Regra não encontrada.")
    return {"ok": True}
