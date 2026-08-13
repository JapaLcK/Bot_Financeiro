"""
Helpers de plano. Lê o estado canônico de auth_accounts via db.get_auth_user e
expõe checagens simples para o resto do app.

Dois mundos atrás do flag PLANS_V2_ENABLED (lido dinâmico, sem redeploy):

  • OFF (default, produção atual): binário Free × Pro + paywall obrigatório
    (PAYWALL_ENABLED). Comportamento 100% preservado.
  • ON (escada v2): 4 tiers free < essencial < plus < pro. O valor 'pro' no
    banco é ALIAS LEGADO do tier plus (R$ 19,90 — antigo "Pro", hoje "Plus");
    o tier pro novo (R$ 39,90) usa o valor 'pro_max'. Grátis entra no app
    (has_app_access sempre True) e os gates viram por-feature/por-tier.
    Trial (2026-08-06): 30 dias do PLANO ESCOLHIDO, via Stripe COM CARTÃO
    (subscription trialing → cobra no dia 31). Escolheu Pro? 30 dias de Pro.
    Nasce no checkout, não no cadastro; 1 por telefone na vida (plan_trials).
    Durante o trial o tier vem da coluna `plan` (assinatura vigente), com os
    limites cheios do plano assinado — cadastro novo sem assinatura = Grátis.

A FastAPI dependency `require_pro_feature` vive em
frontend/finance_bot_websocket_custom.py para evitar import circular com
_get_current_user — ela usa os helpers daqui.
"""

import os
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from db import get_auth_user

from .plan_limits import (
    PlanLimits,
    PlanLimitExceeded,
    limits_for,
    limits_for_tier,
    tier_at_least,
)

# Valores aceitos na coluna auth_accounts.plan e o tier v2 correspondente.
_STORED_PLAN_TO_TIER = {
    "free": "free",
    "essencial": "essencial",
    "pro": "plus",       # legado: assinante de R$ 19,90 (rebatizado Plus)
    "plus": "plus",
    "pro_max": "pro",    # tier novo de R$ 39,90 (ainda não vendido)
}

TRIAL_DAYS_DEFAULT = 30


