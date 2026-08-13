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
    agents_max: int | None              # teto de agentes ativos (legado; hoje o limite real é a energia)
    agents_energy_budget: int           # orçamento de energia p/ ativar agentes (0 = sem agentes)


# Ordem canônica da escada. Comparações de tier usam este ranking.
TIER_ORDER: dict[str, int] = {"free": 0, "essencial": 1, "plus": 2, "pro": 3}

# Custo de ENERGIA de cada agente (modelo 2026-08-13). Calibrado por quanto o
# agente custa pra gente rodar: frequência + dependência de Open Finance + peso
# de processamento. Os leves (leitura mensal) custam 1; os pesados (varredura
# contínua de histórico OF) custam 3. Fonte única: rotas, runners e UI leem daqui.
#
# Regra dos planos (todos os agentes ficam liberados em Plus e Pro; o limitador
# é a energia): Plus tem orçamento 4 → cabem os 3 baratos (1+1+2), ou 2 se um
# for caro (⚡3). Pro tem 12 → cabem os 6 (1+1+2+2+3+3). Grátis/Essencial = 0.
AGENT_ENERGY_COST: dict[str, int] = {
    "reporter": 1,   # mensal, só lê o mês fechado
    "carteiro": 1,   # contínuo, mas só checa data de boleto
    "xerife": 2,     # diário + a cada sync, análise de anomalia
    "barao": 2,      # mensal, mas depende de Open Finance
    "detetive": 3,   # contínuo + varre 6 meses de histórico OF a cada sync
    "cofre": 3,      # contínuo + acompanha caixinhas OF a cada sync
}

# Custo default de kind não mapeado: o mais caro, por segurança.
_DEFAULT_AGENT_ENERGY_COST = 3


def agent_energy_cost(kind: str) -> int:
    """Energia que ativar este agente consome. Kind desconhecido → custo máximo."""
    return AGENT_ENERGY_COST.get(kind, _DEFAULT_AGENT_ENERGY_COST)


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
    "agents_energy_budget": 0,           # Grátis não tem energia p/ agentes
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
    "agents_energy_budget": 0,           # Essencial ainda não tem energia p/ agentes
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
    "agents_max": 3,                     # legado (headline "até 3"); o limite real é a energia
    "agents_energy_budget": 4,           # cabem os 3 baratos (1+1+2), ou 2 se um for caro (⚡3)
}


PRO_LIMITS: PlanLimits = {
    **PLUS_LIMITS,
    "history_days": 730,                 # 24 meses
    "of_banks_max": 5,
    "agents_max": 6,                     # legado; o limite real é a energia
    "agents_energy_budget": 12,          # cabem os 6 (1+1+2+2+3+3)
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
