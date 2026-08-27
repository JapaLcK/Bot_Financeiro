"""Estado do wizard de primeira configuração servido em /onboarding.

Duas responsabilidades: guardar em que passo o usuário parou (pra retomar em vez
de recomeçar quando ele fecha, recarrega ou troca de aparelho) e carimbar a
conclusão, que é o que desliga o `gate_onboarding`.

Auth por IDENTIDADE, não por assinatura: usa `resolve_dashboard_user_id` e NÃO
`authorize_dashboard_access`, pelo mesmo motivo do `routes/push.py`. O segundo
aplicaria `_enforce_subscription_gate`, que devolve 402 pra quem está sem
`has_app_access` — e aí a pessoa não conseguiria nem concluir nem pular o wizard,
ficando presa no gate que o próprio wizard deveria abrir.

O `user_id` NUNCA vem do cliente: não há `{user_id}` no caminho nem id no corpo.
Ele sai só do token de sessão, que o `resolve_dashboard_user_id` valida contra
`auth_sessions` pelo `jti` (401 se a sessão foi revogada ou expirou).
"""
import asyncio

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from db import (
    get_onboarding_state,
    mark_onboarding_completed,
    set_onboarding_step,
)
from frontend.routes import shared

router = APIRouter()

# Passos do wizard: 1 boas-vindas · 2 dinheiro · 3 WhatsApp · 4 resumo · 5 pronto
TOTAL_STEPS = 5

# Telemetria: eventos aceitos do cliente → event_type em system_event_logs.
# Abandono NÃO é evento próprio — sai por query (último step_view sem
# onboarding_completed), então não há nada pro cliente mandar quando ele some.
_EVENT_TYPES = {
    "view": "onboarding_step_view",
    "skip": "onboarding_step_skip",
}


class OnboardingStatePayload(BaseModel):
    step: int | None = None
    completed: bool | None = None
    event: str | None = None


@router.get("/onboarding/state")
async def onboarding_state_route(request: Request):
    user_id = shared.resolve_dashboard_user_id(request)
    state = await asyncio.to_thread(get_onboarding_state, user_id)
    return {**state, "total_steps": TOTAL_STEPS}


@router.post("/onboarding/state")
@shared.limiter.limit("120/hour")
async def update_onboarding_state_route(request: Request, payload: OnboardingStatePayload):
    user_id = shared.resolve_dashboard_user_id(request)

    # Valida TUDO antes de escrever qualquer coisa: senão um `event` inválido
    # sairia com 400 depois de já ter gravado o passo, e o cliente não teria
    # como saber que metade do pedido passou.
    if payload.step is not None and not 0 <= int(payload.step) <= TOTAL_STEPS:
        raise HTTPException(status_code=400, detail="Passo inválido.")
    if payload.event is not None:
        if payload.event not in _EVENT_TYPES:
            raise HTTPException(status_code=400, detail="Evento inválido.")
        if payload.step is None:
            raise HTTPException(status_code=400, detail="Evento exige o passo.")

    if payload.step is not None:
        await asyncio.to_thread(set_onboarding_step, user_id, int(payload.step))

    if payload.completed:
        await asyncio.to_thread(mark_onboarding_completed, user_id)

    # Telemetria por último e sem poder derrubar a escrita acima: perder um
    # evento de funil é barato, perder o progresso do usuário não é.
    # `log_system_event` já engole a própria exceção; o try é pro import.
    try:
        from core.admin_dashboard import log_system_event

        if payload.event is not None:
            await log_system_event(
                "info",
                _EVENT_TYPES[payload.event],
                f"onboarding passo {payload.step}",
                source="onboarding",
                user_id=user_id,
                details={"step": int(payload.step)},
            )
        if payload.completed:
            await log_system_event(
                "info",
                "onboarding_completed",
                "onboarding concluído",
                source="onboarding",
                user_id=user_id,
                details={"last_step": payload.step},
            )
    except Exception:
        pass

    state = await asyncio.to_thread(get_onboarding_state, user_id)
    return {**state, "total_steps": TOTAL_STEPS}
