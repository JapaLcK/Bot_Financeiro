"""`GET /api/v2/me`: o que o v2 precisa saber do usuário da sessão. Sem PII e sem id."""
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.v2.sessao import usuario_atual
from core.services.plan_service import get_plan_tier

router = APIRouter()


class Me(BaseModel):
    plan_tier: Literal["free", "essencial", "plus", "pro"]


@router.get("/me", response_model=Me)
def me(uid: int = Depends(usuario_atual)) -> Me:
    return Me(plan_tier=get_plan_tier(uid))
