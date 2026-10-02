"""`/api/v2/assinaturas`: a lista do Recurring Payments da Pluggy e a marcação
do usuário. Plus ou Pro (`subscriptions` em `FEATURE_MIN_TIER_V2`). O CSRF do
POST é o `csrf_middleware` do monólito, que cobre `/api/v2/*`."""
from datetime import date
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.v2.sessao import usuario_atual
from core.services.assinaturas import listar_assinaturas
from core.services.plan_service import plan_gate_ok
from db.of_recurring import marcar

router = APIRouter()


class Meio(BaseModel):
    tipo: Literal["cartao", "conta"]
    nome: str
    final: str | None


class Assinatura(BaseModel):
    chave: str
    nome: str
    categoria: str | None
    valor: Decimal
    valor_anterior: Decimal | None
    reajuste_em: str | None
    dia: int
    proxima: str
    ultima: str
    desde: str
    meses: int
    meio: Meio
    status: Literal["ativa", "possivelmente_cancelada"]
    marcada: bool


class Assinaturas(BaseModel):
    servicos: list[Assinatura]
    outras: list[Assinatura]
    ignoradas: list[Assinatura]
    total_mensal: Decimal
    total_anual: Decimal


class MarcaIn(BaseModel):
    chave: str
    status: Literal["assinatura", "ignorar", "nenhuma"]


def _assinante(uid: int = Depends(usuario_atual)) -> int:
    if not plan_gate_ok(uid, "subscriptions"):
        raise HTTPException(status_code=403, detail={"error": "pro_required", "feature": "subscriptions"})
    return uid


@router.get("/assinaturas", response_model=Assinaturas)
def assinaturas(uid: int = Depends(_assinante)) -> Assinaturas:
    return Assinaturas(**listar_assinaturas(uid, date.today()))


@router.post("/assinaturas/marca", response_model=Assinaturas)
def marca(corpo: MarcaIn, uid: int = Depends(_assinante)) -> Assinaturas:
    if corpo.chave not in listar_assinaturas(uid, date.today())["chaves"]:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    marcar(uid, corpo.chave, corpo.status)
    return Assinaturas(**listar_assinaturas(uid, date.today()))
