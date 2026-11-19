"""
core/services/pix_valor.py — o valor pago confere com o devido? (issue #1228)

Pagamento parcial NÃO libera o plano: o dreno (`pix_drain._decidir`) só segue
para `paid` + efeitos de compra quando o `value` do webhook cobre o
`total_cents` da cobrança (plano + cadernos, já líquido do crédito de upgrade).
Parcial, valor ausente ou ilegível: a cobrança fica como está, o operador é
alertado e o evento é FECHADO — nunca preso na outbox. Um alerta por lado
(retido / concedido) por pagamento.

Excesso concede e alerta (o operador devolve a diferença à mão); pagamento
antes retido e depois completado também alerta, para ninguém devolver. Soma de
parciais e "desconto autorizado" não são reconhecidos automaticamente: o
operador concede em /admin (runbook em `docs/CLAUDE.md`).

Cobrança fora das origens (`paid`, estornada) com `payment_id` DIFERENTE do dela
(#1257): alerta e retém, para não repetir Purchase/e-mail sob o id novo.
"""

from __future__ import annotations

from decimal import Decimal

from core.observability import log_system_event_sync
from core.services import admin_notify
from core.services.pix_extras import total_cents
from db.pix_effects import efeito_registrado, lock_efeito, registrar_efeito

TOLERANCIA_CENTAVOS = 0  # D1 do dono: 498,99 retém, 499,00 concede


def centavos_do_valor(valor) -> int | None:
    """Reais do Asaas -> centavos inteiros. `None` = ilegível.

    Aceita só `int`/`float` JSON finito, >= 0, até um bigint de centavos e com no
    máximo 2 casas: 498.995 NÃO arredonda para cobrir. `Decimal(repr(valor))` em
    vez de `float * 100`: `1e308` e `inf` não viram `OverflowError` (que prendia
    o evento na outbox).
    """
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return None
    if valor != valor or valor in (float("inf"), float("-inf")) or valor < 0:
        return None
    cents = Decimal(repr(valor)) * 100
    # Acima de um bigint de centavos não é quantia real (1e308 = dado corrompido).
    if cents != cents.to_integral_value() or cents > 2**63 - 1:
        return None
    return int(cents)


def valor_pago_cobre_devido(pago_cents: int | None, devido_cents: int,
                            tolerancia_cents: int = TOLERANCIA_CENTAVOS) -> bool:
    """`None` ("não sei") nunca decide dinheiro: não cobre."""
    return pago_cents is not None and pago_cents + tolerancia_cents >= devido_cents


def _alertar_uma_vez(cobranca, payment_id, event_id, mensagem: str, *,
                     marcador: str) -> None:
    """Dois marcadores, um por lado: `valor_alertado` (retido) e
    `valor_concedido_alertado` (excesso ou retido-e-completado).

    Alerta ANTES de registrar (o `_orfao` faz o inverso): perder o aviso de
    dinheiro retido é pior que duplicá-lo se o processo morrer entre os dois.
    O marcador só vale com entrega CONFIRMADA: `notify_pix_alerta` devolve `False`
    (webhook vazio ou não-2xx) sem levantar, e marcar assim silenciava o caso
    para sempre; sem marcador o próximo evento tenta de novo, e o log durável
    registra a falha. Sem `payment_id` (não deveria ocorrer) não há onde marcar.
    """
    def _entregar() -> bool:
        if admin_notify.notify_pix_alerta(mensagem):
            return True
        log_system_event_sync(
            "error", "pix_alerta_nao_entregue",
            "Alerta de valor do Pix não foi entregue ao canal do admin.",
            source="pix", user_id=cobranca["user_id"],
            details={"charge_id": cobranca["id"]})
        return False

    if not payment_id:
        _entregar()
        return
    with lock_efeito(payment_id, marcador):
        if not efeito_registrado(payment_id, marcador) and _entregar():
            registrar_efeito(payment_id, marcador, event_id)


