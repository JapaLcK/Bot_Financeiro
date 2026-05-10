"""
core/services/billing_commands.py — handlers para comandos de assinatura.

Comandos suportados (case-insensitive, sem acentos):
- "assinar", "fazer upgrade", "upgrade", "quero pro", "pigbank+", "pigbank plus"
- "cancelar", "cancelar assinatura", "encerrar assinatura"
- "plano", "meu plano", "minha assinatura", "ver plano"

Compartilhado entre Discord (cog) e WhatsApp (handle_incoming),
pra resposta consistente entre canais.
"""

from __future__ import annotations

import unicodedata
from datetime import datetime, timezone

from core.dashboard_links import build_dashboard_link


_ASSINAR_TRIGGERS = {
    "assinar", "fazer upgrade", "upgrade", "quero pro", "quero o pro",
    "pigbank+", "pigbank plus", "assinatura", "virar pro", "pagar",
}
_CANCELAR_TRIGGERS = {
    "cancelar", "cancelar assinatura", "cancelar pro", "cancelar pigbank+",
    "cancelar plano", "encerrar assinatura", "encerrar plano",
}
_PLANO_TRIGGERS = {
    "plano", "meu plano", "minha assinatura", "ver plano", "qual meu plano",
    "qual eh meu plano", "qual e meu plano",
}


def _normalize(text: str) -> str:
    """Lowercase + remove acentos + colapsa espacos."""
    if not text:
        return ""
    t = text.strip().lower()
    t = "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn")
    return " ".join(t.split())


def _bold(s: str, platform: str) -> str:
    return f"*{s}*" if platform == "whatsapp" else f"**{s}**"


def _format_plan_expires(expires_at) -> str:
    if not expires_at:
        return "sem data definida"
    if isinstance(expires_at, str):
        try:
            expires_at = datetime.fromisoformat(expires_at)
        except Exception:
            return str(expires_at)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    # Converte pra horario de Brasilia (UTC-3) sem precisar de pytz
    local = expires_at.astimezone(timezone.utc)
    # Usa offset fixo BRT (UTC-3); suficiente pra exibir no chat.
    from datetime import timedelta
    brt = local.astimezone(timezone(timedelta(hours=-3)))
    return brt.strftime("%d/%m/%Y às %H:%M")


def _handle_assinar(user_id: int, platform: str) -> str:
    from core.services.plan_service import is_pro
    from db import get_auth_user

    if is_pro(user_id):
        user = get_auth_user(user_id)
        expires = _format_plan_expires((user or {}).get("plan_expires_at"))
        link = build_dashboard_link(user_id, hours=1.0, next_path="/conta") or "https://pigbankai.com/conta"
        b = lambda s: _bold(s, platform)
        return (
            f"🐷 Você já tá no {b('PigBank+')}!\n\n"
            f"Próxima renovação: {b(expires)}\n\n"
            f"Pra ver detalhes ou cancelar:\n{link}"
        )

    link = build_dashboard_link(user_id, hours=1.0, next_path="/precos")
    if not link:
        return (
            "🐷 Ih, não consegui gerar o link agora. Tenta de novo em uns segundos "
            "ou abre direto pigbankai.com/precos no navegador."
        )
    b = lambda s: _bold(s, platform)
    return (
        f"🐷✨ Bora pro {b('PigBank+')}?\n\n"
        f"Aqui ó, link pra assinar com {b('7 dias grátis')} (cancela quando quiser, sem cobrança no trial):\n"
        f"{link}\n\n"
        f"Esse link é só seu e expira em 1h."
    )


def _handle_cancelar(user_id: int, platform: str) -> str:
    from core.services.plan_service import is_pro

    if not is_pro(user_id):
        return "🐷 Você tá no plano Free — não tem o que cancelar. Tá tudo de graça mesmo."

    link = build_dashboard_link(user_id, hours=1.0, next_path="/conta")
    if not link:
        return (
            "🐷 Não consegui gerar o link agora. Tenta de novo em uns segundos "
            "ou abre direto pigbankai.com/conta no navegador."
        )
    b = lambda s: _bold(s, platform)
    return (
        f"🐷 Quer cancelar? Sem hard feelings.\n\n"
        f"Abre esse link pra gerenciar sua assinatura no portal da Stripe:\n{link}\n\n"
        f"Você continua com acesso {b('até o fim do período já pago')} — "
        f"depois o plano volta pra Free automaticamente."
    )


def _handle_plano(user_id: int, platform: str) -> str:
    from core.services.plan_service import is_pro
    from db import get_auth_user

    user = get_auth_user(user_id) or {}
    plan = (user.get("plan") or "free").lower()
    status = user.get("last_payment_status") or "—"
    expires = user.get("plan_expires_at")
    b = lambda s: _bold(s, platform)

    if not is_pro(user_id):
        return (
            f"🐷 Plano: {b('Free')}\n\n"
            f"O que vem aqui:\n"
            f"• 1 caixinha e 1 cartão de crédito\n"
            f"• Histórico dos últimos 30 dias\n"
            f"• Lançamentos manuais por chat e dashboard\n\n"
            f"Quer ver o que rola no PigBank+? Manda {b('assinar')} 🐷✨"
        )

    expires_fmt = _format_plan_expires(expires)
    status_label = {
        "trialing": "Trial em andamento (7 dias grátis)",
        "active": "Ativo",
        "past_due": "Pagamento em atraso",
        "canceled": "Cancelado",
        "unpaid": "Não pago",
        "inactive": "Inativo",
    }.get(status, status)

    next_label = "Próxima renovação" if status == "active" else (
        "Fim do trial / primeira cobrança" if status == "trialing" else "Expira em"
    )

    return (
        f"🐷 Plano: {b('PigBank+')}\n\n"
        f"Status: {status_label}\n"
        f"{next_label}: {b(expires_fmt)}\n\n"
        f"Pra cancelar: manda {b('cancelar')}"
    )


def handle_billing_command(user_id: int, text: str, platform: str = "whatsapp") -> str | None:
    """
    Retorna a resposta pro comando de billing, ou None se nao for um comando reconhecido.
    `platform` controla a formatacao (negrito etc): "whatsapp" ou "discord".
    """
    norm = _normalize(text)
    if not norm:
        return None

    # Aceita prefixo "/" do Discord (ex: "/assinar")
    if norm.startswith("/"):
        norm = norm[1:].strip()

    if norm in _ASSINAR_TRIGGERS:
        return _handle_assinar(user_id, platform)
    if norm in _CANCELAR_TRIGGERS:
        return _handle_cancelar(user_id, platform)
    if norm in _PLANO_TRIGGERS:
        return _handle_plano(user_id, platform)
    return None
