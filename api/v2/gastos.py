"""`GET /api/v2/gastos?mes=AAAA-MM`: "Para onde vai", a visão do mês (`db/gastos.py`).

`mes` como em `resumo-do-mes` (sem ele o corrente; futuro ou fora de `AAAA-MM` = 422). Só saída, pelas
mesmas linhas do `/lancamentos` sem interno e sem outra moeda. `valor` é o mês inteiro; `valor_ate_corte`
e `anterior_ate_corte` vão do dia 1 a `dia_de_corte` (`null` quando o corte do plano toca o mês anterior).
`anterior_total_ate_corte` = todas as saídas do mês anterior até o mesmo corte (inclui categoria que só existe lá).
`disponivel: false` = mês todo antes do corte do plano (nunca "R$ 0"). Dinheiro `Decimal` sai como texto.

`GET /api/v2/gastos/categoria?mes=&categoria=<chave>`: o detalhe de uma categoria do mês (`gastos.ler_categoria`),
das mesmas linhas. Nunca sai `raw`, CNPJ, nome do recebedor, `businessName` nem id de linha.
"""
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from api.nativo.dinheiro import soma_arredondada_diverge
from api.v2.resumo_mes import PADRAO_MES, mes_pedido
from api.v2.sessao import usuario_atual
from db import CATEGORY_NAME_MAX_LEN, gastos, lancamentos

router = APIRouter()


class CategoriaVisual(BaseModel):
    chave: str
    nome: str
    emoji: str
    icone: str
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


class GastoDiaCategoria(BaseModel):
    chave: str
    valor: Decimal


class GastoDia(BaseModel):
    dia: str
    total: Decimal
    maiores: list[GastoMaior]
    quantidade: int  # as saídas do dia (as que somam `total`); `maiores` é só o topo
    categorias: list[GastoDiaCategoria]  # Σ valor = `total`; maior primeiro, empate pela chave


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


def _dia_diverge(dias) -> bool:
    """O card do dia mostra `categorias` em centavos ao lado de `total`: a soma exibida tem de fechar, dia a dia."""
    return any(soma_arredondada_diverge([c["valor"] for c in x["categorias"]], x["total"]) for x in dias)


@router.get("/gastos", response_model=Gastos)
def gastos_do_mes(mes: str | None = Query(None, pattern=PADRAO_MES),
                  uid: int = Depends(usuario_atual)) -> Gastos:
    d = gastos.ler_mes(uid, *mes_pedido(mes))
    if d["total"] is not None and (
            soma_arredondada_diverge([c["valor"] for c in d["categorias"]], d["total"])
            or soma_arredondada_diverge([x["total"] for x in d["dias"]] + [d["fora_da_grade"]["valor"]],
                                        d["total"])
            or _dia_diverge(d["dias"])):
        d["motivos"] = [m for m in gastos.MOTIVOS_GASTOS if m in {*d["motivos"], "arredondamento_por_grupo"}]
    return Gastos(**d)


class CompraRecente(BaseModel):
    data: str
    descricao: str
    mensagem: str | None
    origem: Literal[lancamentos.ORIGENS]
    valor: Decimal


class MesDaSerie(BaseModel):
    mes: str
    valor: Decimal | None  # null = o corte do plano toca o mês (nunca zero)


class Estabelecimento(BaseModel):
    nome: str
    valor: Decimal
    quantidade: int


class GastosDaCategoria(BaseModel):
    mes: str
    disponivel: bool
    hoje: str
    historico_desde: str | None
    categoria: CategoriaVisual
    total: Decimal | None
    compras: int | None
    valor_medio: Decimal | None
    ultimas: list[CompraRecente]
    serie: list[MesDaSerie]
    estabelecimentos: list[Estabelecimento]
    dias: list[GastoDia]
    fora_da_grade: ForaDaGrade
    saidas_em_outra_moeda: int
    motivos: list[Literal[gastos.MOTIVOS_GASTOS]]


@router.get("/gastos/categoria", response_model=GastosDaCategoria)
def gastos_da_categoria(mes: str | None = Query(None, pattern=PADRAO_MES),
                        categoria: str = Query(..., min_length=1, max_length=CATEGORY_NAME_MAX_LEN),
                        uid: int = Depends(usuario_atual)) -> GastosDaCategoria:
    ano, m, agora = mes_pedido(mes)
    d = gastos.ler_categoria(uid, ano, m, categoria, agora)
    if d["total"] is not None and soma_arredondada_diverge(
            [x["total"] for x in d["dias"]] + [d["fora_da_grade"]["valor"]], d["total"]):
        d["motivos"] = [m for m in gastos.MOTIVOS_GASTOS if m in {*d["motivos"], "arredondamento_por_grupo"}]
    return GastosDaCategoria(**d)
