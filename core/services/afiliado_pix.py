"""
core/services/afiliado_pix.py — o lado do afiliado no dreno do Pix.

Pagante e reembolso do indicado (`db/affiliate_payments.py`). Saiu de
`pix_drain_effects.py` por assunto. Como os outros efeitos, levanta em caso de falha:
quem chama transforma a exceção em retentativa.
"""

from __future__ import annotations

from datetime import datetime, timezone


def registrar(cobranca, evt) -> None:
    """Conversão do indicado: só o plano (`amount_cents`), nunca os cadernos. O rótulo no
    fim é da retentativa: o parcial que passou antes não achou a linha para marcar."""
    from db.affiliate_payments import registrar_pagamento

    registrar_pagamento(cobranca["user_id"], "pix", str(cobranca["id"]),
                        int(cobranca["amount_cents"]),
                        cobranca["paid_at"] or datetime.now(timezone.utc))
    rotular_reembolso(cobranca, cobranca["status"])


def marcar(cobranca, tipo: str) -> None:
    """Dois efeitos, um por `tipo`: o dreno deduplica por `(pagamento, efeito)`, e com
    um nome só o total depois do parcial seria pulado."""
    from db.affiliate_payments import marcar_reembolso

    marcar_reembolso("pix", str(cobranca["id"]), tipo,
                     cobranca["refunded_at"] or datetime.now(timezone.utc))


def rotular_reembolso(cobranca, status: str | None) -> None:
    """Rótulo pelo estado da cobrança, sem registro de efeito (é idempotente): serve à
    conta excluída, que não passa pelo laço, e à retentativa de `registrar`."""
    tipo = {"refunded": "total", "refunded_partial": "partial"}.get(status)
    if tipo:
        marcar(cobranca, tipo)


def efeitos_do_laco(efeitos, cobranca, payment_id) -> tuple:
    """O rótulo do parcial só roda com a cobrança em `refunded_partial` e com id: parcial
    fora de ordem é zero efeito, e sem id não há par `(pagamento, efeito)` para registrar."""
    if cobranca["status"] == "refunded_partial" and payment_id:
        return efeitos
    return tuple(e for e in efeitos if e != "afiliado_estorno_parcial")
