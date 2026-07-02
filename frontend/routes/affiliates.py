"""Rotas do programa de afiliados.

Público:   GET /r/{code}            → seta cookie de atribuição (30d) e manda pra landing.
Dashboard: GET /api/affiliate/me    → painel do afiliado (404 se o user não é afiliado).
           POST /api/affiliate/payout → pedido de saque (mínimo R$ 50, Pix manual).

O gate de assinatura NÃO se aplica aqui de propósito: o afiliado precisa ver o
próprio painel e sacar mesmo que não seja assinante Pro.
"""
import asyncio

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from db.affiliates import (
    MIN_PAYOUT_CENTS,
    REF_COOKIE_MAX_AGE_DAYS,
    get_affiliate_by_code,
    get_affiliate_by_user,
    get_affiliate_stats,
    list_affiliate_commissions,
    list_affiliate_payouts,
    request_payout,
    set_affiliate_pix_key,
)
from frontend.routes import shared

router = APIRouter()

REF_COOKIE_NAME = "ref_code"
_COOKIE_SECURE = shared.DASHBOARD_URL.startswith("https://")


@router.get("/r/{code}")
async def affiliate_link(code: str):
    """Link de divulgação do afiliado. Código válido → cookie de atribuição;
    inválido → só redireciona (sem vazar se o código existe)."""
    response = RedirectResponse(url="/", status_code=302)
    affiliate = await asyncio.to_thread(get_affiliate_by_code, code)
    if affiliate and affiliate["status"] == "active":
        response.set_cookie(
            REF_COOKIE_NAME,
            affiliate["code"],
            max_age=REF_COOKIE_MAX_AGE_DAYS * 24 * 3600,
            httponly=True,
            secure=_COOKIE_SECURE,
            samesite="lax",
        )
    return response


def _cents(v) -> float:
    return round(int(v) / 100.0, 2)


@router.get("/api/affiliate/me")
async def affiliate_me(request: Request):
    user_id = shared.resolve_dashboard_user_id(request)
    shared.raise_if_account_scheduled_for_deletion(user_id)

    affiliate = await asyncio.to_thread(get_affiliate_by_user, user_id)
    if not affiliate:
        raise HTTPException(status_code=404, detail="Você não faz parte do programa de afiliados.")

    stats = await asyncio.to_thread(get_affiliate_stats, affiliate["id"])
    commissions = await asyncio.to_thread(list_affiliate_commissions, affiliate["id"], 50)
    payouts = await asyncio.to_thread(list_affiliate_payouts, affiliate["id"], 20)

    return {
        "code": affiliate["code"],
        "status": affiliate["status"],
        "link": shared.public_site_url(f"/r/{affiliate['code']}"),
        "commission_percent": affiliate["commission_bps"] / 100.0,
        "has_pix_key": bool(affiliate["pix_key_enc"]),
        "min_payout": _cents(MIN_PAYOUT_CENTS),
        "stats": {
            "referrals": stats["referrals"],
            "held": _cents(stats["held_cents"]),
            "available": _cents(stats["available_cents"]),
            "requested": _cents(stats["requested_cents"]),
            "paid": _cents(stats["paid_cents"]),
        },
        "commissions": [
            {
                "id": c["id"],
                "invoice_amount": _cents(c["invoice_amount_cents"]),
                "amount": _cents(c["amount_cents"]),
                "status": c["status"],
                "available_at": c["available_at"].isoformat(),
                "created_at": c["created_at"].isoformat(),
            }
            for c in commissions
        ],
        "payouts": [
            {
                "id": p["id"],
                "amount": _cents(p["amount_cents"]),
                "status": p["status"],
                "requested_at": p["requested_at"].isoformat(),
                "paid_at": p["paid_at"].isoformat() if p["paid_at"] else None,
                "note": p["note"],
            }
            for p in payouts
        ],
    }


class PayoutRequestBody(BaseModel):
    pix_key: str


@router.post("/api/affiliate/payout")
async def affiliate_request_payout(request: Request, body: PayoutRequestBody):
    user_id = shared.resolve_dashboard_user_id(request)
    shared.raise_if_account_scheduled_for_deletion(user_id)

    affiliate = await asyncio.to_thread(get_affiliate_by_user, user_id)
    if not affiliate:
        raise HTTPException(status_code=404, detail="Você não faz parte do programa de afiliados.")

    pix_key = (body.pix_key or "").strip()
    if not (5 <= len(pix_key) <= 140):
        raise HTTPException(status_code=400, detail="Informe uma chave Pix válida.")

    from core.crypto import encrypt_pii_optional, hash_pii_optional
    pix_enc = encrypt_pii_optional(pix_key)
    pix_hash = hash_pii_optional(pix_key, kind="generic")

    try:
        payout = await asyncio.to_thread(request_payout, affiliate["id"], pix_enc)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Guarda a chave no cadastro do afiliado pros próximos saques
    await asyncio.to_thread(set_affiliate_pix_key, affiliate["id"], pix_hash, pix_enc)

    return {
        "ok": True,
        "payout": {
            "id": payout["id"],
            "amount": _cents(payout["amount_cents"]),
            "status": payout["status"],
            "requested_at": payout["requested_at"].isoformat(),
        },
    }
