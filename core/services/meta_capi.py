"""
core/services/meta_capi.py — Meta (Facebook) Conversions API (server-side).

Complementa o pixel client-side: o backend dispara os eventos de conversão
daqui, com dados precisos e um `event_id` compartilhado com o pixel do
navegador. O Meta deduplica eventos com mesmo (event_name, event_id) vindos do
pixel e da CAPI — então a conversão conta uma vez só, mesmo que os dois disparem.

Eventos enviados hoje (via `send_event`):
  - CompleteRegistration — no /auth/verify-email e no signup via Google.
  - StartTrial           — no checkout.session.completed com status=trialing.
  - Purchase             — compra imediata (checkout sem trial) e a cobrança
                           real de invoice.paid (fim do trial + renovações).

Por que server-side além do pixel:
  - Valor preciso da compra (o pixel client-side manda só a moeda) → ROAS real.
  - Imune a adblock / iOS-ATT / restrição de cookie (o pixel perde ~10-30%).
  - Dedup por event_id resolve disparos duplicados.

Configuração (ambas obrigatórias — se faltar qualquer uma, tudo vira no-op):
  - META_PIXEL_ID: mesmo ID usado no pixel client-side.
  - META_PIXEL_ACCESS_TOKEN: token da Conversions API (Events Manager →
    Configurações → Conversions API → Gerar token de acesso).

Quem chama: webhook do Stripe e os endpoints de cadastro, sempre via
asyncio.to_thread pra não pesar no request crítico. Falha silenciosa.
"""
from __future__ import annotations

import hashlib
import logging
import os

import requests

logger = logging.getLogger(__name__)

# Versão do Graph API. Estável e retrocompatível; subir quando necessário.
_GRAPH_VERSION = "v21.0"
_REQUEST_TIMEOUT_SECONDS = 10.0

_PIXEL_ID_ENV = "META_PIXEL_ID"
_ACCESS_TOKEN_ENV = "META_PIXEL_ACCESS_TOKEN"


def _sha256(value: str) -> str:
    """Hash exigido pelo Meta pros dados de contato (email, telefone, etc.)."""
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()


def capi_configured() -> bool:
    """True quando pixel + token estão setados (senão os envios são no-op)."""
    return bool(
        (os.getenv(_PIXEL_ID_ENV) or "").strip()
        and (os.getenv(_ACCESS_TOKEN_ENV) or "").strip()
    )


# ── event_id builders — mantêm client e servidor em sincronia pro dedup ──────
# O client-side usa EXATAMENTE o mesmo formato a partir do id que recebe, então
# o Meta deduplica os pares pixel↔CAPI. Suffixos distintos (sessão vs fatura)
# garantem que eventos diferentes nunca colidam.

def purchase_event_id(ref: str) -> str:
    """Purchase — `ref` é a sessão de checkout (compra imediata, casa com o
    pixel) ou o id da fatura (cobrança real pós-trial / renovação, só servidor)."""
    return f"purchase_{ref}"


def trial_event_id(session_id: str) -> str:
    """StartTrial — derivado da sessão de checkout; casa com o pixel na volta
    do checkout (/home?upgrade=success&ev=trial)."""
    return f"trial_{session_id}"


def registration_event_id(user_id: int | str) -> str:
    """CompleteRegistration — derivado do user_id recém-criado; casa com o
    pixel disparado no /cadastro após a conta ser criada."""
    return f"signup_{user_id}"


def send_event(
    *,
    event_name: str,
    event_id: str,
    event_time: int,
    value: float | None = None,
    currency: str = "BRL",
    email: str | None = None,
    event_source_url: str | None = None,
    action_source: str = "website",
) -> bool:
    """Envia um evento genérico pro Meta via Conversions API.

    `value` é opcional (eventos como CompleteRegistration não têm valor
    monetário). Retorna True se o Meta aceitou (2xx). No-op → False quando a
    CAPI não está configurada. Falha silenciosa (log + False) — nunca deve
    quebrar o fluxo que chamou.
    """
    pixel_id = (os.getenv(_PIXEL_ID_ENV) or "").strip()
    token = (os.getenv(_ACCESS_TOKEN_ENV) or "").strip()
    if not pixel_id or not token:
        return False

    # user_data precisa de ao menos um identificador pro Meta casar o evento.
    user_data: dict[str, list[str]] = {}
    if email:
        user_data["em"] = [_sha256(email)]
    if not user_data:
        logger.warning("[meta_capi] %s sem identificador (email) — pulando envio.", event_name)
        return False

    event: dict = {
        "event_name": event_name,
        "event_time": int(event_time),
        "event_id": event_id,
        "action_source": action_source,
        "user_data": user_data,
    }
    if value is not None:
        event["custom_data"] = {
            "currency": (currency or "BRL").upper(),
            "value": round(float(value), 2),
        }
    if event_source_url:
        event["event_source_url"] = event_source_url

    url = f"https://graph.facebook.com/{_GRAPH_VERSION}/{pixel_id}/events"
    try:
        resp = requests.post(
            url,
            params={"access_token": token},
            json={"data": [event]},
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        if 200 <= resp.status_code < 300:
            return True
        logger.warning(
            "[meta_capi] %s rejeitado (%s): %s",
            event_name, resp.status_code, resp.text[:300],
        )
        return False
    except Exception as exc:
        logger.warning("[meta_capi] falha ao enviar %s: %s", event_name, exc, exc_info=True)
        return False


def send_purchase_event(
    *,
    session_id: str,
    event_time: int,
    value: float,
    currency: str = "BRL",
    email: str | None = None,
    event_source_url: str | None = None,
) -> bool:
    """Wrapper de compatibilidade — Purchase derivado da sessão de checkout."""
    return send_event(
        event_name="Purchase",
        event_id=purchase_event_id(session_id),
        event_time=event_time,
        value=value,
        currency=currency,
        email=email,
        event_source_url=event_source_url,
    )
