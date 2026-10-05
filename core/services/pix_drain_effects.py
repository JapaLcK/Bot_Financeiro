"""
core/services/pix_drain_effects.py — o que o dreno do Pix faz no MUNDO LÁ FORA.

Saiu de `core/services/pix_drain.py` por assunto, não por tamanho: lá mora a
máquina — reserva, roteamento, guardas, transição, laço —, e aqui mora o que
cada efeito executa (Stripe, `plan_grants`, cadernos, GA4, Meta CAPI, e-mail), mais a
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
from core.services.pix_extras import total_cents

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
        f"valor R$ {total_cents(cobranca) / 100:.2f}{extra} · "
        "o acesso NÃO foi alterado."
    )


def alertar_stripe_desistido(cobranca) -> None:
    """O fallback do §8.2: na 6ª falha do `stripe_cancel`, o grant sai assim mesmo.

    Stripe fora do ar barrava o `grant` em toda retentativa — dinheiro dentro e
    acesso nenhum, para sempre. O começo do acesso já é
    `max(now, stripe_period_end_at)` desde a transição (`janela_de_acesso`),
    inclusive com a coluna nula, que é o caso da própria indisponibilidade.

    Sobreposição de período é ESTORNÁVEL; acesso negado a quem pagou não é. O
    cartão fica com o cancelamento pendente e o caminho é manual (§9). Sem
    `stripe_subscription_id` a falha foi na CONSULTA: não se sabe se há cartão.
    """
    titulo, acao = "cancelamento no Stripe não entrou", "Cancele a assinatura à mão"
    if not cobranca["stripe_subscription_id"]:
        titulo = "não consegui consultar o Stripe"
        acao = (f"Confira se a conta `{cobranca['user_id']}` tem assinatura viva; "
                "se tiver, cancele à mão")
    admin_notify.notify_pix_alerta(
        f"🚨 **Pix: {titulo}** cobrança `{cobranca['id']}` — 6ª falha, o acesso "
        f"Pix FOI concedido assim mesmo. {acao} e estorne a fatura que renovar."
    )


def alertar_valor(cobranca, valor) -> None:
    """Valor liquidado ≠ total do snapshot (plano + cadernos, `total_cents`) —
    **alerta, e NÃO barra**.

    Barrar era a outra saída possível, e ela é pior aqui: o QR dinâmico carrega
    o valor, então o pagador não escolhe quanto paga. Divergência só nasce de
    edição no painel do Asaas — um desconto que NÓS demos —, e recusar acesso a
    quem pagou o que combinamos é o erro irreversível (§7 do plano).

    `value` ausente ou não numérico sai calado: o corpo minimizado o traz
    sempre, e inventar alerta sobre campo que não veio é ruído.
    """
    try:
        recebido = int(round(float(valor) * 100))
    except (TypeError, ValueError):
        return
    combinado = total_cents(cobranca)
    if recebido != combinado:
        admin_notify.notify_pix_alerta(
            f"⚠️ **Pix: valor diferente do combinado** cobrança `{cobranca['id']}`"
            f" — recebido R$ {recebido / 100:.2f} contra R$ {combinado / 100:.2f}."
            " O acesso FOI concedido; confira a cobrança no painel do Asaas."
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

    Sem `stripe_subscription_id`, ou com a gravada MORTA, pergunta ao Stripe
    (`_stripe_vivo`): achou viva, migra com ela — vai para a coluna, e o começo
    que esperava a morta é desfeito antes de adiar até a viva; nada vivo nem
    gravado = no-op REGISTRADO. Cartão que conclui depois disto o webhook recusa.

    §9 item 4, **nesta ordem**: (1) grava o `current_period_end` com a janela
    ADIADA (`_janela_adiada`) no MESMO update, antes de o efeito contar como
    feito — a retentativa do `grant` relê a linha; (2) `cancel_at_period_end`,
    nunca `delete`, que cortaria hoje o acesso já pago. Gravada morta e nada
    vivo: sem `modify`, e a janela que esperava a morta volta para agora.
    **Levanta se o Stripe recusar**: é o PRIMEIRO efeito porque, sem retentativa,
    ficaria dinheiro dentro com o cartão cobrando, calado. 6ª falha: alerta §8.2.
    """
    import stripe as _stripe
    from core.services.cartao_recusado_por_pix import _JA_MORTA
    from core.services.pix_checkout import _stripe_vivo
    from db.pix_charges_saga import gravar_stripe_period_end
    from frontend.finance_bot_websocket_custom import (  # noqa: PLC0415
        STRIPE_SECRET_KEY, _sg, _sub_period_end_ts,
    )
    # Só handlers HTTP atribuíam a chave; o dreno num processo novo ia sem ela.
    _stripe.api_key = STRIPE_SECRET_KEY
    agora, desfeita = datetime.now(timezone.utc), {}
    sub_id = cobranca["stripe_subscription_id"]
    sub = _stripe.Subscription.retrieve(sub_id) if sub_id else None
    if sub is None or _sg(sub, "status") in _JA_MORTA:
        vivo = _stripe_vivo(cobranca["user_id"])
        if vivo:  # a viva pode acabar ANTES do fim estimado da morta
            desfeita = _janela_adiada(cobranca, agora, True) if sub is not None else {}
            sub_id = vivo[0]
            sub = _stripe.Subscription.retrieve(sub_id)
        elif sub is None:
            return
    ts = _sub_period_end_ts(sub)
    morta = _sg(sub, "status") in _JA_MORTA
    if ts or morta or desfeita:
        fim = (agora if morta or not ts
               else datetime.fromtimestamp(int(ts), tz=timezone.utc))
        janela = desfeita | _janela_adiada(cobranca | desfeita, fim, morta)
        gravar_stripe_period_end(cobranca["id"], sub_id, fim, **janela)
        cobranca.update(janela, stripe_subscription_id=sub_id)
    if not morta:
        _stripe.Subscription.modify(sub_id, cancel_at_period_end=True)
    log_system_event_sync(
        "info", "pix_stripe_cancel_agendado",
        "Assinatura do Stripe agendada para cancelar no fim do periodo pago.",
        source="pix", user_id=cobranca["user_id"],
        details={"charge_id": cobranca["id"], "sub_status": _sg(sub, "status")})


