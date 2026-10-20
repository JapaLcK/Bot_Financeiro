"""`GET /api/v2/contas-a-pagar`: as contas a pagar (boletos) pendentes, a MESMA consulta do Carteiro
(`_get_bills_to_pay`, `sync=False`: ler não gera instância do próximo ciclo, ao contrário da rota
antiga `/recurring-bills`). Gate `recurring_expenses`, o da agenda de boletos. Dinheiro sai como TEXTO."""
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.v2.orcamento_domestico import _d
from api.v2.sessao import usuario_atual
from core.services.ai_chat.tools.bills import _get_bills_to_pay
from core.services.plan_service import plan_gate_ok

router = APIRouter()


class ContaAPagar(BaseModel):
    id: int
    nome: str | None
    categoria: str | None
    vence_em: str | None  # AAAA-MM-DD
    dias: int | None  # 0 = hoje, negativo = vencida
    valor: Decimal | None  # com `variavel`, só a estimativa
    variavel: bool
    vencida: bool


class ContasAPagar(BaseModel):
    hoje: str
    pendentes: int
    vencidas: int
    total_estimado: Decimal
    tem_variavel: bool
    contas: list[ContaAPagar]  # pendentes, da que vence primeiro


def _agenda(uid: int = Depends(usuario_atual)) -> int:
    if not plan_gate_ok(uid, "recurring_expenses"):
        raise HTTPException(status_code=403, detail={"error": "pro_required", "feature": "recurring_expenses"})
    return uid


@router.get("/contas-a-pagar", response_model=ContasAPagar)
def contas_a_pagar(uid: int = Depends(_agenda)) -> ContasAPagar:
    r = _get_bills_to_pay(uid, {}, sync=False)
    return ContasAPagar(
        hoje=r["today"], pendentes=r["count_pending"], vencidas=r["overdue_count"],
        total_estimado=_d(r["total_estimated_value"]), tem_variavel=r["has_variable"],
        contas=[ContaAPagar(id=b["id"], nome=b["name"], categoria=b["category"], vence_em=b["due_date"], dias=b["days_until"],
                            valor=_d(b["amount"]), variavel=b["variable"], vencida=b["overdue"]) for b in r["bills"]])
