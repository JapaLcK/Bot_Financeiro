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


class ExtrasIndisponiveis(RuntimeError):
    """Caderno pedido fora da oferta ATUAL → **409**, com a oferta atual para a tela
    redesenhar. Levantada antes de qualquer escrita e de qualquer chamada ao Asaas."""

    ERRO = "extras_indisponiveis"

    def __init__(self, oferta: list[dict]):
        super().__init__(self.ERRO)
        self.oferta = oferta


def _ofertas(user_id: int) -> list:
    """`ofertas_da_pagina`, a MESMA fonte do cartão (texto e preço iguais, §0.7).
    Vazia com a venda Pix ou a página própria (`CHECKOUT_PAGINA_PROPRIA`, Q2)
    desligadas, ou sem Stripe configurado. Falha do Stripe também dá vazia (lá)."""
    from core.services.extras_assinar import ofertas_da_pagina, pagina_propria_ligada
    from core.services.pix_checkout import pix_annual_available
    from frontend.finance_bot_websocket_custom import STRIPE_SECRET_KEY  # noqa: PLC0415

    if not (pix_annual_available() and pagina_propria_ligada() and STRIPE_SECRET_KEY):
        return []
    import stripe
    stripe.api_key = STRIPE_SECRET_KEY
    return ofertas_da_pagina(stripe, user_id)


def _tela(ofertas) -> list[dict]:
    from core.services.extras_assinar import para_tela
    return [dict(t, price=p) for t, (p, _, _) in zip(para_tela(ofertas), ofertas)]


def oferta(user_id: int) -> list[dict]:
    """As caixas do modal do Pix: as do cartão + o id do Price. A URL do PDF fica fora."""
    return _tela(_ofertas(user_id))


def escolher(user_id: int, ids: list[str]) -> list[dict]:
    """A FOTO `[{price, url, nome, valor_cents}]` dos ids, relida da oferta ATUAL: o
    valor é o do Price do Stripe, nunca o do cliente. Sem ids, o Stripe nem é
    consultado (o plano vende com ele fora). Id fora da oferta → `ExtrasIndisponiveis`."""
    if not ids:
        return []
    ofertas = _ofertas(user_id)
    if not set(ids) <= {p for p, _, _ in ofertas}:
        raise ExtrasIndisponiveis(_tela(ofertas))
    return [{"price": p, "url": u, "nome": t["nome"], "valor_cents": t["valor_centavos"]}
            for p, u, t in ofertas if p in ids]


def selecao(user_id: int) -> list[str]:
    """Ids da cobrança ativa `pending` com QR vivo do usuário (Q6); senão `[]`."""
    from core.services.pix_checkout_resposta import qr_vivo
    from db.pix_charges_saga import buscar_ativa

    ativa = buscar_ativa(user_id)
    return [e["price"] for e in ativa["extras"]] if ativa and qr_vivo(ativa) else []


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
