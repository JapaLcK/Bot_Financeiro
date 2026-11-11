"""
core/services/afiliado_pix.py — o lado do afiliado no dreno do Pix.

Pagante, comissão e reembolso do indicado. A comissão e o estorno passam pela mesma máquina
do cartão (`db/affiliates.py` e `db/affiliate_reviews.py`), com a chave `pix:<id>`. Saiu de
`pix_drain_effects.py` por assunto. Como os outros efeitos, levanta em caso de falha:
quem chama transforma a exceção em retentativa.
"""

from __future__ import annotations

from datetime import datetime, timezone


def registrar(cobranca, evt) -> None:
    """Conversão do indicado: só o plano (`amount_cents`), nunca os cadernos. Depois, a
    comissão da primeira cobrança (1/12 do pago), exceto na cobrança já estornada por
    inteiro: aí o pagamento fica no histórico, encerra a elegibilidade e não comissiona.
    O rótulo no fim é da retentativa: o reembolso que passou antes não achou o que marcar."""
    from core.observability import log_system_event_sync
    from core.services.pix_pricing import MESES_DO_PIX
    from db.affiliate_payments import registrar_pagamento
    from db.affiliates import record_commission_for_pix

    registrar_pagamento(cobranca["user_id"], "pix", str(cobranca["id"]),
                        int(cobranca["amount_cents"]),
                        cobranca["paid_at"] or datetime.now(timezone.utc))
    if cobranca["status"] != "refunded" and (comissao := record_commission_for_pix(cobranca, MESES_DO_PIX)):
        log_system_event_sync(
            "info", "affiliate_commission_created",
            f"Comissao de afiliado criada: R$ {comissao['amount_cents'] / 100:.2f}.",
            source="affiliates", user_id=cobranca["user_id"],
            details={"affiliate_id": comissao["affiliate_id"], "charge_id": cobranca["id"],
                     "amount_cents": comissao["amount_cents"]})
    # Sem as tentativas da compra: gastas por outra falha, abririam `open/0` na 1ª falha do Asaas. A
    # compra retenta sozinha (só o `afiliado` falta) até o valor sair; a purga de 7 dias é o limite.
    rotular_reembolso(cobranca, cobranca["status"])


def estornado_cents(payment_id: str) -> int:
    """O devolvido acumulado do pagamento, em centavos: a soma de `refunds[].value` do Asaas
    (`buscar_pagamento`). Item com `status` conta só se `DONE` (sem diferenciar maiúscula); `CANCELLED` é
    pulado; qualquer outro (`PENDING`...) levanta, pois o acumulado ainda vai crescer; sem `status`, conta
    (forma a conferir no sandbox). Cada `value` passa pela regra
    do `value` do webhook (`centavos_do_valor`: número finito, >= 0, até 2 casas). Levanta com o Asaas
    fora ou com qualquer forma inesperada: nunca um número inventado."""
    from core.services.asaas import buscar_pagamento
    from core.services.pix_valor import centavos_do_valor

    estornos = buscar_pagamento(payment_id).get("refunds")
    if not isinstance(estornos, list) or not all(isinstance(e, dict) for e in estornos):
        raise ValueError("refunds do Asaas em forma inesperada")
    total = 0
    for e in estornos:
        if (st := str(e["status"]).upper() if e.get("status") is not None else "DONE") != "DONE":
            if st == "CANCELLED":
                continue
            raise ValueError("estorno do Asaas ainda não concluído")
        cents = centavos_do_valor(e.get("value"))
        if cents is None:
            raise ValueError("value de refunds do Asaas inválido")
        total += cents
    return total