def _janela_adiada(cobranca, fim, morta=False) -> dict:
    """A janela recalculada quando o período do cartão renovou DEPOIS do checkout.

    Só ADIA: `fim` igual ou anterior ao `access_starts_at` gravado devolve `{}`
    — antecipar sobreporia grant com período de cartão pago. Linha sem janela
    (o `CHECK` a proíbe em `paid`) também sai por `{}`: quem a escreve é a
    transição. `morta` (`fim` = agora): só o começo que esperava ela volta.
    """
    inicio = cobranca["access_starts_at"]
    if not inicio or (inicio <= fim or inicio != cobranca["stripe_period_end_at"]
                      if morta else fim <= inicio):
        return {}
    return {"access_starts_at": fim,
            "access_expires_at": fim + timedelta(days=int(cobranca["duration_days"]))}


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


def _ebook(cobranca, evt) -> None:
    """Os cadernos da foto viram pendência em `ebook_entregas` — uma linha por
    caderno, `session_id` = `external_reference` (`pix:<id>`); quem entrega é o job
    `core/services/ebook_entrega.py`. Sem cadernos é no-op REGISTRADO (padrão do
    `stripe_cancel`). O `on conflict do nothing` do `registrar` é a 2ª barreira
    contra reentrega, depois do par `(payment_id, 'ebook')`."""
    from db.ebook_entregas import registrar

    extras = cobranca.get("extras") or []
    if extras:
        registrar(cobranca["user_id"], cobranca["external_reference"],
                  [(e["price"], e["url"]) for e in extras])