def plans_v2_enabled() -> bool:
    """Escada de tiers + trial via Stripe. LANÇADA 2026-08-06: default LIGADO
    (decisão do Lucas — sem flag de rollout; a /precos é hardcoded na escada).
    A env virou só FREIO DE EMERGÊNCIA: PLANS_V2_ENABLED=0/false desliga o
    comportamento v2 do backend sem redeploy se algo der errado."""
    raw = (os.getenv("PLANS_V2_ENABLED") or "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    return True


def trial_days_total() -> int:
    try:
        return int(os.getenv("PLANS_TRIAL_DAYS", str(TRIAL_DAYS_DEFAULT)))
    except ValueError:
        return TRIAL_DAYS_DEFAULT


def _paid_plan_active(user: dict) -> bool:
    """Assinatura paga (qualquer tier) vigente? Defensivo: se plan_expires_at
    já passou mas o webhook não rebaixou ainda (evento perdido), trata como
    expirado. plan_expires_at NULL = vitalício (grandfathered)."""
    expires_at = user.get("plan_expires_at")
    if expires_at is None:
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at > datetime.now(timezone.utc)


def get_trial_status(user_id: int, user: dict | None = None) -> dict:
    """{"active": bool, "days_left": int, "started_at": datetime|None}.

    Fonte: auth_accounts.trial_started_at (ancorado por telefone via
    db.plans.claim_trial_for_user — 30 dias únicos por phone_hash, na vida;
    cartão não zera nem estica)."""
    started = None
    if user is not None and "trial_started_at" in user:
        started = user.get("trial_started_at")
        if started is not None and started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
    else:
        from db.plans import get_trial_started_at
        started = get_trial_started_at(int(user_id))
    if started is None:
        return {"active": False, "days_left": 0, "started_at": None}
    ends_at = started + timedelta(days=trial_days_total())
    now = datetime.now(timezone.utc)
    if ends_at <= now:
        return {"active": False, "days_left": 0, "started_at": started}
    remaining = ends_at - now
    days_left = remaining.days + (1 if (remaining.seconds or remaining.microseconds) else 0)
    return {"active": True, "days_left": days_left, "started_at": started}


def get_plan_tier(user_id: int) -> str:
    """Tier efetivo do usuário na escada v2: free | essencial | plus | pro.

    Assinatura vigente manda — inclui o TRIAL, que hoje é uma assinatura Stripe
    do plano escolhido (status trialing, plan_expires_at no futuro = fim do
    trial), então o tier vem naturalmente da coluna `plan`. Sem assinatura
    vigente = free (cadastro novo entra Grátis; não há mais trial sem cartão).
    Com PLANS_V2_ENABLED off, colapsa no binário legado (pro→plus, resto free)
    pra quem chamar isto cedo demais não ver nada diferente do is_pro."""
    user = get_auth_user(int(user_id))
    if not user:
        return "free"
    stored = (user.get("plan") or "").lower()
    tier = _STORED_PLAN_TO_TIER.get(stored, "free")
    if tier != "free" and _paid_plan_active(user):
        return tier
    return "free"


def is_pro(user_id: int) -> bool:
    """
    True se o usuário tem o plano pago principal ativo (Plus, ex-"Pro" — inclui
    o trial do Stripe, que hoje é uma assinatura trialing do plano escolhido, e
    o tier pro acima).

    Semântica preservada pros 27 call sites: "posso usar as features pagas?".
    """
    if plans_v2_enabled():
        return tier_at_least(get_plan_tier(int(user_id)), "plus")
    user = get_auth_user(int(user_id))
    if not user:
        return False
    plan = (user.get("plan") or "").lower()
    if plan != "pro":
        return False
    return _paid_plan_active(user)


# user_ids sempre liberados (admin/teste), mesmo sem assinatura.
_ACCESS_ALLOWLIST = {88648360, 832398038}


def paywall_enabled() -> bool:
    """Gate de assinatura obrigatória, lido dinamicamente do ambiente pra
    ligar/desligar sem redeploy. Default: DESLIGADO (comportamento legado).
    Liga setando PAYWALL_ENABLED=true no ambiente — só DEPOIS do backfill."""
    return (os.getenv("PAYWALL_ENABLED") or "").strip().lower() in ("1", "true", "yes", "on")


# Allowlist padrão do beta de Agentes (usada quando AGENTS_BETA_EMAILS não está
# setado no ambiente). Lançamento em fases: primeiro só o teste, depois geral.
_AGENTS_BETA_EMAILS_DEFAULT = {"lucaskuramoti06@gmail.com", "hiagojo2016@gmail.com"}


def agents_beta_tester(user_id: int, email: str | None = None) -> bool:
    """True SÓ pros testers explícitos do beta — e-mail no allowlist
    AGENTS_BETA_EMAILS (default = e-mails de teste) OU id em AGENTS_BETA_USER_IDS.
    IGNORA o flag global AGENTS_UI_ENABLED de propósito: é o que libera TODOS os
    agentes (qualquer tier) só pra quem testa, sem afetar o público quando a
    feature abrir geral."""
    raw = os.getenv("AGENTS_BETA_EMAILS")
    if raw is None:
        beta_emails = _AGENTS_BETA_EMAILS_DEFAULT
    else:
        beta_emails = {e.strip().lower() for e in raw.split(",") if e.strip()}
    if email is None:
        try:
            from db import get_user_email
            email = get_user_email(user_id)
        except Exception:
            email = None
    if email and str(email).strip().lower() in beta_emails:
        return True
    beta_ids = {i.strip() for i in (os.getenv("AGENTS_BETA_USER_IDS") or "").split(",") if i.strip()}
    return str(user_id) in beta_ids


def agents_ui_enabled(user_id: int, email: str | None = None) -> bool:
    """Gate da feature de Agentes. LANÇADO pra todos por padrão — quem decide
    Free vs pago é o gate por tier (agent_kind_allowed/require_min_tier), não este.
    AGENTS_UI_ENABLED=0 é o freio de emergência: volta pro modo beta (só os
    testers da allowlist — ver agents_beta_tester)."""
    if (os.getenv("AGENTS_UI_ENABLED") or "").strip().lower() in ("0", "false", "no", "off"):
        return agents_beta_tester(user_id, email)
    return True


# Allowlist padrão do beta da lista completa de bancos (modal com busca). Mesma
# mecânica dos Agentes/OF: lançamento em fases, primeiro só os e-mails de teste.
_BANK_LIST_BETA_EMAILS_DEFAULT = {"lucaskuramoti06@gmail.com", "hiagojo2016@gmail.com"}


def bank_list_ui_enabled(user_id: int, email: str | None = None) -> bool:
    """Gate beta do modal 'Ver todos os bancos' (lista completa da Pluggy). Liga se:
    - OF_BANK_LIST_ENABLED global ligado (lançamento pra todos), OU
    - o e-mail está no allowlist OF_BANK_LIST_BETA_EMAILS (default = e-mails de teste), OU
    - o user_id está em OF_BANK_LIST_BETA_USER_IDS.
    Default: SÓ os e-mails de teste veem o modal; o resto mantém a lista curta de sempre."""
    if (os.getenv("OF_BANK_LIST_ENABLED") or "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    raw = os.getenv("OF_BANK_LIST_BETA_EMAILS")
    if raw is None:
        beta_emails = _BANK_LIST_BETA_EMAILS_DEFAULT
    else:
        beta_emails = {e.strip().lower() for e in raw.split(",") if e.strip()}
    if email is None:
        try:
            from db import get_user_email
            email = get_user_email(user_id)
        except Exception:
            email = None
    if email and str(email).strip().lower() in beta_emails:
        return True
    beta_ids = {i.strip() for i in (os.getenv("OF_BANK_LIST_BETA_USER_IDS") or "").split(",") if i.strip()}
    return str(user_id) in beta_ids


# Allowlist padrão do beta do saldo consolidado (Carteira + bancos OF) no bot/
# relatórios/chat IA. Mesma mecânica dos Agentes: primeiro só o teste, depois geral.
_CONSOLIDATED_BETA_EMAILS_DEFAULT = {"lucaskuramoti06@gmail.com", "hiagojo2016@gmail.com"}


def consolidated_balance_enabled(user_id: int, email: str | None = None) -> bool:
    """Gate do saldo consolidado nas superfícies de leitura (WhatsApp /saldo,
    resumos, chat IA, Discord). LANÇADO pra todos por padrão: quem tem banco OF
    conectado vê Carteira + bancos somados, o resto segue só a Carteira manual.
    OF_CONSOLIDATED_BALANCE_ENABLED=0 é o freio de emergência: volta pro beta
    (só a allowlist de e-mail/id). O dashboard web não passa por aqui (já somava
    os bancos antes deste gate)."""
    if (os.getenv("OF_CONSOLIDATED_BALANCE_ENABLED") or "").strip().lower() not in ("0", "false", "no", "off"):
        return True
    # Freio puxado: volta pro comportamento de beta (só a allowlist).
    raw = os.getenv("OF_CONSOLIDATED_BETA_EMAILS")
    if raw is None:
        beta_emails = _CONSOLIDATED_BETA_EMAILS_DEFAULT
    else:
        beta_emails = {e.strip().lower() for e in raw.split(",") if e.strip()}
    if email is None:
        try:
            from db import get_user_email
            email = get_user_email(user_id)
        except Exception:
            email = None
    if email and str(email).strip().lower() in beta_emails:
        return True
    beta_ids = {i.strip() for i in (os.getenv("OF_CONSOLIDATED_BETA_USER_IDS") or "").split(",") if i.strip()}
    return str(user_id) in beta_ids


def has_app_access(user_id: int) -> bool:
    """True se o usuário pode entrar no app.

    v2 ON: sempre True — o Grátis entra no app e os limites são por feature
    (o paywall binário dá lugar à escada).
    v2 OFF: com o paywall ligado, exige assinatura ativa/trial (is_pro) —
    menos a allowlist de admin/teste. Paywall desligado libera todo mundo."""
    if plans_v2_enabled():
        return True
    if not paywall_enabled():
        return True
    if int(user_id) in _ACCESS_ALLOWLIST:
        return True
    return is_pro(int(user_id))


def needs_plan_selection(user_id: int, user: dict | None = None) -> bool:
    """True se o cadastro ainda NÃO escolheu um plano na /precos.

    Regra de produto (2026-08-11): depois de criar a conta, o usuário é obrigado
    a passar pela /precos e escolher um plano — mesmo o Grátis — antes de entrar
    no dashboard. `plan_selected_at` (auth_accounts) marca essa escolha.

    Nunca trava quem já tem assinatura paga/trial vigente (escolha implícita no
    checkout) nem contas antigas (backfill em schema.py). Com o v2 desligado
    (freio de emergência) o gate fica dormente — o fluxo legado do paywall vale.
    """
    if not plans_v2_enabled():
        return False
    if user is None:
        user = get_auth_user(int(user_id))
    if not user:
        return False
    if user.get("plan_selected_at"):
        return False
    # Assinante pago/trial ativo já escolheu implicitamente no checkout.
    stored = (user.get("plan") or "").lower()
    tier = _STORED_PLAN_TO_TIER.get(stored, "free")
    if tier != "free" and _paid_plan_active(user):
        return False
    return True


def get_user_limits(user_id: int) -> PlanLimits:
    """Retorna os limites/features aplicáveis a este usuário.

    v2 ON: limites do tier efetivo. O trial é uma assinatura do plano escolhido,
    então recebe os limites cheios desse plano (ex.: trial de Pro = 5 bancos no
    Open Finance) — sem cap especial de trial.
    """
    if not plans_v2_enabled():
        return limits_for("pro" if is_pro(user_id) else "free")
    return limits_for_tier(get_plan_tier(user_id))


def history_days_cap(user_id: int) -> int | None:
    """Janela de histórico do plano em dias (None = ilimitado). v1: Free 30d /
    Pro ilimitado (inalterado). v2: Grátis mês corrente (ver
    history_current_month_only) / Essencial 90d / Plus 365d / Pro 730d."""
    return get_user_limits(user_id)["history_days"]


def history_current_month_only(user_id: int) -> bool:
    """True se o tier só enxerga o mês-calendário corrente (Grátis). Os
    endpoints de histórico cortam no dia 1 do mês em vez de usar history_days."""
    if not plans_v2_enabled():
        return False
    return bool(get_user_limits(user_id).get("history_current_month_only", False))


def history_earliest_date(user_id: int, now: datetime | None = None) -> date | None:
    """Primeiro dia que o tier pode consultar; None significa sem limite."""
    limits = get_user_limits(user_id)
    current = now or datetime.now(timezone.utc)
    try:
        local_now = current.astimezone(ZoneInfo(os.getenv("TZ", "America/Sao_Paulo")))
    except ZoneInfoNotFoundError:
        local_now = current.astimezone(timezone.utc)

    if limits.get("history_current_month_only", False):
        return local_now.date().replace(day=1)
    days = limits.get("history_days")
    return None if days is None else local_now.date() - timedelta(days=int(days))


def history_months_cap(user_id: int) -> int | None:
    days = history_days_cap(user_id)
    return None if days is None else max(1, days // 30)


def feature_enabled(user_id: int, key: str) -> bool:
    """Gate booleano de feature por tier (ex.: 'audio_enabled',
    'image_ocr_enabled'). Com v2 off cai no binário legado is_pro."""
    if not plans_v2_enabled():
        return is_pro(user_id)
    return bool(get_user_limits(user_id).get(key, False))


def agents_energy_budget(user_id: int) -> int:
    """Orçamento de energia do tier pra ativar agentes (0 = sem agentes).

    Modelo 2026-08-13: todos os agentes ficam liberados em Plus e Pro; quem
    limita quantos você ativa é a energia. Plus 4 / Pro 12 / Grátis e Essencial 0.
    Com v2 OFF devolve o orçamento do Plus pra quem é pago (is_pro), senão 0 —
    espelhando a semântica legada (pago ativa agentes, grátis não)."""
    from .plan_limits import PLUS_LIMITS
    if not plans_v2_enabled():
        return PLUS_LIMITS["agents_energy_budget"] if is_pro(user_id) else 0
    return int(get_user_limits(user_id).get("agents_energy_budget", 0))


def agent_kind_allowed(user_id: int, kind: str) -> bool:
    """O tier do usuário dá direito a AGENTES? (modelo de energia: Plus/Pro sim,
    Grátis/Essencial não). Sem trava por kind — todos os agentes ficam liberados
    nos tiers com energia; quem limita quantos é o orçamento (checado na
    ativação). O `kind` fica na assinatura por compat com os call sites.

    Com v2 OFF devolve True — o gate legado (Free 1 / Plus+ todos) mora na
    rota de ativação e os runners não filtravam por plano no v1.
    Usado pelos RUNNERS também: agente ativado no trial para de disparar
    quando o usuário cai pro Grátis (orçamento 0)."""
    if not plans_v2_enabled():
        return True
    # Testers do beta usam QUALQUER agente, independe do tier — pra o plano de
    # teste (Lucas/Hiago) conseguir ver todos os agentes sem precisar assinar.
    # Não afeta o público: agents_beta_tester ignora o flag global e só casa com
    # o allowlist explícito.
    if agents_beta_tester(int(user_id)):
        return True
    return agents_energy_budget(int(user_id)) > 0


def require_min_tier(user_id: int, minimum: str) -> bool:
    """True se o tier efetivo do usuário é >= minimum ('essencial'|'plus'|'pro').
    Com v2 off, cai na semântica legada (is_pro pra qualquer exigência paga)."""
    if not plans_v2_enabled():
        return is_pro(user_id)
    return tier_at_least(get_plan_tier(user_id), minimum)


def ai_monthly_limit_for(user_id: int) -> int:
    """Cota mensal de mensagens da IA pro usuário (sempre um int; tiers sem
    cota própria caem no limite global AI_CHAT_MONTHLY_LIMIT)."""
    from .ai_chat_commands import AI_CHAT_MONTHLY_LIMIT
    per_tier = get_user_limits(user_id).get("ai_monthly_messages")
    return AI_CHAT_MONTHLY_LIMIT if per_tier is None else min(per_tier, AI_CHAT_MONTHLY_LIMIT)


def ai_chat_allowed(user_id: int) -> bool:
    """Pode falar com a Piggy agora? v1: só Pro. v2: todo tier tem IA — a cota
    mensal é quem limita (checada dentro do chat via monthly_limit)."""
    if not plans_v2_enabled():
        return is_pro(user_id)
    if not get_user_limits(user_id)["ai_conversational_enabled"]:
        return False
    from db.ai_chat import get_usage_this_month
    return get_usage_this_month(int(user_id)) < ai_monthly_limit_for(user_id)


def check_can_create_launch(user_id: int) -> None:
    """Levanta PlanLimitExceeded se o tier tem teto mensal de lançamentos
    manuais e ele foi atingido. No-op com v2 off (v1 não tinha esse limite)."""
    if not plans_v2_enabled():
        return
    cap = get_user_limits(user_id)["launches_month_max"]
    if cap is None:
        return
    from db.plans import count_launches_this_month
    if count_launches_this_month(user_id) >= cap:
        raise PlanLimitExceeded(
            "launches_month",
            f"🐷 No Grátis você registra {cap} lançamentos por mês — e esse mês "
            "lotou! No Essencial é ilimitado (com áudio, foto de cupom e OFX).\n"
            "Faça upgrade: https://pigbankai.com/precos",
        )


def check_can_create_pocket(user_id: int) -> None:
    """Levanta PlanLimitExceeded se já atingiu o limite de caixinhas do tier.
    Chamado do DB layer pra blindar TODOS os canais (HTTP, bot, IA)."""
    limits = get_user_limits(user_id)
    pockets_max = limits["pockets_max"]
    if pockets_max is None:
        return
    from db.pockets import list_pockets
    if len(list_pockets(user_id)) >= pockets_max:
        raise PlanLimitExceeded(
            "pockets_unlimited",
            f"🐷 No seu plano você cria {pockets_max} caixinha. "
            "Com um plano pago é ilimitado — separe sua reserva, viagens, "
            "presentes…\nFaça upgrade: https://pigbankai.com/precos",
        )


def check_can_create_card(user_id: int) -> None:
    """Levanta PlanLimitExceeded se já atingiu o limite de cartões do tier."""
    limits = get_user_limits(user_id)
    cards_max = limits["cards_max"]
    if cards_max is None:
        return
    from db.cards import list_cards
    if len(list_cards(user_id)) >= cards_max:
        raise PlanLimitExceeded(
            "cards_unlimited",
            f"🐷 No seu plano você adiciona {cards_max} cartão. "
            "Com um plano pago você organiza todos eles em um lugar só.\n"
            "Faça upgrade: https://pigbankai.com/precos",
        )
