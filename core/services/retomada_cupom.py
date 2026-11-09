"""Cupom Stripe do desconto de retomada (docs/plano-remarketing.md, PR 6b).
Etapa 0 = campanha (`oferta_campanha`): `rm_e{retomada_etapa or 0}_40_{produto}` (e1-e3 com link).

Um cupom por (etapa, %, produto), com ID FIXO: `repeating` por 1 mês cobre a 1ª cobrança real
(dia 7 com trial, imediata sem trial) e não pega a renovação; `once` seria gasto na fatura de
R$ 0 do trial. Existente que divirja do esperado NUNCA é corrigido: o chamador responde 503.
"""
from __future__ import annotations

import logging

from core.services.stripe_sdk import _sg


class CupomIndisponivel(RuntimeError):
    """Stripe fora, cupom divergente ou inválido: a venda com desconto não sai (503)."""


def _ok(cupom, pct: int, products: list[str]) -> bool:
    produtos = list(_sg(_sg(cupom, "applies_to", {}) or {}, "products", []) or [])
    return (
        _sg(cupom, "valid", True) is not False
        and float(_sg(cupom, "percent_off", 0) or 0) == float(pct)
        and _sg(cupom, "duration") == "repeating"
        and _sg(cupom, "duration_in_months") == 1
        and sorted(produtos) == products
    )


def cupom_id(stripe_mod, etapa: int, pct: int, price_id: str) -> str:
    """ID do cupom da etapa para o produto do Price, criando-o se faltar. Idempotente.
    `price_id` também aceita lista/tupla de Prices: um cupom para todos os produtos deles."""
    try:
        products = []
        for pid in ([price_id] if isinstance(price_id, str) else price_id):
            product = _sg(stripe_mod.Price.retrieve(pid), "product")
            products.append(product if isinstance(product, str) else _sg(product, "id"))
        products = sorted(set(products))
        cid = f"rm_e{etapa}_{pct}_" + "_".join(products)
        try:
            cupom = stripe_mod.Coupon.retrieve(cid, expand=["applies_to"])
        except stripe_mod.error.InvalidRequestError as exc:
            if getattr(exc, "code", None) != "resource_missing":
                raise
            try:
                cupom = stripe_mod.Coupon.create(
                    id=cid, name=f"{pct}% OFF", percent_off=pct, duration="repeating", duration_in_months=1,
                    applies_to={"products": products}, expand=["applies_to"])
            except stripe_mod.error.InvalidRequestError as exc2:   # corrida: outro worker criou
                if getattr(exc2, "code", None) != "resource_already_exists" \
                        and "already exists" not in str(exc2):
                    raise
                cupom = stripe_mod.Coupon.retrieve(cid, expand=["applies_to"])
    except stripe_mod.error.StripeError as exc:
        logging.getLogger(__name__).error("retomada_cupom_stripe: %s", exc)
        raise CupomIndisponivel(str(exc)) from exc
    if not _ok(cupom, pct, products):
        logging.getLogger(__name__).error("retomada_cupom_divergente id=%s", cid)
        raise CupomIndisponivel(cid)
    return cid