def marcar(cobranca, tipo: str, tentativas: int = 0) -> None:
    """Reembolso do Pix pela máquina do cartão: rótulo do pagamento, pendência e estorno da
    comissão numa transação (`registrar_reembolso`), e o aviso ao admin. Sempre, como na Stripe:
    sem comissão vira `no_commission`, que `promover` resolve se ela nascer depois.
    Devolvido: no total, o cobrado (plano + cadernos). No parcial, o acumulado do Asaas, só quando
    ele decide algo (`precisa_do_acumulado`: comissão não estornada, ou a paga de afiliado excluído);
    fora do ar, em forma inesperada, com refund não `DONE`/`CANCELLED` ou ainda zerado (estorno não
    concluído), levanta e vira retentativa, até `TENTATIVAS_ATE_TRAVAR` (`tentativas` = falhas
    anteriores do evento de parcial, ou do órfão; a compra passa 0): cerca de 5 minutos com o dreno
    passando a cada 60 s. Daí segue com `desconhecido=True` e a revisão vai a `open` com o acumulado
    já registrado (0 no 1º parcial; o valor anterior no 2º, pois `registrar_reembolso` usa o maior),
    que bloqueia o saque e avisa o admin, em vez de esperar a purga fechar o evento calado. Estorno do
    Asaas que leve mais que isso para ficar `DONE` acaba em `open`, e o admin decide pelo Asaas. Sem isso, devolvido 0: o rótulo sai igual e o parcial não depende do
    Asaas. Tabela de estados × eventos: `docs/CLAUDE.md`, "Afiliado no dreno do Pix".
    `ponytail:` comissão que nasce entre a checagem e a trava fica em revisão com devolvido 0
    (`open/0`, do lado seguro: bloqueia o saque). Só um parcial seguinte com acumulado maior, ou a
    retentativa da compra que já lê `refunded_partial`, corrige o valor; senão o admin decide pelo Asaas.
    Dois efeitos, um por `tipo`: o dreno deduplica por `(pagamento, efeito)`, e com
    um nome só o total depois do parcial seria pulado."""
    from core.services.afiliado_reembolso import avisar_pendencia
    from core.services.pix_extras import total_cents
    from db.affiliate_reviews import registrar_reembolso
    from core.services.pix_drain import TENTATIVAS_ATE_TRAVAR
    from db.affiliates import precisa_do_acumulado

    devolvido, desconhecido = 0, False
    if tipo == "total":
        devolvido = total_cents(cobranca)
    elif precisa_do_acumulado(cobranca["external_reference"]):
        try:
            devolvido = estornado_cents(cobranca["asaas_payment_id"])
            if devolvido <= 0:
                raise ValueError("estorno parcial sem refunds concluídos no Asaas")
        except Exception:
            if tentativas < TENTATIVAS_ATE_TRAVAR:
                raise
            devolvido, desconhecido = 0, True
    avisar_pendencia(registrar_reembolso(cobranca["external_reference"], tipo, devolvido,
                                         cobranca["refunded_at"] or datetime.now(timezone.utc),
                                         desconhecido=desconhecido))


def estornar(cobranca, evt) -> None:
    """Efeito `afiliado_estorno`. `refunded` prova que houve `paid`; com o `afiliado` pendente, é
    aqui que o pagante nasce, porque depois do `revoke` a guarda D3 fecha a compra. Fora de
    `refunded` (`chargeback`, que não transiciona, ou o estorno fora de ordem em `pending`) só
    `marcar`: a máquina age sobre o que existir, rótulo e comissão."""
    if cobranca["status"] == "refunded":
        registrar(cobranca, evt)
    else:
        marcar(cobranca, "total")


def rotular_reembolso(cobranca, status: str | None, tentativas: int = 0) -> None:
    """Rótulo pelo estado da cobrança, sem registro de efeito (é idempotente): serve à
    conta excluída, que não passa pelo laço, e à retentativa de `registrar`."""
    tipo = {"refunded": "total", "refunded_partial": "partial"}.get(status)
    if tipo:
        marcar(cobranca, tipo, tentativas)


def efeitos_do_laco(efeitos, cobranca, payment_id) -> tuple:
    """O rótulo do parcial só roda com a cobrança em `refunded_partial` e com id: parcial
    fora de ordem é zero efeito, e sem id não há par `(pagamento, efeito)` para registrar."""
    if cobranca["status"] == "refunded_partial" and payment_id:
        return efeitos
    return tuple(e for e in efeitos if e != "afiliado_estorno_parcial")
