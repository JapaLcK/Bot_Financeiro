"""Patrimônio real e fotos existentes: somente a projeção pública, sem base bruta."""
from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.v2.sessao import usuario_atual
from core.services.plan_service import history_earliest_date
from db.connection import get_conn
from db.patrimonio import calcular
from utils_date import day_tz, now_tz

from .dinheiro import Dinheiro, soma_arredondada_diverge

router = APIRouter()


class Partes(BaseModel):
    carteira: Dinheiro
    bancos: Dinheiro
    investimentos_banco: Dinheiro
    caixinhas: Dinheiro
    investimentos_manuais: Dinheiro


class Foto(BaseModel):
    dia: date
    total: Dinheiro
    partes: Partes
    motivos: list[str]


class Patrimonio(BaseModel):
    total: Dinheiro
    partes: Partes
    motivos: list[str]
    historico: list[Foto]
    historico_desde: date | None


def _publico(dados: dict) -> dict:
    partes = {nome: dados[nome] for nome in Partes.model_fields}
    motivos = list(dados["motivos"])
    # A projeção em centavos preserva o total calculado/fotografado oficialmente.
    # Arredondar cada parte não autoriza recalcular esse total financeiro.
    if soma_arredondada_diverge(list(partes.values()), dados["total"]):
        motivos.append("arredondamento_por_grupo")
    return {"total": dados["total"], "partes": partes, "motivos": motivos}


@router.get("/patrimonio", response_model=Patrimonio)
def patrimonio(uid: int = Depends(usuario_atual)) -> Patrimonio:
    agora = now_tz()
    corte = history_earliest_date(uid, agora)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("set transaction isolation level repeatable read, read only")
        atual = _publico(calcular(cur, uid))
        cur.execute("""select dia, total, carteira, bancos, investimentos_banco,
                              caixinhas, investimentos_manuais, motivos
                         from patrimonio_fotos
                        where user_id=%s and dia >= %s and dia <= %s order by dia""",
                    (uid, corte or date.min, day_tz(agora)))
        fotos = [{"dia": r["dia"], **_publico(r)} for r in cur.fetchall()]
        conn.rollback()
    return Patrimonio(**atual, historico=fotos,
                      historico_desde=fotos[0]["dia"] if fotos else None)
