"""Contrato informado por investimento (`itens`) e quanto os investimentos renderam no Mês e no Total
(`rendimentos`, estimado pelo saldo: `db/rendimentos.py`). Não usa lastMonthRate/annualRate."""
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.nativo.dinheiro import Dinheiro
from api.v2.sessao import usuario_atual
from core.services.plan_service import plan_gate_ok
from db.connection import get_conn
from db.investido import TIPOS
from db.rendimento_contratado import ler
from db.rendimentos import fim_do_mes_anterior
from utils_date import today_tz

router = APIRouter()


class TaxaContratada(BaseModel):
    nome: str | None
    instituicao: str | None
    taxa: Decimal | None
    tipo_taxa: str | None
    observado_em: datetime | None
    motivos: list[str]


MotivoAtivo = Literal["saldo_ausente", "aplicado_ausente", "sem_base", "sem_leitura_no_mes", "sem_custo",
                      "quantidade_ausente", "quantidade_mudou"]


class PeriodoAtivo(BaseModel):
    valor: Dinheiro | None
    pct: Decimal | None
    motivo: MotivoAtivo | None


class Periodo(BaseModel):
    valor: Dinheiro | None
    pct: Decimal | None
    sem_dado: int


class Ativo(BaseModel):
    nome: str | None
    instituicao: str | None
    logo_proprio: str | None
    saldo: Dinheiro | None
    mes: PeriodoAtivo
    total: PeriodoAtivo


class Categoria(BaseModel):
    tipo: Literal[tuple(TIPOS)]
    rotulo: str
    saldo: Dinheiro | None
    quantidade: int
    mes: Periodo
    total: Periodo
    ativos: list[Ativo]


class Rendimentos(BaseModel):
    saldo: Dinheiro | None
    quantidade: int
    mes: Periodo
    total: Periodo
    mes_desde: date
    sincronizado_em: datetime | None
    categorias: list[Categoria]
    motivos: list[Literal["sem_posicoes", "banco_desatualizado", "plano_sem_investimentos"]]


class Rendimento(BaseModel):
    tipo: Literal["contratado"]
    itens: list[TaxaContratada]
    motivos: list[str]
    rendimentos: Rendimentos | None = None


@router.get("/rendimento", response_model=Rendimento)
def rendimento(uid: int = Depends(usuario_atual)) -> Rendimento:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("set transaction isolation level repeatable read, read only")
        dados = ler(cur, uid)
        conn.rollback()
    if not plan_gate_ok(uid, "investments"):  # o `itens` segue como sempre; o rendimento é do plano dos investimentos
        vazio = Periodo(valor=None, pct=None, sem_dado=0)
        dados["rendimentos"] = {"saldo": None, "quantidade": 0, "mes": vazio, "total": vazio,
                                "mes_desde": fim_do_mes_anterior(today_tz()), "sincronizado_em": None,
                                "categorias": [], "motivos": ["plano_sem_investimentos"]}
    return Rendimento(**dados)
