"""`GET /api/v2/gastos?mes=AAAA-MM`: "Para onde vai", a visão do mês (`db/gastos.py`).

`mes` como em `resumo-do-mes` (sem ele o corrente; futuro ou fora de `AAAA-MM` = 422). Só saída, pelas
mesmas linhas do `/lancamentos` sem interno e sem outra moeda. `valor` é o mês inteiro; `valor_ate_corte`
e `anterior_ate_corte` vão do dia 1 a `dia_de_corte` (`null` quando o corte do plano toca o mês anterior).
`anterior_total_ate_corte` = todas as saídas do mês anterior até o mesmo corte (inclui categoria que só existe lá).
`disponivel: false` = mês todo antes do corte do plano (nunca "R$ 0"). Dinheiro `Decimal` sai como texto.
"""
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from api.nativo.dinheiro import soma_arredondada_diverge
from api.v2.resumo_mes import PADRAO_MES, mes_pedido
from api.v2.sessao import usuario_atual
from db import gastos

router = APIRouter()


class CategoriaVisual(BaseModel):
    chave: str
    nome: str
    emoji: str
    cor: str


class GastoCategoria(CategoriaVisual):
    valor: Decimal
    quantidade: int
    valor_ate_corte: Decimal
    anterior_ate_corte: Decimal | None


class GastoMaior(BaseModel):
    descricao: str
    valor: Decimal
    categoria: str


class GastoDia(BaseModel):
    dia: str
    total: Decimal
    maiores: list[GastoMaior]


class ForaDaGrade(BaseModel):
    quantidade: int
    valor: Decimal


class Gastos(BaseModel):
    mes: str
    disponivel: bool
    hoje: str
    dia_de_corte: int
    historico_desde: str | None
    total: Decimal | None
    categorias: list[GastoCategoria]
    dias: list[GastoDia]
    fora_da_grade: ForaDaGrade
    saidas_em_outra_moeda: int
    anterior_total_ate_corte: Decimal | None
    motivos: list[Literal[gastos.MOTIVOS_GASTOS]]


@router.get("/gastos", response_model=Gastos)
def gastos_do_mes(mes: str | None = Query(None, pattern=PADRAO_MES),
                  uid: int = Depends(usuario_atual)) -> Gastos:
    d = gastos.ler_mes(uid, *mes_pedido(mes))
    if d["total"] is not None and (
            soma_arredondada_diverge([c["valor"] for c in d["categorias"]], d["total"])
            or soma_arredondada_diverge([x["total"] for x in d["dias"]] + [d["fora_da_grade"]["valor"]],
                                        d["total"])):
        d["motivos"] = [m for m in gastos.MOTIVOS_GASTOS if m in {*d["motivos"], "arredondamento_por_grupo"}]
    return Gastos(**d)
