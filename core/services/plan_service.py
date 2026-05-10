"""
Helpers de plano (Free vs Pro). Lê o estado canônico de auth_accounts via
db.get_auth_user e expõe checagens simples para o resto do app.

A FastAPI dependency `require_pro_feature` vive em
frontend/finance_bot_websocket_custom.py para evitar import circular com
_get_current_user — ela usa is_pro daqui.
"""

from datetime import datetime, timezone

from db import get_auth_user

from .plan_limits import PlanLimits, limits_for


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
