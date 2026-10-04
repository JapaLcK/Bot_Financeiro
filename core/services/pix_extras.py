"""core/services/pix_extras.py — os cadernos extras vendidos junto com o Pix anual.

A cobrança guarda a FOTO dos cadernos na coluna `extras`
(`[{price, url, nome, valor_cents}]`), fora de `amount_cents`, que segue só o
plano (crédito de upgrade e `pix_charges_amount_fecha` não mudam). O que o cliente
paga é `total_cents`.
"""

from __future__ import annotations

# Status do `GET /v3/payments/{id}` do Asaas. Os de estorno/contestação seguram a
# entrega para sempre (D2/D3, a mesma régua do cartão); contestação e estorno em
# andamento não mudam o status local (ROTEAMENTO do dreno), por isso a consulta.
_PAGO = {"RECEIVED", "CONFIRMED"}
_ESTORNO = {"REFUNDED", "REFUND_REQUESTED", "REFUND_IN_PROGRESS", "CHARGEBACK_REQUESTED",
            "CHARGEBACK_DISPUTE", "AWAITING_CHARGEBACK_REVERSAL"}
_ESTORNO_LOCAL = {"refunded", "refunded_partial", "chargeback"}


def total_cents(cobranca) -> int:
    """Plano + Σ cadernos da foto."""
    return int(cobranca["amount_cents"]) + sum(
        int(e["valor_cents"]) for e in cobranca.get("extras") or [])


def conferir_entrega(user_id: int, sid: str, preco: str) -> tuple[str, str | None]:
    """(`nao_comprou` | `estornado` | `pago`, nome do caderno na foto).

    Ordem: cobrança do dono (senão `nao_comprou`, sem Asaas) → estorno local (sem
    Asaas) → Asaas. Falha do Asaas ou forma inesperada LEVANTA (falha fechado):
    o job não envia nem fecha, e o claim expira com backoff."""
    from core.services.asaas import buscar_pagamento
    from db.pix_extras import cobranca_do_dono

    cob = cobranca_do_dono(user_id, sid)
    item = next((e for e in (cob or {}).get("extras") or [] if e.get("price") == preco), None)
    if item is None:
        return "nao_comprou", None
    if cob["status"] in _ESTORNO_LOCAL:
        return "estornado", item.get("nome")
    if not cob["asaas_payment_id"]:
        raise RuntimeError("cobrança sem pagamento no Asaas")
    pag = buscar_pagamento(cob["asaas_payment_id"])
    status, estornos = pag.get("status"), pag.get("refunds")
    if estornos is not None and not isinstance(estornos, list):
        raise RuntimeError("refunds de forma inesperada")
    if status in _ESTORNO or estornos:
        return "estornado", item.get("nome")
    if status in _PAGO:
        return "pago", item.get("nome")
    raise RuntimeError(f"status do Asaas inesperado: {status}")
