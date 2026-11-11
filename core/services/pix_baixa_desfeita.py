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
from datetime import datetime, timezone

from psycopg import errors

from core.services import admin_notify, afiliado_pix, asaas
from core.services.billing_access import recompute_entitlement
from core.services.pix_drain_effects import EXECUTORES
from core.services.pix_extras import _PAGO, total_cents
from db.pix_charges import buscar_por_asaas_payment_id, transicionar
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

    def _estado():  # "revoked" por este motivo, "active", ou None
        for g in list_grants(cobranca["user_id"]):
            if g["source"] == "pix" and g["external_ref"] == str(cobranca["id"]):
                if g["status"] == "revoked":
                    return "revoked" if g["revoked_reason"] == f"asaas:{TIPO}" else None
                return g["status"]

    if (cobranca["user_id"] is None or cobranca["status"] not in ("paid", "paid_orphan")
            or not efeito_registrado(payment_id, "baixa_desfeita") or _estado() not in ("revoked", "active")
            or not pagamento_confirmado(payment_id)):
        return False
    with lock_efeito(payment_id, "grant"):
        atual = buscar_por_asaas_payment_id(payment_id) or cobranca  # estorno pode ter vindo antes do lock
        if not (atual["status"] in ("paid", "paid_orphan")
                and efeito_registrado(payment_id, "grant")):
            return False
        estado = _estado()
        if estado == "active":
            # `_grant` commita o grant ANTES do `recompute`: se este levantou, a retentativa
            # acha o grant ativo e só reprojeta (idempotente; nunca reconcede nem toca o grant).
            recompute_entitlement(cobranca["user_id"], origem="pix")
            return False
        if estado != "revoked":
            return False
        EXECUTORES["grant"](cobranca, evt)
    log.warning("pix: grant reconcedido (baixa desfeita, Pix real) cobranca=%s", cobranca["id"])
    _alertar(cobranca, "acesso reconcedido: o Pix real chegou antes da retentativa do UNDONE.")
    return True


def _outro_pagamento(cobranca, payment_id: str) -> bool:
    # #1257: UNDONE de OUTRO pagamento (retido) numa cobrança paga por `antigo`:
    # revogar/reabrir tiraria o acesso de um Pix real.
    antigo = cobranca["asaas_payment_id"]
    return bool(cobranca["status"] in ("paid", "paid_orphan", "refunded_partial")
                and antigo and payment_id != antigo)


def _ignorar_outro(cobranca, payment_id: str) -> None:
    _alertar(cobranca, f"ignorado: baixa desfeita do pagamento `{payment_id}`, "
             f"mas a cobrança foi paga por `{cobranca['asaas_payment_id']}`; nada alterado.")


def _segurar_comissao(cobranca) -> None:
    """Abre pendência `open` (devolvido 0) que bloqueia o saque enquanto o admin decide. Um UNDONE reentregue com a
    cobrança já fora de `paid` não chega aqui. Uma nova baixa desfeita depois que a cobrança voltou a `paid`
    reabre a revisão `resolved` enquanto a comissão não saiu (`desconhecido=True`)."""
    from core.services.afiliado_reembolso import avisar_pendencia
    from db.affiliate_reviews import registrar_reembolso
    from db.affiliates import precisa_do_acumulado

    chave = cobranca["external_reference"]
    if precisa_do_acumulado(chave):
        avisar_pendencia(registrar_reembolso(chave, "partial", 0, datetime.now(timezone.utc),
                                             rotular=False, desconhecido=True))


