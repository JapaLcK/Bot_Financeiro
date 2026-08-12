"""
Limites e features de cada plano.

Single source of truth — quando precisar mudar o que um plano pode fazer, mexer
aqui e tudo (backend gates, frontend modal de upgrade, copy de marketing) deve
usar estas constantes em vez de hardcodar.

Dois mundos convivem atrás do flag PLANS_V2_ENABLED (ver plan_service):

  • v1 (flag OFF, produção atual): binário Free × Pro. `limits_for(plan)`
    mantém o comportamento histórico — "pro" ganha tudo, resto cai no FREE.
  • v2 (flag ON): escada de 4 tiers — free (Grátis) < essencial < plus < pro.
    "plus" é o antigo Pro de R$ 19,90 (valor 'pro' no banco é alias legado de
    plus). "pro" é o tier novo de R$ 39,90 (ainda não vendido).

Convenção: valor None em limites numéricos significa "ilimitado".
"""

from typing import TypedDict


class PlanLimits(TypedDict):
    pockets_max: int | None
    cards_max: int | None
    history_days: int | None
    history_current_month_only: bool     # Grátis: só o mês-calendário corrente
    investments_enabled: bool
    ofx_enabled: bool
    export_enabled: bool
    ai_conversational_enabled: bool
    ai_categorization_enabled: bool
    recurring_expenses_enabled: bool
    # ── v2 (escada de 4 tiers) ──────────────────────────────────────────────
    launches_month_max: int | None      # lançamentos manuais por mês-calendário
    ai_monthly_messages: int | None     # None = limite global (AI_CHAT_MONTHLY_LIMIT)
    audio_enabled: bool                 # transcrição de áudio no WhatsApp
    image_ocr_enabled: bool             # leitura de cupom/comprovante por IA
    bills_enabled: bool                 # agenda de boletos / contas a pagar
    of_banks_max: int | None            # bancos conectáveis no Open Finance (0 = sem OF)
    agents_max: int | None              # agentes do Piggy ativos (prateleira futura)


# Ordem canônica da escada. Comparações de tier usam este ranking.
TIER_ORDER: dict[str, int] = {"free": 0, "essencial": 1, "plus": 2, "pro": 3}

# Custo de infraestrutura de Open Finance: o provedor (Pluggy) cobra por conexão
# (item) ATIVA por mês. Valor de contrato (2026-08-12): R$ 5,00/conexão/mês.
# Usado no dimensionamento de margem do Premium/Modo Família (ver
# docs/premium_family_plan_design.md, seção 3). Não entra em nenhum gate — é
# parâmetro de custo, não de limite. Atualizar aqui se o contrato mudar.
PLUGGY_COST_PER_CONNECTION_BRL: int = 5

# Tier mínimo POR AGENTE (escada v2). Prateleira começa no Plus — Grátis e
# Essencial têm 0 agentes (decisão 2026-08-06). Fase A = Plus+; Fase B
# (detetive/cofre/barao, quando existirem) = Pro+. Kind não mapeado cai em
# "pro" por segurança. Fonte única: rotas E runners leem daqui.
AGENT_KIND_MIN_TIER: dict[str, str] = {
    "xerife": "plus",
    "reporter": "plus",
    "carteiro": "plus",
    "detetive": "pro",   # Fase B: caça-assinaturas (tier Pro/pro_max)
    "cofre": "pro",      # Fase B: Banqueiro (aporte na caixinha OF → meta)
    "barao": "pro",      # Fase B: Barão (dinheiro parado vs CDI)
}


