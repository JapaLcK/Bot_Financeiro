"""
core/services/pix_drain_effects.py — o que o dreno do Pix faz no MUNDO LÁ FORA.

Saiu de `core/services/pix_drain.py` por assunto, não por tamanho: lá mora a
máquina — reserva, roteamento, guardas, transição, laço —, e aqui mora o que
cada efeito executa (Stripe, `plan_grants`, GA4, Meta CAPI, e-mail), mais a
janela de acesso que o `grant` grava e o alerta do que NÃO tem efeito
automático.

Plano: docs/plano_pix_anual_asaas.md §7, §8.2 e §12.

**Cada função aqui LEVANTA em caso de falha, e é o contrato.** Quem chama
converte a exceção em `attempts` + `last_error` e sai sem carimbar
`processed_at`, para a próxima passada retomar no efeito que faltou. Efeito que
engole o próprio erro é dinheiro dentro sem acesso, calado.

**Nenhuma delas consulta `pix_payment_effects`.** Quem decide se o efeito roda é
o laço do dreno, sob `lock_efeito` — repetir a consulta aqui daria a impressão
de guarda onde não há.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from core.crypto import PiiAccessContext, decrypt_pii_optional
from core.observability import log_system_event_sync, recent_event_exists
from core.services import admin_notify

def janela_de_acesso(cobranca) -> tuple[datetime, datetime]:
    """Quando o acesso comprado começa e termina (§7), decidido NO PAGAMENTO.

    Derivada do próprio snapshot, sem reconsultar `plano_da_cobranca`: chamá-lo
    de novo aqui poderia levantar `CoberturaJaPaga` sobre dinheiro que JÁ entrou,
    que é o erro irreversível.

      credit_cents > 0              → upgrade Pix→Pix: começa agora
      stripe_subscription_id        → migração: começa no fim do período pago
      resto                         → renovação/agendada: emenda no último grant

    `ponytail:` teto — upgrade Pix→Pix cujo crédito arredonde para 0 é
    classificado como renovação e começa no fim da cobertura em vez de hoje.
    Custa dias ao cliente, não dinheiro; o upgrade é persistir a janela na
    criação, o que exige mexer no `criar_cobranca`.
    """
    from db.plan_grants import list_grants

    agora = datetime.now(timezone.utc)
    if int(cobranca["credit_cents"] or 0) > 0:
        inicio = agora
    elif cobranca["stripe_subscription_id"]:
        inicio = max(agora, cobranca["stripe_period_end_at"] or agora)
    else:
        fins = [g["ends_at"] for g in list_grants(cobranca["user_id"])
                if g["status"] == "active" and g["ends_at"]]
        inicio = max([agora] + fins)
    return inicio, inicio + timedelta(days=int(cobranca["duration_days"]))


def alertar(tipo: str, cobranca, payment_id: str) -> None:
    """Alerta acionável para o que NÃO tem efeito automático.

    No estorno parcial vai o **devolvido acumulado × o valor da cobrança** — e o
    acumulado vem do Asaas (`buscar_pagamento`), porque ele não é derivável do
    que sobrevive à minimização e à purga. Asaas fora → sai
    `acumulado indisponível`, **nunca um número inventado**.

    Não é efeito registrado, então reentrega pode duplicá-lo: mesmo precedente
    do `notify_new_pro`, e está escrito aqui em vez de descoberto na revisão.
    """
    extra = ""
    if tipo == "PAYMENT_PARTIALLY_REFUNDED":
        extra = f" · devolvido: {_acumulado_estornado(payment_id)}"
    admin_notify.notify_pix_alerta(
        f"⚠️ **Pix: {tipo}** cobrança `{cobranca['id']}` · "
        f"valor R$ {int(cobranca['amount_cents']) / 100:.2f}{extra} · "
        "o acesso NÃO foi alterado."
    )


def _acumulado_estornado(payment_id: str) -> str:
    from core.services.asaas import buscar_pagamento

    try:
        estornos = buscar_pagamento(payment_id).get("refunds")
    except Exception:  # noqa: BLE001 — provedor fora não pode virar número
        return "acumulado indisponível"
    if not isinstance(estornos, list):
        return "acumulado indisponível"
    total = sum(float(e.get("value") or 0) for e in estornos if isinstance(e, dict))
    return f"R$ {total:.2f}"


# ── os efeitos ───────────────────────────────────────────────────────────────

def _stripe_cancel(cobranca, evt) -> None:
    """Migração Stripe→Pix: reconfirma o período pago e agenda o cancelamento.

    Compra Pix comum (`stripe_subscription_id is null`) é **no-op REGISTRADO**:
    não chama o Stripe, não conta `attempts`, não alerta — registra o par e o
    laço segue para o `grant`. É a esmagadora maioria das vendas.

    Na migração, os dois passos do §9 item 4, **nesta ordem**:

      1. lê o `current_period_end` de verdade e grava em `stripe_period_end_at`;
      2. `cancel_at_period_end=True` — nunca `Subscription.delete`, que cortaria
         hoje o acesso que o cliente já pagou até o fim do mês.

    **A janela de acesso NÃO é recalculada aqui**, e é o certo: ela foi decidida
    na transição para `paid`, a partir da estimativa que o checkout gravou.
    Reescrevê-la seria evento de pagamento mexendo em grant (§6 proíbe), e o
    grant nasce no efeito SEGUINTE — ele leria uma linha que este efeito acabou
    de mover debaixo dele.

    **Levanta se o Stripe recusar**, e é por isso que este é o PRIMEIRO efeito da
    lista: sem `attempts` + retentativa, ficaria dinheiro dentro com o cartão
    ainda cobrando, calado. A promessa do §9 é condicional — se o agendamento
    nunca entrar, o alerta sai e o caminho é estorno manual daquela fatura; o
    cliente não fica sem acesso porque o grant Pix começa depois do período pago.
    """
    from db.pix_charges_saga import gravar_stripe_period_end

    sub_id = cobranca["stripe_subscription_id"]
    if not sub_id:
        return
    import stripe as _stripe
    from frontend.finance_bot_websocket_custom import (  # noqa: PLC0415
        _sub_period_end_ts,
    )
    sub = _stripe.Subscription.retrieve(sub_id)
    ts = _sub_period_end_ts(sub)
    if ts:
        gravar_stripe_period_end(cobranca["id"],
                                 datetime.fromtimestamp(int(ts), tz=timezone.utc))
    _stripe.Subscription.modify(sub_id, cancel_at_period_end=True)
    log_system_event_sync(
        "info", "pix_stripe_cancel_agendado",
        "Assinatura do Stripe agendada para cancelar no fim do periodo pago.",
        source="pix", user_id=cobranca["user_id"],
        details={"charge_id": cobranca["id"]})


def _grant(cobranca, evt) -> None:
    """O direito, e o funil. `source='pix'` é criação única (§6)."""
    from db.checkout_funnel import record_checkout_completed
    from db.plan_grants import upsert_grant
    from core.services.billing_access import recompute_entitlement

    upsert_grant(cobranca["user_id"], "pix", str(cobranca["id"]),
                 cobranca["plan_stored"], cobranca["access_starts_at"],
                 cobranca["access_expires_at"], int(evt["event_version"]),
                 last_event_id=evt["event_id"])
    recompute_entitlement(cobranca["user_id"], origem="pix")
    record_checkout_completed(cobranca["user_id"], cobranca["public_token"])


def _ga4(cobranca, evt) -> None:
    """`purchase` com `transaction_id = public_token` — a chave de dedupe do
    GA4, e o único identificador da cobrança que sai do servidor (§13.6)."""
    from core.services.ga4_mp import fallback_client_id, mp_configured, send_purchase

    if not mp_configured():
        return
    send_purchase(
        transaction_id=cobranca["public_token"],
        value=int(cobranca["amount_cents"]) / 100,
        currency=cobranca["currency"] or "BRL",
        plan=cobranca["plan"],
        client_id=cobranca["ga_client_id"] or fallback_client_id(cobranca["user_id"]),
        user_id=cobranca["user_id"],
    )


def _capi(cobranca, evt) -> None:
    """`Purchase` na CAPI com `event_id = purchase_<public_token>` — é o que
    deduplica contra o pixel do navegador (§13.6)."""
    from core.services.meta_capi import capi_configured, purchase_event_id, send_event

    if not capi_configured():
        return
    send_event(
        event_name="Purchase",
        event_id=purchase_event_id(cobranca["public_token"]),
        event_time=int(datetime.now(timezone.utc).timestamp()),
        value=int(cobranca["amount_cents"]) / 100,
        currency=cobranca["currency"] or "BRL",
        email=_email_do_titular(cobranca["user_id"]),
        fbp=cobranca["fbp"], fbc=cobranca["fbc"],
        event_source_url=f"{os.getenv('DASHBOARD_URL') or 'https://pigbankai.com'}/home",
    )


def _email(cobranca, evt) -> None:
    """Confirmação da compra. O registro fecha a janela normal; a residual (cair
    ENTRE enviar e registrar) é coberta pelo `recent_event_exists`, o mesmo
    padrão do `_fire_email` — e-mail duplicado é o pior caso aceitável."""
    from core.services.email_service import send_pix_paid_email

    destino = _email_do_titular(cobranca["user_id"])
    if not destino or recent_event_exists("pix_paid_email_sent",
                                          cobranca["user_id"], 1.0):
        return
    send_pix_paid_email(destino, int(cobranca["amount_cents"]) / 100,
                        cobranca["access_expires_at"])
    log_system_event_sync("info", "pix_paid_email_sent",
                          "E-mail de confirmacao da compra Pix enviado.",
                          source="pix", user_id=cobranca["user_id"])


def _revoke(cobranca, evt) -> None:
    """Estorno TOTAL e chargeback perdido. Não há restauração de snapshot: o
    acesso passa a ser o que os grants restantes sustentam (§12)."""
    from db.plan_grants import revoke_grant
    from core.services.billing_access import recompute_entitlement

    if cobranca["user_id"] is None:
        return
    revoke_grant(cobranca["user_id"], "pix", str(cobranca["id"]),
                 f"asaas:{evt['event_type']}", int(evt["event_version"]),
                 last_event_id=evt["event_id"])
    recompute_entitlement(cobranca["user_id"], origem="pix_estorno")


def _email_do_titular(user_id: int) -> str | None:
    from db import get_auth_user

    conta = get_auth_user(user_id) or {}
    if conta.get("email_enc"):
        return decrypt_pii_optional(conta["email_enc"], ctx=PiiAccessContext(
            purpose="pix_paid_email", actor="system:pix_drain",
            subject_user_id=user_id, field="email"))
    return conta.get("email")


EXECUTORES = {"stripe_cancel": _stripe_cancel, "grant": _grant, "ga4": _ga4,
               "capi": _capi, "email": _email, "revoke": _revoke}
