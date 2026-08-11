"""
core/services/meta_capi.py — Meta (Facebook) Conversions API (server-side).

Complementa o pixel client-side: o webhook da Stripe dispara o `Purchase` daqui,
com o valor REAL da assinatura e um `event_id` compartilhado com o pixel do
navegador. O Meta deduplica eventos com mesmo (event_name, event_id) vindos do
pixel e da CAPI — então a compra conta uma vez só, mesmo que os dois disparem.

Por que server-side além do pixel:
  - Valor preciso da compra (o pixel client-side manda só a moeda) → ROAS real.
  - Imune a adblock / iOS-ATT / restrição de cookie (o pixel perde ~10-30%).
  - Dedup por event_id resolve o disparo duplicado do ?upgrade=success.

Configuração (ambas obrigatórias — se faltar qualquer uma, tudo vira no-op):
  - META_PIXEL_ID: mesmo ID usado no pixel client-side.
  - META_PIXEL_ACCESS_TOKEN: token da Conversions API (Events Manager →
    Configurações → Conversions API → Gerar token de acesso).

Quem chama: webhook do Stripe (checkout.session.completed), sempre via
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


def purchase_event_id(session_id: str) -> str:
    """event_id determinístico da compra, derivado da sessão de checkout.

    O client-side (home.html) usa o MESMO formato a partir do `sid` que a
    Stripe devolve no success_url — é isso que permite o dedup no Meta.
    """
    return f"purchase_{session_id}"


def send_purchase_event(
    *,
    session_id: str,
    event_time: int,
    value: float,
    currency: str = "BRL",
    email: str | None = None,
    event_source_url: str | None = None,
) -> bool:
    """Envia um evento `Purchase` pro Meta via Conversions API.

    Retorna True se o Meta aceitou (2xx). No-op → False quando CAPI não está
    configurado. Falha silenciosa (log + False) — nunca deve quebrar o webhook.
    """
    pixel_id = (os.getenv(_PIXEL_ID_ENV) or "").strip()
    token = (os.getenv(_ACCESS_TOKEN_ENV) or "").strip()
    if not pixel_id or not token:
        return False

    # user_data precisa de ao menos um identificador pro Meta casar a conversão.
    user_data: dict[str, list[str]] = {}
    if email:
        user_data["em"] = [_sha256(email)]
    if not user_data:
        logger.warning("[meta_capi] Purchase sem identificador (email) — pulando envio.")
        return False

    event: dict = {
        "event_name": "Purchase",
        "event_time": int(event_time),
        "event_id": purchase_event_id(session_id),
        "action_source": "website",
        "user_data": user_data,
        "custom_data": {
            "currency": (currency or "BRL").upper(),
            "value": round(float(value or 0), 2),
        },
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
            "[meta_capi] Purchase rejeitado (%s): %s",
            resp.status_code, resp.text[:300],
        )
        return False
    except Exception as exc:
        logger.warning("[meta_capi] falha ao enviar Purchase: %s", exc, exc_info=True)
        return False
