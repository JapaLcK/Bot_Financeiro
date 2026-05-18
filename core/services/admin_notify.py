"""
core/services/admin_notify.py — Notificações administrativas via webhook.

Pra alertar Lucas (e futuros operadores) sobre eventos críticos do produto:
novos Pros, falhas de pagamento, etc. Auto-detecta provider pelo URL.

Configuração:
  - ADMIN_NOTIFY_WEBHOOK_URL: URL de webhook (Slack ou Discord — auto-detecta).
    - Slack:   https://hooks.slack.com/services/T.../B.../...
    - Discord: https://discord.com/api/webhooks/{id}/{token}

  Se a env estiver vazia, todas as chamadas viram no-op silencioso (não
  bloqueia o fluxo principal e não loga warning ruidoso).

Quem chama: webhooks do Stripe e similares — sempre via fire-and-forget pra
não pesar no request crítico (usar asyncio.to_thread no caller).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any

import requests

logger = logging.getLogger(__name__)

_WEBHOOK_URL_ENV = "ADMIN_NOTIFY_WEBHOOK_URL"
_REQUEST_TIMEOUT_SECONDS = 5.0


def _detect_provider(url: str) -> str:
    """Decide o formato do payload pelo host da URL."""
    if "hooks.slack.com" in url:
        return "slack"
    if "discord.com/api/webhooks" in url or "discordapp.com/api/webhooks" in url:
        return "discord"
    # default: Discord-style (mais comum no projeto)
    return "discord"


def _build_payload(provider: str, message: str) -> dict[str, Any]:
    if provider == "slack":
        return {"text": message}
    # Discord: aceita 'content' (texto plain) ou 'embeds'.
    # Usamos content simples — Discord respeita markdown limitado.
    return {"content": message[:2000]}  # Discord limita content em 2000 chars


def _send(message: str) -> bool:
    """Envia mensagem pro webhook. Retorna True se 2xx. Falha silenciosa."""
    url = (os.getenv(_WEBHOOK_URL_ENV) or "").strip()
    if not url:
        return False
    try:
        provider = _detect_provider(url)
        payload = _build_payload(provider, message)
        resp = requests.post(url, json=payload, timeout=_REQUEST_TIMEOUT_SECONDS)
        if 200 <= resp.status_code < 300:
            return True
        logger.warning(
            "[admin_notify] webhook %s respondeu %s: %s",
            provider, resp.status_code, resp.text[:200],
        )
        return False
    except Exception as exc:
        logger.warning("[admin_notify] falha ao notificar: %s", exc, exc_info=True)
        return False


# ──────────────────────────────────────────────────────────────────────────────
# Eventos suportados
# ──────────────────────────────────────────────────────────────────────────────

def _mask_email(email: str | None) -> str:
    """`lucaskuramoti06@gmail.com` → `lck***@gmail.com` (privacidade no canal)."""
    if not email or "@" not in email:
        return email or "(sem email)"
    user, domain = email.split("@", 1)
    if len(user) <= 3:
        return f"{user}@{domain}"
    return f"{user[:3]}***@{domain}"


def notify_new_pro(
    *,
    user_id: int,
    email: str | None,
    plan: str = "pro",
    status: str = "trialing",
    expires_at: datetime | None = None,
    interval: str | None = None,
) -> bool:
    """Notifica admin de novo Pro (trial ou pago). Chamado de
    checkout.session.completed (Stripe webhook)."""
    when = expires_at.strftime("%d/%m/%Y") if expires_at else "sem data"
    interval_str = f" · {interval}" if interval else ""
    is_trial = (status or "").lower() == "trialing"
    icon = "🎉" if not is_trial else "🐷"
    label = "Novo PRO pagante" if not is_trial else "Novo trial PigBank+"
    message = (
        f"{icon} **{label}**\n"
        f"user `{user_id}` · {_mask_email(email)}{interval_str}\n"
        f"status: `{status}` · expira: {when}"
    )
    return _send(message)


def notify_payment_failed(
    *,
    user_id: int,
    email: str | None,
    attempt_count: int | None = None,
) -> bool:
    """Notifica admin sobre falha de pagamento Pro. Chamado de
    invoice.payment_failed (Stripe webhook)."""
    extra = f" · tentativa #{attempt_count}" if attempt_count else ""
    message = (
        f"⚠️ **Pagamento falhou**\n"
        f"user `{user_id}` · {_mask_email(email)}{extra}"
    )
    return _send(message)


def notify_subscription_canceled(
    *,
    user_id: int,
    email: str | None,
    expires_at: datetime | None = None,
) -> bool:
    """Notifica admin que um Pro cancelou (não bloqueia receita imediata,
    mas é sinal de churn)."""
    when = expires_at.strftime("%d/%m/%Y") if expires_at else "imediato"
    message = (
        f"😬 **Pro cancelou**\n"
        f"user `{user_id}` · {_mask_email(email)}\n"
        f"acesso Pro até: {when}"
    )
    return _send(message)
