"""`GET /api/v2/insights`: as dicas do Piggy (card do Resumo), só LEITURA. A MESMA `generate_ai_insights` da rota legada
(`force=False`: cache de 6 h por usuário; sem cache gera na hora, no máximo 1 chamada de IA a cada 6 h). O gate `insights`
(Plus) roda antes de ler ou gerar."""
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
