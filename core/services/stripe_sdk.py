"""core/services/stripe_sdk.py — acessores do SDK Stripe sem dependência do monólito.

Moraram em `frontend/finance_bot_websocket_custom.py`; saíram de lá para o job de
exclusão de conta (`core/services/stripe_exclusao.py`) não carregar o app inteiro.
"""
from __future__ import annotations


def _sg(obj, key, default=None):
    """Acessor seguro pra objetos do SDK Stripe v8 (não são dicts — sempre
    obj[k] com try/except, nunca .get). Espelho do _g do webhook."""
    if obj is None:
        return default
    try:
        v = obj[key]
    except (KeyError, TypeError, AttributeError):
        return default
    return v if v is not None else default


def _is_missing_stripe_customer(stripe_mod, exc: Exception) -> bool:
    """Distingue customer apagado de indisponibilidade real da API."""
    invalid_request = getattr(getattr(stripe_mod, "error", None), "InvalidRequestError", None)
    if not isinstance(invalid_request, type) or not isinstance(exc, invalid_request):
        return False
    return (
        (getattr(exc, "code", None) == "resource_missing"
         and getattr(exc, "param", None) == "customer")
        or "no such customer" in str(exc).lower()
    )

