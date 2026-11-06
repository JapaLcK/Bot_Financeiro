"""
core/services/afiliado_reembolso.py — reembolso e disputa perdida da Stripe no programa de
afiliados: rotula o pagamento do indicado e aplica a regra da comissão.

Regra (D9): reembolso total estorna a comissão da fatura se ela ainda não entrou em saque; em
saque ou já paga ela não muda e o admin é avisado; parcial só avisa. A conversão nunca sai:
`marcar_reembolso` só rotula.

Tudo aqui é idempotente, então a falha sobe: o webhook responde 5xx e a Stripe reentrega.
A comissão vem antes do rótulo de propósito: morrer entre os dois repete o alerta, e não o perde.
"""

from __future__ import annotations

from datetime import datetime, timezone

from core.observability import log_system_event_sync
from core.services import admin_notify
from core.services.extras_assinar import _ler
from db.affiliate_payments import marcar_reembolso
from db.affiliates import comissao_da_fatura, reverse_commission


def tipo_do_reembolso(cobranca) -> str | None:
    """`total`, `partial` ou None pelo acumulado devolvido da cobrança."""
    devolvido = cobranca["amount_refunded"]
    if devolvido <= 0:
        return None
    return "total" if devolvido >= cobranca["amount"] else "partial"


def fatura_da_cobranca(cobranca) -> str | None:
    """Id da fatura da cobrança, ou None se ela não tem fatura.

    Dois caminhos, porque o formato do evento segue a versão de API do ENDPOINT, que não
    mora no repositório: `charge.invoice` existe nas versões antigas e saiu do SDK fixado
    (`stripe==15.6.1`, 2026-08-26). Sem ele, a volta é o `InvoicePayment` da `PaymentIntent`.
    """
    import stripe  # noqa: PLC0415

    fatura = _ler(cobranca, "invoice")
    if fatura:
        return _ler(fatura, "id") or fatura
    intent = _ler(cobranca, "payment_intent")
    intent = _ler(intent, "id") or intent
    if not intent:
        return None
    filtro = {"type": "payment_intent", "payment_intent": intent}
    for pagamento in stripe.InvoicePayment.list(payment=filtro, limit=100)["data"]:
        if pagamento["status"] == "paid":  # estornado continua "paid"; open/canceled são tentativas
            fatura = pagamento["invoice"]
            return _ler(fatura, "id") or fatura
    return None


def _avisar(comissao: dict, tipo: str) -> None:
    onde = {"partial": "reembolso parcial", "total": "reembolso total com a comissão fora de alcance"}[tipo]
    saque = f" · saque `{comissao['payout_id']}`" if comissao["payout_id"] else ""
    admin_notify.notify_afiliado_alerta(
        f"⚠️ **Afiliado: {onde}** comissão `{comissao['id']}` ({comissao['status']}) · "
        f"R$ {comissao['amount_cents'] / 100:.2f}{saque} · a comissão NÃO foi alterada, confira."
    )
    log_system_event_sync(
        "warning", "affiliate_refund_commission_kept",
        "Reembolso de indicado sem estorno automatico da comissao.", source="affiliates",
        details={"commission_id": comissao["id"], "payout_id": comissao["payout_id"], "tipo": tipo},
    )


def _estornar(comissao: dict) -> bool:
    if not reverse_commission(comissao["id"]):
        return False
    log_system_event_sync(
        "info", "affiliate_commission_reversed_by_refund",
        "Comissao estornada por reembolso total do indicado.", source="affiliates",
        details={"commission_id": comissao["id"]},
    )
    return True


def _aplicar_na_comissao(fatura: str, tipo: str) -> None:
    comissao = comissao_da_fatura(fatura)
    if comissao is None or comissao["status"] == "reversed":
        return
    if tipo == "total" and _estornar(comissao):
        return
    if comissao["refund_status"] == tipo or comissao["refund_status"] == "total":
        return  # reentrega: o alerta já saiu
    # Quem decide é o `update` atômico acima, nunca a leitura de antes: o saque pode ter levado
    # a comissão no meio. Relê só para o alerta dizer o estado de agora.
    atual = comissao_da_fatura(fatura) or comissao
    if atual["status"] != "reversed":
        _avisar(atual, tipo)


def tratar_evento(event) -> None:
    """`charge.refunded` e `charge.dispute.closed`. Sem fatura ou fora do registro é no-op."""
    import stripe  # noqa: PLC0415

    objeto = event["data"]["object"]
    if event["type"] == "charge.dispute.closed":
        if objeto["status"] != "lost":
            return
        cobranca = objeto["charge"]
        if isinstance(cobranca, str):
            cobranca = stripe.Charge.retrieve(cobranca)
        # Soma o já devolvido: disputa perdida do restante deixa a cobrança sem nada.
        tipo = "total" if objeto["amount"] + cobranca["amount_refunded"] >= cobranca["amount"] else "partial"
    else:
        cobranca, tipo = objeto, tipo_do_reembolso(objeto)
    fatura = fatura_da_cobranca(cobranca)
    if not tipo or not fatura:
        log_system_event_sync(
            "warning", "affiliate_refund_sem_fatura",
            "Reembolso da Stripe sem fatura identificada.", source="affiliates",
            details={"event_type": event["type"], "event_id": event["id"]},
        )
        return
    _aplicar_na_comissao(fatura, tipo)
    marcar_reembolso("stripe", fatura, tipo, datetime.fromtimestamp(event["created"], timezone.utc))
    # De novo depois do rótulo: um `reject_payout` que leu antes dele devolveu a comissão, e
    # ela está solta agora. Quem rejeita depois do rótulo já a estorna sozinho.
    comissao = comissao_da_fatura(fatura) if tipo == "total" else None
    if comissao:
        _estornar(comissao)