def desfazer_baixa(cobranca, evt, payment_id: str) -> None:
    if _outro_pagamento(cobranca, payment_id):
        return _ignorar_outro(cobranca, payment_id)
    if pagamento_confirmado(payment_id):  # UNDONE velho, reentregue depois do Pix real
        if reconciliar_grant(cobranca, evt, payment_id):
            return
        return _alertar(cobranca, "ignorado: o Asaas já mostra o pagamento como pago.")
    if cobranca["status"] in ("refunded", "chargeback"):
        return _alertar(cobranca, "cobrança já estornada/contestada, nada reaberto.")
    registrar_efeito(payment_id, "baixa_desfeita", evt["event_id"])
    with lock_efeito(payment_id, "grant"):
        antigo = cobranca["asaas_payment_id"]  # UNDONE de outro pagamento: o id dele não acha a linha
        cobranca = (buscar_por_asaas_payment_id(payment_id)
                    or (antigo and buscar_por_asaas_payment_id(antigo)) or cobranca)  # RECEIVED/estorno mudou o estado antes do lock
        if _outro_pagamento(cobranca, payment_id):
            return _ignorar_outro(cobranca, payment_id)
        status = cobranca["status"]
        if status in ("refunded", "chargeback"):
            return _alertar(cobranca, "cobrança já estornada/contestada, nada reaberto.")
        if pagamento_confirmado(payment_id):  # Pix real drenou entre o GET de cima e o lock
            return _alertar(cobranca, "ignorado: o Asaas passou a mostrar o pagamento como pago.")
        if cobranca["user_id"] is not None:
            EXECUTORES["revoke"](cobranca, evt)
        # O efeito no afiliado vai ANTES da transição de estado: é ela que o retry reconhece, e
        # `registrar_reembolso` é idempotente. Falha aqui deixa tudo como estava, e o retry refaz.
        # grant→afiliado, sempre nesta ordem: o efeito `afiliado` do RECEIVED (pix_drain) já roda sob o
        # lock `afiliado` e nunca pede o `grant` dentro dele. Segura+reabre não intercala com a comissão.
        with lock_efeito(payment_id, "afiliado"):
            if status == "refunded_partial":
                afiliado_pix.marcar(cobranca, "total")
                transicionar(cobranca["id"], de="refunded_partial", para="refunded",
                             asaas_payment_id=payment_id)
                registrar_efeito(payment_id, "revoke", evt["event_id"])
            elif status in ("paid", "paid_orphan"):
                _segurar_comissao(cobranca)
                try:
                    # órfã (sem titular) não vai a pending: o índice/leitores de ativa esperam titular
                    reaberta = reabrir_baixa(cobranca["id"], payment_id,
                                             "pending" if cobranca["user_id"] is not None else "expired")
                except errors.UniqueViolation:
                    reaberta = reabrir_baixa(cobranca["id"], payment_id, "expired")
    if status in ("paid", "paid_orphan"):
        texto = ("acesso revogado, cobrança reaberta." if reaberta
                 else "acesso revogado, mas a cobrança já não estava paga: NÃO foi reaberta; conferir.")
    elif status == "refunded_partial":
        texto = "acesso revogado, cobrança estornada."
    else:
        texto = f"cobrança em {status}: nada concedido nem alterado, só registrado."
    _alertar(cobranca, texto + _aviso_stripe(cobranca, payment_id))


def _aviso_stripe(cobranca, payment_id: str) -> str:
    if cobranca["stripe_subscription_id"] and efeito_registrado(payment_id, "stripe_cancel"):
        return " O cancelamento do cartão no Stripe segue agendado (já executado): conferir no Stripe."
    return ""


def veto_grant(payment_id: str) -> tuple[bool, tuple | None]:
    """Sob o lock do `grant` do RECEIVED: devolve `(parar, falha)`. Sem marcador, vale só o
    estado relido (estorno/UNDONE ganhou a corrida: fecha); com marcador, o Asaas decide:
    não pago fecha com alerta, pago com cobrança reaberta é falha retentável (transiciona e concede)."""
    atual = buscar_por_asaas_payment_id(payment_id)
    pago = atual is None or atual["status"] in ("paid", "paid_orphan", "refunded_partial")
    if not efeito_registrado(payment_id, "baixa_desfeita"):
        return (not pago, None)
    if not pagamento_confirmado(payment_id):
        if atual:
            _alertar(atual, "RECEIVED ignorado: baixa desfeita e o Asaas não mostra pago; acesso NÃO concedido." + _aviso_stripe(atual, payment_id))
        return (True, None)
    return (not pago, None if pago else ("CobrancaReaberta", None))


def veto_afiliado(payment_id: str) -> tuple[bool, tuple | None]:
    """Sob o lock `afiliado` do RECEIVED: o UNDONE completo pode ter reaberto a cobrança depois que o
    dreno leu o estado (a comissão nasceria sacável sobre dinheiro não recebido). Relê; reaberta é falha
    retentável, e a retentativa fecha pela guarda da baixa desfeita."""
    atual = buscar_por_asaas_payment_id(payment_id)
    if atual is not None and atual["status"] not in ("paid", "paid_orphan", "refunded_partial", "refunded"):
        return (True, ("CobrancaReaberta", None))
    return (False, None)
