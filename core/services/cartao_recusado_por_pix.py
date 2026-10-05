"""core/services/cartao_recusado_por_pix.py — assinatura de cartão que nasce
com um Pix pago já cobrindo o período: cancela na hora, e quem cobrou avisa.

Caminho A da cobrança dupla: a sessão de cartão foi aberta antes de o Pix
entrar e concluída depois. O `create-checkout` já recusa quem tem Pix vigente;
este é o par dele para a sessão que passou antes da guarda.

`Subscription.cancel` imediato, nunca `cancel_at_period_end`: o período que o
cartão abriria é o mesmo que o Pix já pagou. A `MARCA` no `comment` é o que o
ramo `customer.subscription.deleted` lê para não mandar "assinatura cancelada"
a quem continua pago.

**Levanta em caso de falha** (vira 5xx e o Stripe reentrega), o mesmo contrato
do `_materializar_assinatura`.
"""

from __future__ import annotations

from core.observability import log_system_event_sync
from core.services import admin_notify

MARCA = "pigbank:pix_vigente"
_JA_MORTA = ("canceled", "incomplete_expired")


def pix_cobre_agora(user_id: int) -> bool:
    """O Pix pago cobre HOJE — a pergunta das guardas do webhook e do
    `create-checkout` (§0.7).

    Grant Pix vigente, OU cobrança paga com a janela contendo agora e o grant
    ainda por nascer: o dreno falhou entre `stripe_cancel` (que não viu cartão)
    e `grant`, e um checkout concluído nesse intervalo passava pelas duas pontas
    e renovava em 30 dias. Grant Pix REVOGADO não conta (`paga_cobrindo_agora`).
    Janela FUTURA não barra: é a migração, em que o Pix começa quando o cartão
    acaba (o `stripe_cancel` a persiste antes de se registrar, então a linha diz
    o mesmo que o grant vai dizer).
    """
    from db.pix_charges_saga import paga_cobrindo_agora
    from frontend.finance_bot_websocket_custom import _grant_pix_vigente  # noqa: PLC0415

    return _grant_pix_vigente(user_id) is not None or paga_cobrindo_agora(user_id)


def recusar_assinatura(stripe_mod, user_id: int, sub_id: str, *, cobrado_cents: int,
                       session_id, cadernos_cents: int | None = 0) -> None:
    """Cancela `sub_id` (se ainda viva), registra e, se o cartão cobrou hoje
    (`cobrado_cents > 0`), pede estorno manual do plano ao admin (decisão E1).

    `cadernos_cents`: `0` = sem cadernos (`cobrado_cents` é o plano); valor = os
    cadernos, fora do `cobrado_cents`; `None` = não deu para separar, e aí
    `cobrado_cents` é o TOTAL. Com cadernos, o estorno do plano espera a
    entrega (E2): estorno antes dela fecha os cadernos como `estornado`
    (`ebook_entrega._compra_estornada`) e o comprador fica sem o que pagou.

    O alerta sai mesmo com a assinatura já cancelada: o `invoice.paid` pode ter
    cancelado antes do checkout chegar, e o dinheiro continua lá. Reentrega do
    checkout pode duplicá-lo — precedente do `notify_new_pro`. Alerta que não
    sai (sem `ADMIN_NOTIFY_WEBHOOK_URL`) sobe o log para `error`, sem 5xx:
    reentregar não faria o webhook do admin existir.
    """
    if stripe_mod.Subscription.retrieve(sub_id)["status"] not in _JA_MORTA:
        stripe_mod.Subscription.cancel(sub_id, cancellation_details={"comment": MARCA})
    enviado = True
    if (cobrado_cents or 0) > 0:
        enviado = admin_notify.notify_pix_alerta(_texto_do_alerta(
            user_id, sub_id, session_id, cobrado_cents, cadernos_cents))
    log_system_event_sync(
        "warning" if enviado else "error", "billing_cartao_recusado_pix",
        "Assinatura de cartao cancelada: um Pix pago ja cobre o periodo."
        + ("" if enviado else " O alerta de estorno NAO saiu."),
        source="billing", user_id=int(user_id),
        details={"sub_id": sub_id, "session_id": session_id,
                 "cobrado_cents": int(cobrado_cents or 0),
                 "cadernos_cents": cadernos_cents})


def _texto_do_alerta(user_id, sub_id, session_id, cobrado_cents, cadernos_cents) -> str:
    cabeca = (f"🚨 **Cartão cobrado com Pix vigente** conta `{user_id}` · sessão "
              f"`{session_id}` · assinatura `{sub_id}` (cancelada) · ")
    if cadernos_cents == 0:
        return cabeca + f"plano cobrado R$ {cobrado_cents / 100:.2f} — ESTORNE à mão."
    valores = (f"total R$ {cobrado_cents / 100:.2f} inclui os cadernos; separe o plano "
               "pela fatura no Stripe" if cadernos_cents is None else
               f"plano R$ {cobrado_cents / 100:.2f} + cadernos R$ {cadernos_cents / 100:.2f}")
    return cabeca + valores + (
        " — ESTORNE à mão SÓ o plano, e só DEPOIS de os cadernos constarem como "
        "enviados: `select ebook_price, resultado from ebook_entregas where "
        f"session_id = '{session_id}'` com `enviado` em todas as linhas (vazio = "
        "ainda não entregue; o job só envia a quem confirmou o e-mail). Estorno "
        "antes fecha os cadernos como `estornado` e o comprador fica sem eles.")
