"""`/api/v2/progresso-milhao`: só o patrimônio de hoje (`db/patrimonio.calcular`, a mesma conta da
tela Patrimônio do app); a projeção até o milhão é do cliente (`milhao.ts`). Plus ou Pro
(`million_progress`). O gate roda ANTES de qualquer leitura. Sem entrada: o usuário é o da sessão."""
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.v2.sessao import usuario_atual
from core.services.plan_service import plan_gate_ok
from db.connection import get_conn
from db.patrimonio import calcular

router = APIRouter()


class ProgressoMilhao(BaseModel):
    patrimonio: Decimal  # texto com 2 casas
    motivos: list[str]


def _jogador(uid: int = Depends(usuario_atual)) -> int:
    if not plan_gate_ok(uid, "million_progress"):
        raise HTTPException(status_code=403, detail={"error": "pro_required", "feature": "million_progress"})
    return uid


@router.get("/progresso-milhao", response_model=ProgressoMilhao)
def ler(uid: int = Depends(_jogador)) -> ProgressoMilhao:
    with get_conn() as conn, conn.cursor() as cur:  # leitura igual à de api/nativo/patrimonio.py
        cur.execute("set transaction isolation level repeatable read, read only")
        p = calcular(cur, uid)
        conn.rollback()
    motivos = list(p["motivos"])
    # Nenhuma conta BRL nem posição de conexão viva entrou: o número é só o manual (ou zero).
    if not p["base"]["contas"] and not p["base"]["posicoes"]:
        motivos.append("sem_banco_conectado")
    return ProgressoMilhao(patrimonio=p["total"].quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), motivos=motivos)
