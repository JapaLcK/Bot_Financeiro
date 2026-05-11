"""
Helpers de plano (Free vs Pro). Lê o estado canônico de auth_accounts via
db.get_auth_user e expõe checagens simples para o resto do app.

A FastAPI dependency `require_pro_feature` vive em
frontend/finance_bot_websocket_custom.py para evitar import circular com
_get_current_user — ela usa is_pro daqui.
"""

from datetime import datetime, timezone

from db import get_auth_user

from .plan_limits import PlanLimits, PlanLimitExceeded, limits_for


def is_pro(user_id: int) -> bool:
    """
    True se o usuário tem plano Pro ativo. Inclui usuários em trial (Stripe
    deixa plan='pro' durante o trial; quando o pagamento falha o webhook
    rebaixa para 'free').

    Defensivo: se plan_expires_at já passou mas o webhook não rebaixou ainda
    (evento perdido), trata como expirado.
    """
    user = get_auth_user(int(user_id))
    if not user:
        return False
    plan = (user.get("plan") or "").lower()
    if plan != "pro":
        return False
    expires_at = user.get("plan_expires_at")
    if expires_at is None:
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at > datetime.now(timezone.utc)


def get_user_limits(user_id: int) -> PlanLimits:
    """Retorna os limites/features aplicáveis a este usuário."""
    return limits_for("pro" if is_pro(user_id) else "free")


def check_can_create_pocket(user_id: int) -> None:
    """Levanta PlanLimitExceeded se Free e já atingiu o limite de caixinhas.
    Chamado do DB layer pra blindar TODOS os canais (HTTP, bot, IA)."""
    limits = get_user_limits(user_id)
    pockets_max = limits["pockets_max"]
    if pockets_max is None:
        return
    from db.pockets import list_pockets
    if len(list_pockets(user_id)) >= pockets_max:
        raise PlanLimitExceeded(
            "pockets_unlimited",
            f"🐷 No Free você cria {pockets_max} caixinha. "
            "Com PigBank+ é ilimitado — separe sua reserva, viagens, "
            "presentes…\nFaça upgrade: https://pigbankai.com/precos",
        )


def check_can_create_card(user_id: int) -> None:
    """Levanta PlanLimitExceeded se Free e já atingiu o limite de cartões."""
    limits = get_user_limits(user_id)
    cards_max = limits["cards_max"]
    if cards_max is None:
        return
    from db.cards import list_cards
    if len(list_cards(user_id)) >= cards_max:
        raise PlanLimitExceeded(
            "cards_unlimited",
            f"🐷 No Free você adiciona {cards_max} cartão. "
            "Com PigBank+ você organiza todos eles em um lugar só.\n"
            "Faça upgrade: https://pigbankai.com/precos",
        )
