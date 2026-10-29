"""`GET /api/v2/insights`: as dicas do Piggy (card do Resumo), só LEITURA. A MESMA `generate_ai_insights` da rota legada
(`force=False`: cache de até 6 h por usuário, ou até a meia-noite de Brasília; sem cache gera na hora, com as consultas dos detectores e SEM IA (D12 do PL-06),
no máximo 1 geração por usuário em voo, também sob pedidos concorrentes: `generate_ai_insights` serializa a geração por
usuário, por processo; sem rate limit próprio, a /api/v2 não tem limiter). A lista já vem projetada por plano e o modelo
daqui só leva `title`/`message`. O gate `insights` (Plus) roda antes de ler ou gerar."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.v2.sessao import usuario_atual
from core.ai_patterns import generate_ai_insights
from frontend.routes.shared import require_plan_feature

router = APIRouter()


class Insight(BaseModel):
    title: str
    message: str


class Insights(BaseModel):
    insights: list[Insight]


def _assinante(uid: int = Depends(usuario_atual)) -> int:
    require_plan_feature(uid, "insights")
    return uid


@router.get("/insights", response_model=Insights)
def insights(uid: int = Depends(_assinante)) -> Insights:
    return Insights(insights=[Insight(title=i["title"], message=i["message"]) for i in generate_ai_insights(uid, force=False) or []])
