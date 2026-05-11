"""
Limites e features de cada plano (Free vs Pro v1).

Single source of truth — quando precisar mudar o que Free pode fazer, mexer aqui
e tudo (backend gates, frontend modal de upgrade, copy de marketing) deve usar
estas constantes em vez de hardcodar.

Convenção: valor None em limites numéricos significa "ilimitado".
"""

from typing import TypedDict


class PlanLimits(TypedDict):
    pockets_max: int | None
    cards_max: int | None
    history_days: int | None
    investments_enabled: bool
    ofx_enabled: bool
    export_enabled: bool
    ai_conversational_enabled: bool
    ai_categorization_enabled: bool


FREE_LIMITS: PlanLimits = {
    "pockets_max": 1,
    "cards_max": 1,
    "history_days": 30,
    "investments_enabled": False,
    "ofx_enabled": False,
    "export_enabled": False,
    "ai_conversational_enabled": False,
    "ai_categorization_enabled": True,
}


PRO_LIMITS: PlanLimits = {
    "pockets_max": None,
    "cards_max": None,
    "history_days": None,
    "investments_enabled": True,
    "ofx_enabled": True,
    "export_enabled": True,
    "ai_conversational_enabled": True,
    "ai_categorization_enabled": True,
}


def limits_for(plan: str) -> PlanLimits:
    return PRO_LIMITS if (plan or "").lower() == "pro" else FREE_LIMITS


class PlanLimitExceeded(Exception):
    """Free atingiu o limite de uma feature. Carrega `feature` pra UIs decidirem
    o que mostrar (badge, modal, mensagem amigável) e `message` pronta pra
    canais texto (bot Discord/WhatsApp, IA conversacional)."""

    def __init__(self, feature: str, message: str):
        super().__init__(message)
        self.feature = feature
        self.message = message
