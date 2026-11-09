"""Reconquista de trial vencido sem pagamento: UM e-mail, 2 dias depois do fim do trial, com
Promotion Code de 10% no 1º mês (exclusivo do cliente, uso único, 7 dias). Inerte sem
`RECONQUISTA_DESDE`. A fila é `db/reconquista.py`; o agendamento sai do webhook
`customer.subscription.deleted` e o envio do tick do `_remarketing_worker`.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from core.services.remarketing import ligado as _ligado
from core.services.stripe_sdk import _sg

_log = logging.getLogger(__name__)
ETAPA_CUPOM = 9   # reservada à reconquista em retomada_cupom (rm_e9_10_...)


def ligado() -> datetime | None:
    return _ligado("RECONQUISTA_DESDE")


def _prices() -> list[str]:
    """Plus e Pro mensais (cupom por produto: os anuais, se no mesmo produto, entram junto)."""
    plus = os.getenv("STRIPE_PRICE_ID_PRO_MENSAL") or os.getenv("STRIPE_PRICE_ID_PRO") or ""
    return [p for p in (plus, os.getenv("STRIPE_PRICE_ID_PROMAX_MENSAL") or "") if p]


def _pagou_alguma_vez(stripe_mod, cus: str) -> bool:
    # ponytail: 1 página de 100 faturas; trial sem pagamento tem poucas. Paginar se um dia faltar.
    faturas = stripe_mod.Invoice.list(customer=cus, status="paid", limit=100)
    return any((_sg(f, "amount_paid", 0) or 0) > 0 for f in _sg(faturas, "data", []) or [])


def _processar(stripe_mod, uid: int, precos: list[str]) -> tuple[str, str | None]:
    """(status, motivo) da linha reivindicada."""
    from core.services import plan_service  # noqa: PLC0415
    from core.services.email_service import (  # noqa: PLC0415
        make_unsub_url, send_reconquista_email)
    from core.services.retomada_cupom import cupom_id  # noqa: PLC0415
    from db import get_auth_user  # noqa: PLC0415

    user = get_auth_user(uid) or {}
    cus, email = user.get("stripe_customer_id"), (user.get("email") or "").strip()
    if plan_service.get_plan_tier(uid) != "free":
        return "pulado", "assinou"
    if user.get("engagement_opt_out"):
        return "pulado", "opt_out"
    if not email:
        return "pulado", "sem_email"
    if not cus:
        return "pulado", "sem_cliente"
    if _pagou_alguma_vez(stripe_mod, cus):
        return "pulado", "ja_pagou"
    valido_ate = datetime.now(timezone.utc) + timedelta(days=7)
    promo = stripe_mod.PromotionCode.create(
        promotion={"type": "coupon", "coupon": cupom_id(stripe_mod, ETAPA_CUPOM, 10, precos)},
        customer=cus,
        max_redemptions=1, expires_at=int(valido_ate.timestamp()))
    nome = ((user.get("display_name") or "").split() or [""])[0]
    if not send_reconquista_email(email, nome, _sg(promo, "code"), valido_ate,
                                  make_unsub_url(uid, email)):
        return "pulado", "email_falhou"
    return "enviado", None


def rodar_uma_vez(limite: int = 20) -> int:
    """Um tick: reivindica as linhas vencidas e fecha cada uma. Exceção = 'pulado', nunca volta à fila."""
    if ligado() is None or not os.getenv("STRIPE_SECRET_KEY") or not (precos := _prices()):
        return 0
    import stripe  # noqa: PLC0415
    from db.reconquista import fechar, reivindicar_vencidos  # noqa: PLC0415

    stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
    n = 0
    # Uma linha por vez: deploy/morte no meio do tick deixa presa só a linha em curso.
    while n < limite and (linhas := reivindicar_vencidos(1)):
        n += 1
        uid = int(linhas[0]["user_id"])
        try:
            status, motivo = _processar(stripe, uid, precos)
        except Exception as exc:    # inclui falha do Stripe: sem retentativa, é um e-mail só
            _log.error("reconquista user=%s: %s", uid, type(exc).__name__)
            status, motivo = "pulado", f"erro:{type(exc).__name__}"
        fechar(uid, status, motivo)
    return n