ESTORNOS_E_CHARGEBACK = (
    "PAYMENT_REFUNDED", "PAYMENT_PARTIALLY_REFUNDED", "PAYMENT_REFUND_IN_PROGRESS",
    "PAYMENT_REFUND_DENIED", "PAYMENT_CHARGEBACK_REQUESTED", "PAYMENT_CHARGEBACK_DISPUTE",
    "PAYMENT_AWAITING_CHARGEBACK_REVERSAL")


def estorno_de_outro_pagamento(tipo, cobranca, payment_id) -> bool:
    """#1435: estorno/chargeback do pagamento DUPLICADO (id que a cobrança nunca registrou).

    Sem isto o evento caía em `external_reference`, achava a cobrança boa e a revogava.
    `True` = alerta e o dreno FECHA o evento sem transição nem efeito.
    """
    antigo = cobranca["asaas_payment_id"]
    if tipo not in ESTORNOS_E_CHARGEBACK or not payment_id or not antigo or payment_id == antigo:
        return False
    log_system_event_sync(
        "warning", "pix_estorno_de_outro_pagamento",
        "Estorno/chargeback de OUTRO pagamento da cobrança: nada alterado.",
        source="pix", user_id=cobranca["user_id"],
        details={"charge_id": cobranca["id"], "status": cobranca["status"],
                 "payment_id": payment_id, "event_type": tipo})
    admin_notify.notify_pix_alerta(
        f"⚠️ **Pix: {tipo} de outro pagamento** na cobrança `{cobranca['id']}`"
        f" (`{cobranca['status']}`) — pagamento `{payment_id}`; a cobrança foi paga por"
        f" `{antigo}`. Acesso e afiliado NÃO foram alterados. Confira no Asaas.")
    return True


def conferir_valor(cobranca, origens, valor, payment_id, event_id) -> bool:
    """`True` = o dreno segue; `False` = RETIDO (o dreno fecha o evento)."""
    if cobranca["status"] not in origens:
        antigo = cobranca["asaas_payment_id"]
        if payment_id and antigo and payment_id != antigo:
            # #1257: OUTRO pagamento numa cobrança já paga/estornada: efeitos de compra
            # rodariam de novo sob o payment_id novo (2º Purchase). Fecha sem efeitos.
            pago = centavos_do_valor(valor)
            log_system_event_sync(
                "warning", "pix_pagamento_duplicado",
                "Segundo pagamento Pix na mesma cobrança: nenhum efeito de compra.",
                source="pix", user_id=cobranca["user_id"],
                details={"charge_id": cobranca["id"], "status": cobranca["status"],
                         "payment_id": payment_id,
                         "pago_cents": pago if pago is not None else "ilegivel"})
            recebido = f"R$ {pago / 100:.2f}" if pago is not None else "valor ilegível"
            _alertar_uma_vez(
                cobranca, payment_id, event_id,
                f"⚠️ **Pix: segundo pagamento na mesma cobrança** `{cobranca['id']}`"
                f" (`{cobranca['status']}`) — pagamento `{payment_id}` de {recebido};"
                " nenhum efeito de compra rodou. Confira no Asaas e devolva.",
                marcador="pagamento_duplicado_alertado")
            return False
        return True  # reentrega (`paid`): sem alerta falso
    pago = centavos_do_valor(valor)
    devido = total_cents(cobranca)
    detalhes = {"charge_id": cobranca["id"], "devido_cents": devido,
                "pago_cents": pago if pago is not None else "ilegivel"}
    if valor_pago_cobre_devido(pago, devido):
        # Sem lock: só escolhe o texto. Retido antes (baixa manual desfeita e
        # refeita) e agora concedido: o alerta de "retido" não vale mais.
        retido_antes = efeito_registrado(payment_id, "valor_alertado")
        valores = (f" cobrança `{cobranca['id']}` — recebido R$ {pago / 100:.2f}"
                   f" contra R$ {devido / 100:.2f}.")
        if retido_antes:
            # Rastro durável quando o retido foi ENTREGUE (só então há marcador): o
            # alerta deste lado não é retentado depois de `paid`. Sem entrega, só a
            # coluna `completado_pelo_mesmo_pagamento` do runbook mostra.
            log_system_event_sync(
                "warning", "pix_retido_completado",
                "Pagamento Pix antes retido foi completado: acesso concedido,"
                " NÃO devolver o valor inteiro.",
                source="pix", user_id=cobranca["user_id"], details=detalhes)
        if pago > devido:
            # Rastro durável: o excesso CONCEDE o plano e o canal do admin pode falhar.
            log_system_event_sync(
                "warning", "pix_excesso_recebido",
                "Pagamento Pix maior que o devido: acesso concedido, devolver a diferença.",
                source="pix", user_id=cobranca["user_id"], details=detalhes)
            mensagem = ("⚠️ **Pix: valor maior que o combinado**" + valores
                        + " O acesso FOI concedido; devolva a diferença no painel do Asaas.")
            if retido_antes:
                mensagem += (" Este pagamento tinha sido retido antes: devolva só a"
                             f" diferença de R$ {(pago - devido) / 100:.2f}, não o valor"
                             " inteiro.")
        elif retido_antes:
            mensagem = ("⚠️ **Pix: pagamento antes RETIDO foi completado**" + valores
                        + " O acesso FOI concedido agora. NÃO devolva nem cobre diferença;"
                        " desconsidere o alerta de parcial retido deste pagamento.")
        else:
            return True
        _alertar_uma_vez(cobranca, payment_id, event_id, mensagem,
                         marcador="valor_concedido_alertado")
        return True
    log_system_event_sync(
        "warning", "pix_pagamento_parcial_retido",
        "Pagamento Pix menor que o devido (ou valor ilegível): acesso NÃO concedido.",
        source="pix", user_id=cobranca["user_id"],
        details={**detalhes, "payment_id": payment_id})  # o runbook compara com a cobrança
    recebido = f"R$ {pago / 100:.2f}" if pago is not None else "valor ilegível"
    _alertar_uma_vez(
        cobranca, payment_id, event_id,
        f"🚨 **Pix: pagamento parcial retido** cobrança `{cobranca['id']}`"
        f" ({cobranca['status']}) — recebido {recebido} contra R$ {devido / 100:.2f}"
        " devidos. Acesso, cadernos e e-mail NÃO foram enviados. Se o desconto foi"
        " autorizado: conceda o plano em /admin e envie os cadernos à mão. Senão:"
        " devolva o valor no Asaas ou cobre a diferença à parte. Depois feche a"
        " cobrança (runbook em docs/CLAUDE.md).", marcador="valor_alertado")
    return False


