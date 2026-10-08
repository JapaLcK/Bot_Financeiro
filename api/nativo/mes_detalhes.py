"""Mês completo sem top-N ou paginação: categorias, dias e movimentos de guardar."""
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from api.v2.resumo_mes import PADRAO_MES, mes_pedido
from api.v2.sessao import usuario_atual
from db.mes_detalhes import ler

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


class MesDetalhes(BaseModel):
    mes: str
    ate: str
    entrou: Dinheiro
    saiu: Dinheiro
    categorias: list[Categoria]
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
              [d.entrou for d in dados.dias], [d.saiu for d in dados.dias])
    if any(soma_arredondada_diverge(grupo) for grupo in grupos):
        dados.motivos.append("arredondamento_por_grupo")
    return dados
