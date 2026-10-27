"""`GET /api/v2/renda`: receita e despesa dos últimos 6 meses (o card de renda do Resumo), só LEITURA. A MESMA
`compute_evolution` da rota legada e do card do chat; o gate `financial_comparison` (Plus) é a dependência, roda
antes de qualquer leitura (reaproveita o de `/comparacao`). Dinheiro como TEXTO."""
from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.v2.comparacao import _assinante
from api.v2.orcamento_domestico import _d
from db.analytics import compute_evolution

router = APIRouter()
MESES = 6


class MesRenda(BaseModel):
    month: str  # AAAA-MM
    income: Decimal
    expense: Decimal


class RendaMensal(BaseModel):
    evolution: list[MesRenda]  # do mais antigo ao mês corrente, todos os MESES (zero sem movimento)
    months: int


@router.get("/renda", response_model=RendaMensal)
def renda(uid: int = Depends(_assinante)) -> RendaMensal:
    return RendaMensal(months=MESES, evolution=[MesRenda(month=m["month"], income=_d(m["income"]), expense=_d(m["expense"]))
                                          for m in compute_evolution(uid, MESES)])