def parcial_vira_total(evt, cobranca, payment_id) -> dict:
    """#1571: parciais DONE que somam >= o devido são estorno TOTAL (marcador, revoke, afiliado).

    Marcador do #1437 gravado POR ESTE evento (retentativa depois da transição): promove sem ler, em
    qualquer status, senão o `revoke` que falhou seria perdido. Parcial atrasado depois de um REFUNDED
    real (marcador de outro evento) segue parcial (#1503). Cobrança refunded/canceled/expired: não lê.
    Lê `refunds[]` uma vez por evento (memo no `evt`, #1504). Leitura falhou (Asaas fora, forma
    inesperada): segue parcial, com o alerta "acumulado indisponível" (contrato do #1504; revisão manual).
    """
    from core.services import afiliado_pix
    from db.pix_estornos import gravado_pelo_evento

    if evt["event_type"] != "PAYMENT_PARTIALLY_REFUNDED" or not payment_id:
        return evt
    if gravado_pelo_evento(payment_id, evt["event_id"]):  # já promovido; a passada anterior falhou
        return {**evt, "event_type": "PAYMENT_REFUNDED"}
    if cobranca["status"] in ("refunded", "canceled", "expired"):
        return evt
    try:
        concluido, _ = afiliado_pix.somar_estornos(afiliado_pix.ler_estornos(payment_id, evt))
    except Exception:  # noqa: BLE001
        return evt
    if concluido < total_cents(cobranca):
        return evt
    log_system_event_sync(
        "warning", "pix_parciais_somam_total", "Estornos parciais somam o valor devido: tratados como total.",
        source="pix", user_id=cobranca["user_id"], details={"charge_id": cobranca["id"]})
    return {**evt, "event_type": "PAYMENT_REFUNDED"}
