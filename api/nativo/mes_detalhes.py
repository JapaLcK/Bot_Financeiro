"""Mês completo sem top-N ou paginação: categorias, dias e movimentos de guardar."""
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from api.v2.resumo_mes import PADRAO_MES, mes_pedido
from api.v2.lancamentos import Lancamento
from api.v2.sessao import usuario_atual
from db.mes_detalhes import ler, ler_periodo
from utils_date import now_tz

from .dinheiro import Dinheiro, soma_arredondada_diverge

router = APIRouter()


class Categoria(BaseModel):
    categoria: str | None
    valor: Dinheiro
    quantidade: int


class Dia(BaseModel):
    dia: str
    entrou: Dinheiro
    saiu: Dinheiro


class Guardado(BaseModel):
    aportes: Dinheiro
    saques: Dinheiro
    liquido: Dinheiro
    cobertura: Literal["movimentos_registrados"]


class Origens(BaseModel):
    cartao: Dinheiro
    banco: Dinheiro
    carteira: Dinheiro
    registro_antigo: Dinheiro


class MesDetalhes(BaseModel):
    mes: str
    ate: str
    entrou: Dinheiro
    saiu: Dinheiro
    categorias: list[Categoria]
    origens: Origens
    dias: list[Dia]
    guardado: Guardado
    motivos: list[str]
    fora_do_total: int
    criterio_dias: Literal["data_do_lancamento_ou_compra_na_fatura_do_mes"]


@router.get("/mes-detalhes", response_model=MesDetalhes)
def mes_detalhes(mes: str | None = Query(None, pattern=PADRAO_MES),
                 uid: int = Depends(usuario_atual)) -> MesDetalhes:
    dados = MesDetalhes(**ler(uid, *mes_pedido(mes)))
    grupos = ([c.valor for c in dados.categorias],
              [d.entrou for d in dados.dias], [d.saiu for d in dados.dias],
              [getattr(dados.origens, k) for k in Origens.model_fields])
    if any(soma_arredondada_diverge(grupo) for grupo in grupos):
        dados.motivos.append("arredondamento_por_grupo")
    return dados


class MesDoPeriodo(BaseModel):
    mes: str
    entrou: Dinheiro
    saiu: Dinheiro


class CategoriaDoPeriodo(Categoria):
    percentual: int  # do total das categorias em reais (a base do widget "Para onde vai")
    maiores: list[Lancamento]


class Periodo(BaseModel):
    """Card `periodo` do chat (sem rota): 1 a 12 meses inteiros, a regra do painel mês a mês."""
    inicio: str
    fim: str
    ate: str
    entrou: Dinheiro
    saiu: Dinheiro
    saldo: Dinheiro
    meses: list[MesDoPeriodo]
    categorias: list[CategoriaDoPeriodo]
    motivos: list[str]
    historico_desde: str | None
    fora_do_total: int


def periodo(de: tuple[int, int], ate: tuple[int, int], uid: int) -> Periodo:
    d = ler_periodo(uid, de, ate, now_tz())
    base = sum((c["valor"] for c in d["categorias"]), Decimal(0))
    for c in d["categorias"]:
        c["percentual"] = int((c["valor"] * 100 / base).quantize(Decimal(1), ROUND_HALF_UP)) if base else 0
    p = Periodo(**d, saldo=d["entrou"] - d["saiu"])
    grupos = ([m.entrou for m in p.meses], [m.saiu for m in p.meses], [c.valor for c in p.categorias])
    if any(soma_arredondada_diverge(grupo) for grupo in grupos):
        p.motivos.append("arredondamento_por_grupo")
    return p
