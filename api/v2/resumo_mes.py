"""`GET /api/v2/resumo-do-mes?mes=AAAA-MM`: Entrou e Saiu do mês (`db/resumo_mes.py`).

Padrão = o mês corrente no fuso do app. Formato inválido ou mês futuro = 422 no
envelope. Dinheiro é `Decimal` e sai como TEXTO; `ate` e `mes` são texto ISO
(`"2026-10-31"`, `"2026-10"`). `ate` é sempre o último dia do mês: a soma cobre o mês
inteiro, o corrente também (lançamento com data futura dentro do mês entra). `motivos`
vazio = número exato.
"""
from datetime import datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel

from api.v2.sessao import usuario_atual
from db import resumo_mes
from utils_date import now_tz

router = APIRouter()


class MesAnterior(BaseModel):
    mes: str
    entrou: Decimal
    saiu: Decimal


class ResumoDoMes(BaseModel):
    mes: str
    ate: str
    entrou: Decimal
    saiu: Decimal
    anterior: MesAnterior | None
    motivos: list[Literal[resumo_mes.MOTIVOS]]


PADRAO_MES = r"^[1-9][0-9]{3}-(0[1-9]|1[0-2])$"


def mes_pedido(mes: str | None) -> tuple[int, int, datetime]:
    """(ano, mês, agora) do `mes` já validado pelo `PADRAO_MES`: sem ele, o corrente no fuso
    do app; no futuro, 422 no envelope. Também da `GET /api/v2/lancamentos`."""
    agora = now_tz()
    ano, m = (int(mes[:4]), int(mes[5:])) if mes else (agora.year, agora.month)
    if (ano, m) > (agora.year, agora.month):
        raise RequestValidationError([{"loc": ("query", "mes"), "msg": "Mês no futuro.",
                                       "type": "value_error"}])
    return ano, m, agora


@router.get("/resumo-do-mes", response_model=ResumoDoMes)
def resumo(mes: str | None = Query(None, pattern=PADRAO_MES),
           uid: int = Depends(usuario_atual)) -> ResumoDoMes:
    return ResumoDoMes(**resumo_mes.resumo_do_mes(uid, *mes_pedido(mes)))
