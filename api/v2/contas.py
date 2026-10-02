"""`GET /api/v2/contas`: o bloco de contas do Resumo (`db/contas_hoje.py`).

Dinheiro é `Decimal` e sai como TEXTO ("1234.56"): contrato de toda rota da v2. Sem
`raw` e sem `provider_*_id`.
"""
from datetime import datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.v2.sessao import usuario_atual
from core.services.pluggy_health import _LABELS
from db import contas_hoje
from db.connection import get_conn

router = APIRouter()

MotivoCarteira = Literal[contas_hoje.MOTIVOS_CARTEIRA]
MotivoConta = Literal[contas_hoje.MOTIVOS_CONTA]


class Carteira(BaseModel):
    saldo: Decimal
    motivos: list[MotivoCarteira]


class Conta(BaseModel):
    id: int
    instituicao: str | None
    nome: str | None
    saldo: Decimal | None
    moeda: str
    no_total: bool
    conexao: Literal[tuple(_LABELS)]
    sincronizado_em: datetime | None
    motivos: list[MotivoConta]


class Contas(BaseModel):
    total: Decimal
    motivos: list[Literal[MotivoCarteira, MotivoConta]]
    fora_do_total: int
    carteira: Carteira
    contas: list[Conta]


@router.get("/contas", response_model=Contas)
def contas(uid: int = Depends(usuario_atual)) -> Contas:
    # Um snapshot só: o total, a carteira e cada conta saem do mesmo instante.
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("set transaction isolation level repeatable read, read only")
        dados = contas_hoje.listar(cur, uid)
        conn.rollback()
    return Contas(**dados)
