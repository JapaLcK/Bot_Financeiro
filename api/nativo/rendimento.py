"""Contrato informado por investimento; não usa lastMonthRate/annualRate como yield."""
from datetime import datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.v2.sessao import usuario_atual
from db.connection import get_conn
from db.rendimento_contratado import ler

router = APIRouter()


class TaxaContratada(BaseModel):
    nome: str | None
    instituicao: str | None
    taxa: Decimal | None
    tipo_taxa: str | None
    observado_em: datetime | None
    motivos: list[str]


class Rendimento(BaseModel):
    tipo: Literal["contratado"]
    itens: list[TaxaContratada]
    motivos: list[str]


@router.get("/rendimento", response_model=Rendimento)
def rendimento(uid: int = Depends(usuario_atual)) -> Rendimento:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("set transaction isolation level repeatable read, read only")
        dados = ler(cur, uid)
        conn.rollback()
    return Rendimento(**dados)
