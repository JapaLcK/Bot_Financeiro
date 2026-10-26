"""`GET /api/v2/comparacao?mes=AAAA-MM`: o mês contra o anterior equivalente (PL-07).

Plus ou Pro (`financial_comparison`): o gate é a dependência, roda antes de qualquer leitura,
e o Essencial recebe 403 `pro_required` sem payload. Janela, delta e o texto `escolha` são de
`core/services/comparacao_periodos.py`, a mesma conta da tool `compare_periods` do chat; aqui
só o formato da v2: dinheiro e `pct` são `Decimal` e saem como TEXTO, datas em texto ISO. Sem
`mes` = o mês corrente no fuso do app; mês futuro ou fora do serviço = 422 no envelope.
"""
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel

from api.v2.resumo_mes import PADRAO_MES, mes_pedido
from api.v2.sessao import usuario_atual
from core.services.comparacao_periodos import comparar_mes
from db import resumo_mes
from frontend.routes.shared import require_plan_feature

router = APIRouter()
RECURSO = "financial_comparison"


class JanelaComparada(BaseModel):
    inicio: str
    fim: str  # inclusivo
    dias: int


class LinhaComparada(BaseModel):
    total_atual: Decimal
    total_anterior: Decimal
    total_delta: Decimal
    pct: Decimal | None  # 1 casa; None = base zero ("nova"), e sempre no `resultado`
    estado: Literal["vazio", "nova", "sem_gasto", "igual", "variou"]


class CategoriaComparada(LinhaComparada):
    categoria: str


class CartaoLancamentos(BaseModel):
    atual: int
    anterior: int


class BaseComparacao(BaseModel):
    regra: Literal["entrou_e_saiu_do_mes"]
    cartao: Literal["fatura"]
    origem: str
    cartao_lancamentos: CartaoLancamentos | None


class Comparacao(BaseModel):
    modo: Literal["mes_aberto", "mes_fechado"]
    escolha: str
    atual: JanelaComparada
    anterior: JanelaComparada
    mesmo_tamanho: bool
    anterior_encurtado: bool
    base: BaseComparacao
    despesa: LinhaComparada | None  # None só com motivo "inicio_do_historico"
    receita: LinhaComparada | None
    resultado: LinhaComparada | None
    categorias: list[CategoriaComparada]
    categorias_omitidas: int
    motivo: Literal["sem_base", "inicio_do_historico", "categorias_indisponiveis"] | None
    motivos: list[Literal[resumo_mes.MOTIVOS]]


def _assinante(uid: int = Depends(usuario_atual)) -> int:
    require_plan_feature(uid, RECURSO)
    return uid


@router.get("/comparacao", response_model=Comparacao)
def comparacao(mes: str | None = Query(None, pattern=PADRAO_MES),
               uid: int = Depends(_assinante)) -> Comparacao:
    ano, m, agora = mes_pedido(mes)
    try:
        r = comparar_mes(uid, ano, m, agora)
    except ValueError as exc:  # `escolher_janelas`: o padrão aceita 1000-1999, o serviço não
        raise RequestValidationError([{"loc": ("query", "mes"), "msg": str(exc), "type": "value_error"}])
    if r is None:  # o plano mudou entre o gate e a leitura: o mesmo 403
        raise HTTPException(status_code=403, detail={"error": "pro_required", "feature": RECURSO})
    for lado in ("atual", "anterior"):
        r[lado] = {**r[lado], "inicio": r[lado]["inicio"].isoformat(), "fim": r[lado]["fim"].isoformat()}
    return Comparacao(**r)
