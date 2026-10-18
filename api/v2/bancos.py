"""`GET /api/v2/bancos`: o que cada banco conectado vale, saldo em conta + investido (`db/bancos.py`).

Dinheiro sai como TEXTO decimal; `null` = não dá para saber (≠ zero). Uma entrada por conexão, pelo id
LOCAL (`conexao_id`). Sem `raw`, sem `provider_*_id`, sem id de conta. Sem o recurso `investments` do
plano o saldo segue e `investido` sai `null` com `plano_sem_investimentos` (não é o 403 de `/investido`).
"""
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.v2.sessao import usuario_atual
from core.services.plan_service import plan_gate_ok
from db import bancos

router = APIRouter()

MotivoBanco = Literal[bancos.MOTIVOS]


class Banco(BaseModel):
    conexao_id: int
    saldo_conta: Decimal | None
    investido: Decimal | None
    total: Decimal | None
    contas: int
    cartoes: int
    investimentos: int | None
    motivos: list[MotivoBanco]


class Bancos(BaseModel):
    total: Decimal | None
    motivos: list[MotivoBanco]
    bancos: list[Banco]


@router.get("/bancos", response_model=Bancos)
def bancos_(uid: int = Depends(usuario_atual)) -> Bancos:
    return Bancos(**bancos.ler(uid, plan_gate_ok(uid, "investments")))
