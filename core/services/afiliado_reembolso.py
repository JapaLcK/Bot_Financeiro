"""
core/services/afiliado_reembolso.py — reembolso e disputa perdida da Stripe no programa de
afiliados: calcula o que a Stripe devolveu, grava a pendência e avisa o admin.

Quem decide é `db.affiliate_reviews.registrar_reembolso`, sob as travas, na mesma transação do
rótulo. O aviso vem depois do commit e não libera nada: falha dele responde 200, e a linha segue
pendente até `reenviar_pendentes`. Falha de banco ou da Stripe sobe: 5xx e a Stripe reentrega.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from core.observability import log_system_event_sync
from core.pg_text import limpa_para_pg
from core.services import admin_notify
from core.services.extras_assinar import _ler
from db.affiliate_reviews import marcar_avisada, registrar_reembolso, reivindicar

_FUSO = ZoneInfo("America/Sao_Paulo")
_DATA = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_REFERENCIA = re.compile(r"^[A-Za-z0-9._:/-]{4,120}$")


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


def _texto(r: dict) -> str:
    saque = f" · saque `{r['payout_id']}`" if r["payout_id"] else ""
    cabeca = f"⚠️ **Afiliado: {{}}** comissão `{r['commission_id']}` ({r['commission_status']}) · "
    if r["status"] == "recovery":
        return (cabeca.format("recuperação pendente") + f"R$ {r['recovery_cents'] / 100:.2f}{saque} · "
                "o dinheiro saiu antes do reembolso, confira.")
    onde = {"partial": "reembolso parcial", "total": "reembolso total com a comissão fora de alcance"}[r["tipo"]]
    return cabeca.format(onde) + f"R$ {r['commission_cents'] / 100:.2f}{saque} · a comissão NÃO foi alterada, confira."


def _registrar_tentativa(r: dict, entregue: bool) -> None:
    log_system_event_sync(
        "warning", "affiliate_refund_commission_kept",
        "Reembolso de indicado sem estorno automatico da comissao.", source="affiliates",
        details={"commission_id": r["commission_id"], "payout_id": r.get("payout_id"), "tipo": r["tipo"],
                 "alerta_entregue": entregue},
    )


def _enviar(r: dict) -> bool:
    """`r` já reivindicada. `notified_at` só com entrega confirmada."""
    entregue = admin_notify.notify_afiliado_alerta(_texto(r)) is True
    if entregue:
        marcar_avisada(r["id"], r["refunded_cents"])
    _registrar_tentativa(r, entregue)
    return entregue


def reenviar_pendentes(limite: int = 20) -> int:
    """Passada do `_afiliado_alerta_worker`. Sem canal não reivindica nada; para na 1ª falha."""
    if not admin_notify.canal_configurado():
        return 0
    enviados = 0
    for _ in range(limite):
        r = reivindicar()
        if r is None or not _enviar(r):
            break
        enviados += 1
    return enviados


def validar_justificativa(valor) -> str:
    """Regra única das três ações do admin. O erro nunca ecoa o valor."""
    texto = limpa_para_pg(valor).strip() if isinstance(valor, str) else ""
    if not 10 <= len(texto) <= 500:
        raise ValueError("Justificativa: de 10 a 500 caracteres.")
    return texto


def validar_comprovante(data_valor, referencia, minimo: date | None) -> tuple[date, str]:
    """A data do comprovante e a referência, para "Pagamento já realizado" e para a recuperação.
    A data não é futura em Brasília nem anterior a `minimo` (pedido do saque ou reembolso)."""
    if not isinstance(data_valor, str) or not _DATA.match(data_valor):
        raise ValueError("Data inválida: use AAAA-MM-DD.")
    try:
        data = date.fromisoformat(data_valor)
    except ValueError:
        raise ValueError("Data inválida: use AAAA-MM-DD.") from None
    if data > datetime.now(_FUSO).date():
        raise ValueError("A data não pode estar no futuro.")
    if minimo and data < minimo:
        raise ValueError("A data é anterior ao início deste caso.")
    limpa = limpa_para_pg(referencia).strip() if isinstance(referencia, str) else ""
    if not _REFERENCIA.match(limpa):
        raise ValueError("Referência inválida: de 4 a 120 caracteres, só letras, números e . _ : / -")
    return data, limpa


def dia_em_brasilia(instante: datetime) -> date:
    return instante.astimezone(_FUSO).date()


def tratar_evento(event) -> None:
    """`charge.refunded` e `charge.dispute.closed`. Sem fatura é no-op."""
    import stripe  # noqa: PLC0415

    objeto = event["data"]["object"]
    if event["type"] == "charge.dispute.closed":
        if objeto["status"] != "lost":
            return
        cobranca = objeto["charge"]
        if isinstance(cobranca, str):
            cobranca = stripe.Charge.retrieve(cobranca)
        # Soma o já devolvido: disputa perdida do restante deixa a cobrança sem nada.
        devolvido = objeto["amount"] + cobranca["amount_refunded"]
        tipo = "total" if devolvido >= cobranca["amount"] else "partial"
    else:
        cobranca, tipo, devolvido = objeto, tipo_do_reembolso(objeto), objeto["amount_refunded"]
    fatura = fatura_da_cobranca(cobranca)
    if not tipo or not fatura:
        log_system_event_sync(
            "warning", "affiliate_refund_sem_fatura",
            "Reembolso da Stripe sem fatura identificada.", source="affiliates",
            details={"event_type": event["type"], "event_id": event["id"]},
        )
        return
    linha = registrar_reembolso(fatura, tipo, devolvido, datetime.fromtimestamp(event["created"], timezone.utc))
    if not linha or linha["status"] not in ("open", "recovery") or linha["notified_at"]:
        return
    if not admin_notify.canal_configurado():
        _registrar_tentativa(linha, False)
        return
    r = reivindicar(linha["id"])
    if r:
        _enviar(r)
