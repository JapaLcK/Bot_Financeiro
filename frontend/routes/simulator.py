"""Simulador de decisão financeira (Pro) — `POST /simulator/{user_id}`.

Sem tela ainda: consumido pela tool de IA `simulate_purchase` e por um card
futuro. Validação única em `core.services.decision_simulator.Simulacao`.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from frontend.routes import shared

router = APIRouter()


@router.post("/simulator/{user_id}")
async def simulator_route(request: Request, user_id: int):
    shared.authorize_dashboard_access(request, user_id)
    from core.services.plan_service import plan_gate_ok
    if not await asyncio.to_thread(plan_gate_ok, user_id, "simulator"):
        raise HTTPException(status_code=403, detail={"error": "pro_required", "feature": "simulator"})

    from core.services.decision_simulator import Simulacao, simulate
    # O corpo só é lido depois da sessão e do gate: sem acesso, a resposta é
    # 401/403 qualquer que seja o corpo, sem detalhe de validação.
    try:
        simulacao = Simulacao.model_validate(await request.json())
    except ValidationError as exc:  # antes do ValueError: ValidationError é subclasse dele
        # Só loc e msg: o `input` ecoaria o corpo (ver validation_exception_page_handler).
        erros = [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        raise HTTPException(status_code=400, detail={"error": "invalid_simulation", "errors": erros})
    except (ValueError, RecursionError):  # JSON/UTF-8 inválido, número de 5000 dígitos, aninhamento fundo
        raise HTTPException(status_code=400, detail={"error": "invalid_simulation", "errors": []})
    result = await asyncio.to_thread(simulate, user_id, simulacao)
    return {"ok": True, "simulacao": result}
