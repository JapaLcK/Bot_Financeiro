"""`GET /api/v2/cartoes` e `GET /api/v2/parcelas`: os dados dos cards de fatura e parcelas do Resumo do /painel, só LEITURA.
A MESMA consulta da rota legada e do card do chat (`resumo_cartoes`, `list_installment_groups_detailed`); aqui só o recorte que o
card desenha e dinheiro como TEXTO. Sem gate de plano (a legada e o chat não têm)."""
from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.v2.orcamento_domestico import _d
from api.v2.sessao import usuario_atual
from db.cards import list_installment_groups_detailed
from db.cartoes_resumo import resumo_cartoes

router = APIRouter()


class FaturaAberta(BaseModel):
    paid_amount: Decimal
    due_amount: Decimal


class Cartao(BaseModel):
    name: str
    last4: str | None
    due_day: int
    closing_day: int
    credit_limit: Decimal | None
    credit_used: Decimal
    open_bill: FaturaAberta


class Cartoes(BaseModel):
    cards: list[Cartao]


class ParcelaDoGrupo(BaseModel):
    installment_no: int
    valor: Decimal
    is_paid: bool
    is_next: bool
    due_date: str


class Parcelamento(BaseModel):
    name: str
    card_name: str | None
    installments_total: int
    parcelas: list[ParcelaDoGrupo]


class Parcelas(BaseModel):
    installments: list[Parcelamento]  # a mais urgente primeiro


@router.get("/cartoes", response_model=Cartoes)
def cartoes(uid: int = Depends(usuario_atual)) -> Cartoes:
    return Cartoes(cards=[Cartao(
        name=c["name"], last4=c["last4"], due_day=c["due_day"], closing_day=c["closing_day"],
        credit_limit=_d(c["credit_limit"]), credit_used=_d(c["credit_used"]),
        open_bill=FaturaAberta(paid_amount=_d(c["open_bill"]["paid_amount"]), due_amount=_d(c["open_bill"]["due_amount"])),
    ) for c in resumo_cartoes(uid)])


@router.get("/parcelas", response_model=Parcelas)
def parcelas(uid: int = Depends(usuario_atual)) -> Parcelas:
    return Parcelas(installments=[Parcelamento(
        name=g["name"], card_name=g["card_name"], installments_total=g["installments_total"],
        parcelas=[ParcelaDoGrupo(installment_no=p["installment_no"], valor=_d(p["valor"]), is_paid=p["is_paid"],
                          is_next=p["is_next"], due_date=p["due_date"]) for p in g["parcelas"]],
    ) for g in list_installment_groups_detailed(uid, "urgency")])
