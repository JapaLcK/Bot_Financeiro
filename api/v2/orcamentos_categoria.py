"""`/api/v2/orcamentos-categoria`: um teto mensal por categoria (`db/budgets.py`, que conta em float;
aqui o float vira `Decimal` de 2 casas). Só o mês corrente (sem `mes`). Sem gate de plano: o Grátis usa,
com teto de `plan_limits.budgets_max` (o mesmo da rota antiga `POST /budgets/{uid}`), que vai no GET
porque o `detail` do 403 perde campos extras no envelope. Dinheiro e % saem como TEXTO. A categoria
nova tem de ser do catálogo do usuário; editar um orçamento que já existe vale mesmo fora dele."""
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.v2.lancamentos import _VALOR, _recusa, _texto, _valor
from api.v2.orcamento_domestico import _d
from api.v2.resumo_mes import mes_pedido
from api.v2.sessao import usuario_atual
from core.services.plan_limits import PlanLimitExceeded
from core.services.plan_service import get_user_limits
from db import budgets
from db.categories import CATEGORY_NAME_MAX_LEN, ensure_user_categories_seeded, resolve_category_input
from db.connection import cat_visual

router = APIRouter()


class Orcamento(BaseModel):
    categoria: str  # grafia gravada: é a identidade do orçamento
    chave: str  # cat_key; o front compara com /categorias.chave
    emoji: str
    icone: str
    cor: str
    teto: Decimal
    gasto: Decimal
    restante: Decimal  # negativo = passou do teto
    usado_pct: Decimal
    status: Literal["verde", "amarelo", "vermelho"]


class TotalOrcamentos(BaseModel):
    teto: Decimal
    gasto: Decimal
    restante: Decimal
    usado_pct: Decimal
    em_risco: int  # categorias em amarelo ou vermelho


class OrcamentosCategoria(BaseModel):
    mes: str  # "AAAA-MM" corrente, no fuso do app
    limite: int | None  # budgets_max do plano; null = sem teto
    orcamentos: list[Orcamento]
    total: TotalOrcamentos


class OrcamentoIn(BaseModel):
    categoria: str = Field(max_length=CATEGORY_NAME_MAX_LEN)
    valor: str = Field(pattern=_VALOR)


class OrcamentoApagar(BaseModel):
    categoria: str = Field(max_length=CATEGORY_NAME_MAX_LEN)


def _limite(uid: int) -> int | None:
    return get_user_limits(uid)["budgets_max"]


def _monta(uid: int) -> OrcamentosCategoria:
    ano, m, _ = mes_pedido(None)
    # O ícone/cor vem do join com `user_categories`; conta antiga que nunca abriu /categorias tem o catálogo
    # vazio e cairia toda no visual neutro. O seed é idempotente (como em `/categorias` e `db/gastos.py`).
    ensure_user_categories_seeded(uid)
    s = budgets.get_budgets_status_for_month(uid, f"{ano:04d}-{m:02d}")
    t = s["totals"]
    return OrcamentosCategoria(
        mes=s["month"],
        limite=_limite(uid),
        orcamentos=[Orcamento(categoria=b["categoria"], chave=b["cat_key"], **cat_visual(b["emoji"], b["color"]),
                              teto=_d(b["budget"]), gasto=_d(b["spent"]), restante=_d(b["remaining"]),
                              usado_pct=_d(b["pct"]), status=b["status"]) for b in s["budgets"]],
        total=TotalOrcamentos(teto=_d(t["budget"]), gasto=_d(t["spent"]), restante=_d(t["remaining"]),
                              usado_pct=_d(t["pct"]), em_risco=t["at_risk"]),
    )


@router.get("/orcamentos-categoria", response_model=OrcamentosCategoria)
def ler(uid: int = Depends(usuario_atual)) -> OrcamentosCategoria:
    return _monta(uid)


@router.post("/orcamentos-categoria", response_model=OrcamentosCategoria)
def salvar(corpo: OrcamentoIn, uid: int = Depends(usuario_atual)) -> OrcamentosCategoria:
    cat = _texto("categoria", corpo.categoria)
    valor = _valor(corpo.valor)
    ensure_user_categories_seeded(uid)  # a categoria custom de quem nunca semeou só existe nos lançamentos
    existente = budgets.get_budget(uid, cat)
    nome = existente["categoria"] if existente else resolve_category_input(uid, cat, create=False)
    if nome is None:
        raise _recusa("categoria", "Escolha uma das suas categorias.", "body")
    try:
        budgets.upsert_budget(uid, nome, valor, limite=_limite(uid))
    except PlanLimitExceeded:
        raise HTTPException(status_code=403, detail={"error": "plan_limit"}) from None
    except ValueError:
        raise _recusa("valor", "Valor inválido.", "body") from None
    return _monta(uid)


@router.post("/orcamentos-categoria/apagar", response_model=OrcamentosCategoria)
def apagar(corpo: OrcamentoApagar, uid: int = Depends(usuario_atual)) -> OrcamentosCategoria:
    if not budgets.delete_budget(uid, _texto("categoria", corpo.categoria)):
        raise HTTPException(status_code=404)
    return _monta(uid)
