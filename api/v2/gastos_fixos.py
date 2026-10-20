"""`POST /api/v2/gastos-fixos`: cadastra um gasto fixo mensal (`db.recurring.create_recurring_expense`, o
mesmo do `/recurring-expenses/{uid}` do /app: nenhuma regra de gravação aqui). Mensal e débito automático.
Gate `recurring_expenses` (Essencial ou acima). A categoria tem de ser do catálogo do usuário; o cartão tem
de ser dele (o `create_` confere com o `user_id`). Dinheiro entra e sai como TEXTO. O CSRF é o do pai."""
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.v2.lancamentos import _VALOR, _recusa, _texto, _valor
from api.v2.sessao import usuario_atual
from core.services.plan_service import plan_gate_ok
from db.categories import CATEGORY_NAME_MAX_LEN, ensure_user_categories_seeded, resolve_category_input
from db.recurring import create_recurring_expense

router = APIRouter()
_FORMA = {"conta": "account", "cartao": "credit_card"}
_ERRO = {"CARTAO_NAO_ENCONTRADO": ("cartao_id", "Cartão não encontrado."),
         "CARTAO_OBRIGATORIO": ("cartao_id", "Escolha o cartão."),
         "NOME_INVALIDO": ("nome", "Não pode ser vazio."),
         "VALOR_INVALIDO": ("valor", "Valor inválido."),
         "DIA_INVALIDO": ("dia", "O dia vai de 1 a 31.")}


class GastoFixoIn(BaseModel):
    nome: str = Field(min_length=1, max_length=80)
    valor: str = Field(pattern=_VALOR)
    dia: int = Field(ge=1, le=31)
    categoria: str = Field(max_length=CATEGORY_NAME_MAX_LEN)
    forma: Literal["conta", "cartao"]
    cartao_id: int | None = Field(None, ge=1, le=2**63 - 1)


class GastoFixo(BaseModel):
    id: int
    nome: str
    valor: Decimal
    dia: int
    categoria: str
    forma: Literal["conta", "cartao"]


def _assinante(uid: int = Depends(usuario_atual)) -> int:
    if not plan_gate_ok(uid, "recurring_expenses"):
        raise HTTPException(status_code=403, detail={"error": "pro_required", "feature": "recurring_expenses"})
    return uid


@router.post("/gastos-fixos", response_model=GastoFixo)
def criar(corpo: GastoFixoIn, uid: int = Depends(_assinante)) -> GastoFixo:
    nome = _texto("nome", corpo.nome)
    valor = _valor(corpo.valor)
    if corpo.forma == "cartao" and corpo.cartao_id is None:
        raise _recusa("cartao_id", "Escolha o cartão.", "body")
    ensure_user_categories_seeded(uid)
    cat = resolve_category_input(uid, _texto("categoria", corpo.categoria), create=False)
    if cat is None:
        raise _recusa("categoria", "Escolha uma das suas categorias.", "body")
    try:
        g = create_recurring_expense(uid, nome, valor, cat, corpo.dia, _FORMA[corpo.forma],
                                     corpo.cartao_id, frequency="monthly", payment_mode="autopay")
    except ValueError as e:
        campo, msg = _ERRO.get(str(e), ("corpo", "Não foi possível cadastrar."))
        raise _recusa(campo, msg, "body") from None
    return GastoFixo(id=g["id"], nome=g["name"], valor=Decimal(str(g["amount"])).quantize(Decimal("0.01")),
                     dia=g["due_day"], categoria=g["category"], forma=corpo.forma)
