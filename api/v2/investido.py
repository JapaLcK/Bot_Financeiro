"""`GET /api/v2/investido`: o total investido nos bancos conectados, por tipo e por banco
(regra em `db/investido.py`, a mesma da IA e do WhatsApp).

Dinheiro sai como TEXTO decimal com 2 casas; `null` = não dá para saber (≠ zero). Sem
`raw`, sem `provider_*_id`, sem id de conexão.
"""
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.v2.sessao import usuario_atual
from core.services.plan_service import plan_gate_ok
from db import investido

router = APIRouter()

MotivoInvestido = Literal[investido.MOTIVOS]
MotivoParte = Literal[investido.MOTIVOS_PARTE]


class ParteTipo(BaseModel):
    tipo: Literal[tuple(investido.TIPOS)]
    rotulo: str
    valor: Decimal | None
    motivos: list[MotivoParte]


class ParteBanco(BaseModel):
    banco: str
    valor: Decimal | None
    motivos: list[MotivoParte]


class Investido(BaseModel):
    total: Decimal | None
    por_tipo: list[ParteTipo]
    por_banco: list[ParteBanco]
    motivos: list[MotivoInvestido]


def _assinante(uid: int = Depends(usuario_atual)) -> int:
    if not plan_gate_ok(uid, "investments"):
        raise HTTPException(status_code=403, detail={"error": "pro_required", "feature": "investments"})
    return uid


@router.get("/investido", response_model=Investido)
def investido_(uid: int = Depends(_assinante)) -> Investido:
    return Investido(**investido.ler(uid))