# Escada FINAL v3 (2026-08-06): Grátis R$0 · Essencial 9,90 · Plus 19,90 ·
# Pro 49,90 (valor 'pro_max' no banco) · Premium engavetado ("em breve", sem
# tier no código). Bancos OF: 0 / 1 / 2 / 5. Agentes: 0/0/3/6.
# Histórico (2026-08-06): Grátis só o mês corrente · Essencial 90d · Plus 12m ·
# Pro 24m. O trial (30d) é uma assinatura Stripe do plano escolhido (com cartão)
# — durante ele o usuário tem os limites do tier que assinou, não do Grátis.
FREE_LIMITS: PlanLimits = {
    "pockets_max": 1,
    "cards_max": 1,
    "history_days": 31,                  # teto de segurança; o corte real é o mês corrente
    "history_current_month_only": True,  # Grátis só enxerga o mês-calendário atual
    "investments_enabled": False,
    "ofx_enabled": False,
    "export_enabled": False,
    "ai_conversational_enabled": True,   # v2: tem cota pequena (v1 ignora — gate é is_pro)
    "ai_categorization_enabled": True,
    "recurring_expenses_enabled": False,
    "launches_month_max": 30,
    "ai_monthly_messages": 20,
    "audio_enabled": False,
    "image_ocr_enabled": False,
    "bills_enabled": False,
    "of_banks_max": 0,                   # Grátis não tem Open Finance (trial usa o tier assinado)
    "agents_max": 0,                     # Grátis não tem agentes (trial usa o tier assinado)
}


ESSENCIAL_LIMITS: PlanLimits = {
    "pockets_max": None,
    "cards_max": None,
    "history_days": 90,                  # 90 dias
    "history_current_month_only": False,
    "investments_enabled": True,
    "ofx_enabled": True,
    "export_enabled": True,
    "ai_conversational_enabled": True,
    "ai_categorization_enabled": True,
    "recurring_expenses_enabled": True,
    "launches_month_max": None,
    "ai_monthly_messages": 200,          # "uso justo"
    "audio_enabled": True,
    "image_ocr_enabled": True,
    "bills_enabled": True,
    "of_banks_max": 1,                   # 1 conexão viva (pode trocar de banco)
    "agents_max": 0,                     # prateleira de agentes começa no Plus
}


PLUS_LIMITS: PlanLimits = {
    "pockets_max": None,
    "cards_max": None,
    "history_days": 365,                 # 12 meses
    "history_current_month_only": False,
    "investments_enabled": True,
    "ofx_enabled": True,
    "export_enabled": True,
    "ai_conversational_enabled": True,
    "ai_categorization_enabled": True,
    "recurring_expenses_enabled": True,
    "launches_month_max": None,
    "ai_monthly_messages": None,
    "audio_enabled": True,
    "image_ocr_enabled": True,
    "bills_enabled": True,
    "of_banks_max": 2,
    "agents_max": 3,                     # Xerife + Repórter + Carteiro
}


PRO_LIMITS: PlanLimits = {
    **PLUS_LIMITS,
    "history_days": 730,                 # 24 meses
    "of_banks_max": 5,
    "agents_max": 6,                     # + Detetive, Banqueiro (kind "cofre"), Barão
}


_TIER_LIMITS: dict[str, PlanLimits] = {
    "free": FREE_LIMITS,
    "essencial": ESSENCIAL_LIMITS,
    "plus": PLUS_LIMITS,
    "pro": PRO_LIMITS,
}


def limits_for(plan: str) -> PlanLimits:
    """Compat v1: 'pro' (valor legado no banco) ganha tudo; resto cai no FREE.
    No v2 use `limits_for_tier` com o tier resolvido por get_plan_tier."""
    return PLUS_LIMITS if (plan or "").lower() == "pro" else FREE_LIMITS


def limits_for_tier(tier: str) -> PlanLimits:
    return _TIER_LIMITS.get((tier or "").lower(), FREE_LIMITS)


def tier_at_least(tier: str, minimum: str) -> bool:
    return TIER_ORDER.get((tier or "").lower(), 0) >= TIER_ORDER.get(minimum, 0)


class PlanLimitExceeded(Exception):
    """Usuário atingiu o limite de uma feature do plano. Carrega `feature` pra
    UIs decidirem o que mostrar (badge, modal, mensagem amigável) e `message`
    pronta pra canais texto (bot WhatsApp, IA conversacional)."""

    def __init__(self, feature: str, message: str):
        super().__init__(message)
        self.feature = feature
        self.message = message