def _ga4(cobranca, evt) -> None:
    """`purchase` com `transaction_id = public_token` — a chave de dedupe do
    GA4, e o único identificador da cobrança que sai do servidor (§13.6)."""
    from core.services.ga4_mp import fallback_client_id, mp_configured, send_purchase
    from core.services.plan_service import tier_publico

    if not mp_configured():
        return
    send_purchase(
        transaction_id=cobranca["public_token"],
        value=total_cents(cobranca) / 100,
        currency=cobranca["currency"] or "BRL",
        # PÚBLICO, e esta é a linha de RECEITA: `send_purchase` usa este valor
        # como `item_id` E `item_name` do evento `purchase`. O Stripe já manda o
        # público no mesmo campo (`_ga_plano_publico`), então o legado da coluna
        # fazia o Pro virar dois produtos no relatório — e, pior, o Plus do Pix
        # (`pro`, R$ 199) caía na MESMA linha do Pro do Stripe (`pro`, R$ 39,90).
        # É o achado do Codex no #244, do lado do Pix.
        plan=tier_publico(cobranca["plan"]),
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
        value=total_cents(cobranca) / 100,
        currency=cobranca["currency"] or "BRL",
        email=_email_do_titular(cobranca["user_id"]),
        fbp=cobranca["fbp"], fbc=cobranca["fbc"],
        event_source_url=f"{os.getenv('DASHBOARD_URL') or 'https://pigbankai.com'}/home",
    )


def _email(cobranca, evt) -> None:
    """Confirmação da compra. O registro fecha a janela normal; a residual (cair
    ENTRE enviar e registrar) é coberta pelo `recent_event_exists`, o mesmo
    padrão do `_fire_email` — e-mail duplicado é o pior caso aceitável.

    **`False` é FALHA, e levanta.** `send_email` nunca lança: Resend fora do ar
    e Resend não configurado saem os dois por `return False`
    (`core/services/email_service.py:72` e `:100`). Ignorar o retorno registrava
    o efeito `email` como feito com zero e-mail enviado, e o par
    `(payment_id, 'email')` **nunca é purgado** — o cliente pagante ficaria sem
    confirmação para sempre. É o mesmo `if not ok` do `_fire_email`
    (`frontend/finance_bot_websocket_custom.py:5069`), que existe pelo mesmo
    motivo. `ponytail:` teto — sem `RESEND_API_KEY` o evento retenta para
    sempre; quem quiser o no-op registrado (padrão de `ga4`/`capi`) precisa de
    uma leitura de env que hoje mora só no `email_service` (§0.7).

    O e-mail do fundador vem depois e nunca levanta: não pode fazer o efeito
    retentar nem repetir a confirmação.
    """
    from core.services.email_service import send_founder_email_once, send_pix_paid_email

    destino = _email_do_titular(cobranca["user_id"])
    if not destino:
        return
    if not recent_event_exists("pix_paid_email_sent", cobranca["user_id"], 1.0):
        if not send_pix_paid_email(destino, cobranca["plan"],
                                   int(cobranca["amount_cents"]) / 100,
                                   cobranca["access_starts_at"],
                                   cobranca["access_expires_at"],
                                   extras=[(e["nome"], int(e["valor_cents"]))
                                           for e in cobranca.get("extras") or []]):
            raise RuntimeError("send_pix_paid_email devolveu False")
        log_system_event_sync("info", "pix_paid_email_sent",
                              "E-mail de confirmacao da compra Pix enviado.",
                              source="pix", user_id=cobranca["user_id"])
    send_founder_email_once(cobranca["user_id"], destino, "pix", str(cobranca["id"]))


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


EXECUTORES = {"stripe_cancel": _stripe_cancel, "grant": _grant, "ebook": _ebook, "ga4": _ga4,
               "capi": _capi, "email": _email, "revoke": _revoke}
