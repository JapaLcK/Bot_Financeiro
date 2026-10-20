"""`GET /api/v2/caixinhas`: as caixinhas e o total guardado, a MESMA consulta do Banqueiro
(`_list_pockets`, `accrue=False`: ler não aplica juros, o saldo é o último registrado). Sem gate de plano
(o Grátis tem caixinha; `pockets_max` só limita criar). Dinheiro sai como TEXTO."""
from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.v2.orcamento_domestico import _d
from api.v2.sessao import usuario_atual
from core.services.ai_chat.tools.pockets import _list_pockets

router = APIRouter()


class Caixinha(BaseModel):
    id: int
    nome: str
    saldo: Decimal
    meta: Decimal | None
    progresso_pct: Decimal | None  # 0 a 100; null sem meta
    atingida: bool | None  # null sem meta


class Caixinhas(BaseModel):
    total: Decimal
    caixinhas: list[Caixinha]  # do maior saldo


@router.get("/caixinhas", response_model=Caixinhas)
def caixinhas(uid: int = Depends(usuario_atual)) -> Caixinhas:
    ps = _list_pockets(uid, {}, accrue=False)["pockets"]
    linhas = [Caixinha(id=p["id"], nome=p["name"], saldo=_d(p["balance"]), meta=_d(p["target_amount"]),
                       progresso_pct=_d(p["progress_pct"]), atingida=p["achieved"]) for p in ps]
    return Caixinhas(total=sum((c.saldo for c in linhas), Decimal("0.00")), caixinhas=linhas)
