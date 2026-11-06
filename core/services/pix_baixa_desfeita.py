"""
core/services/pix_baixa_desfeita.py — `PAYMENT_RECEIVED_IN_CASH_UNDONE` (#1267).

A baixa manual desfeita revoga o acesso, alerta o operador e REABRE a cobrança;
um pagamento confirmado depois, com o mesmo id, concede de novo sem repetir Purchase/e-mail
(`ga4`/`capi`/`email` ficam registrados) e sem enfraquecer a D3.

Por que o desenho é este:
  * `event_version` é época de CHEGADA, não separa reentrega de evento novo; por
    isso a ordem dos fatos se PERGUNTA ao Asaas (`pagamento_confirmado`), e falha
    do Asaas levanta (vira `attempts`) em vez de adivinhar;
  * o UNDONE de `paid` NUNCA grava o efeito `revoke` (ele bloquearia o Pix real
    para sempre pela D3); o marcador durável é `baixa_desfeita`, nunca apagado;
  * a revogação roda ANTES da reabertura, para a retentativa completar se o
    processo morrer entre as duas (`revoke_grant` é idempotente);
  * a volta a `pending` pode estourar `uniq_pix_charge_ativa` (outra cobrança
    ativa do usuário): repete com `expired`;
  * `reconciliar_grant`: se o processo morreu entre a revogação e a reabertura e o Pix
    real chegou antes da retentativa, o grant revogado por este motivo é reconcedido.
"""

from __future__ import annotations

import logging

from psycopg import errors

from core.services import admin_notify, asaas
from core.services.pix_drain_effects import EXECUTORES
from core.services.pix_extras import _PAGO, total_cents
from db.pix_charges import transicionar
from db.pix_charges_saga import reabrir_baixa
from db.pix_effects import efeito_registrado, lock_efeito, registrar_efeito

log = logging.getLogger(__name__)
TIPO = "PAYMENT_RECEIVED_IN_CASH_UNDONE"


def pagamento_confirmado(payment_id: str) -> bool:
    """O Asaas diz que o pagamento está pago AGORA? Levanta se não responder."""
    return asaas.buscar_pagamento(payment_id).get("status") in _PAGO


def _alertar(cobranca, texto: str) -> None:
    admin_notify.notify_pix_alerta(
        f"⚠️ **Pix: {TIPO}** cobrança `{cobranca['id']}` · "
        f"valor R$ {total_cents(cobranca) / 100:.2f} · {texto}")


def reconciliar_grant(cobranca, evt, payment_id: str) -> bool:
    """Morreu depois do `revoke` e antes de `reabrir_baixa`, e o Pix real veio antes da
    retentativa: cobrança `paid`, efeito `grant` registrado (o RECEIVED o pula) e grant
    revogado POR ESTE MOTIVO. Reconcede sem Purchase/e-mail (já registrados). Exige
    marcador; revogado por estorno/admin não volta. True se reconcedeu (já logou e alertou)."""
    from db.plan_grants import list_grants

    def _revogado():
        return any(g["source"] == "pix" and g["external_ref"] == str(cobranca["id"])
                   and g["status"] == "revoked" and g["revoked_reason"] == f"asaas:{TIPO}"
                   for g in list_grants(cobranca["user_id"]))

    if (cobranca["user_id"] is None or cobranca["status"] not in ("paid", "paid_orphan")
            or not efeito_registrado(payment_id, "baixa_desfeita") or not _revogado()
            or not pagamento_confirmado(payment_id)):
        return False
    with lock_efeito(payment_id, "grant"):
        if not (efeito_registrado(payment_id, "grant") and _revogado()):
            return False
        EXECUTORES["grant"](cobranca, evt)
    log.warning("pix: grant reconcedido (baixa desfeita, Pix real) cobranca=%s", cobranca["id"])
    _alertar(cobranca, "acesso reconcedido: o Pix real chegou antes da retentativa do UNDONE.")
    return True


def desfazer_baixa(cobranca, evt, payment_id: str) -> None:
    antigo = cobranca["asaas_payment_id"]
    if (cobranca["status"] in ("paid", "paid_orphan", "refunded_partial")
            and antigo and payment_id != antigo):
        # #1257: UNDONE de OUTRO pagamento (retido) numa cobrança paga por `antigo`:
        # revogar/reabrir tiraria o acesso de um Pix real.
        return _alertar(cobranca, f"ignorado: baixa desfeita do pagamento `{payment_id}`, "
                        f"mas a cobrança foi paga por `{antigo}`; nada alterado.")
    if pagamento_confirmado(payment_id):  # UNDONE velho, reentregue depois do Pix real
        if reconciliar_grant(cobranca, evt, payment_id):
            return
        return _alertar(cobranca, "ignorado: o Asaas já mostra o pagamento como pago.")
    if cobranca["status"] in ("refunded", "chargeback"):
        return _alertar(cobranca, "cobrança já estornada/contestada, nada reaberto.")
    registrar_efeito(payment_id, "baixa_desfeita", evt["event_id"])
    status = cobranca["status"]
    with lock_efeito(payment_id, "grant"):
        if cobranca["user_id"] is not None:
            EXECUTORES["revoke"](cobranca, evt)
        if status == "refunded_partial":
            transicionar(cobranca["id"], de="refunded_partial", para="refunded",
                         asaas_payment_id=payment_id)
            registrar_efeito(payment_id, "revoke", evt["event_id"])
        elif status in ("paid", "paid_orphan"):
            try:
                # órfã (sem titular) não vai a pending: o índice/leitores de ativa esperam titular
                reabrir_baixa(cobranca["id"], payment_id,
                              "pending" if cobranca["user_id"] is not None else "expired")
            except errors.UniqueViolation:
                reabrir_baixa(cobranca["id"], payment_id, "expired")
    if status in ("paid", "paid_orphan"):
        texto = "acesso revogado, cobrança reaberta."
    elif status == "refunded_partial":
        texto = "acesso revogado, cobrança estornada."
    else:
        texto = f"cobrança em {status}: nada concedido nem alterado, só registrado."
    if cobranca["stripe_subscription_id"] and efeito_registrado(payment_id, "stripe_cancel"):
        texto += " O cancelamento do cartão no Stripe segue agendado."
    _alertar(cobranca, texto)
