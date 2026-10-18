"""core/services/stripe_exclusao.py — cancela na Stripe as assinaturas de cartão de
uma conta que está sendo excluída (S2), na hora e SEM reembolso, e expira antes os
checkouts abertos do customer (senão um checkout pago depois da exclusão criaria
assinatura viva sem conta). QR Pix pendente continua fora.

Chamado por `db.privacy.excluir_conta_marcada`, o corpo comum da rota e do job, sob o
advisory lock do checkout do usuário (uma conta em `processing` não ganha checkout novo
depois dele: a guarda está em `_billing_user_lock`). O
customer vem só de `auth_accounts WHERE user_id` da própria conta. **Levanta em
qualquer dúvida** (sem chave, listagem falha, sobrou checkout aberto ou assinatura
viva, checkout ou assinatura de outro usuário): quem chama devolve a conta a `scheduled` e o job tenta de novo.
Ordem: primeiro LÊ checkouts abertos e assinaturas vivas de todos os customers e checa o dono
de cada item; só então escreve (expira, solta/cancela), customer a customer.
"""
from __future__ import annotations

import logging
import os

from core.services.cartao_recusado_por_pix import _JA_MORTA
from core.services.stripe_sdk import _is_missing_stripe_customer, _sg
from db.connection import get_conn

# Lida pelo ramo `customer.subscription.deleted` do webhook: cancelamento NOSSO,
# sem e-mail "assinatura cancelada, acesso até X" para a conta que está sumindo.
MARCA_EXCLUSAO = "pigbank:exclusao_conta"
_log = logging.getLogger(__name__)


def _checar_dono(itens: list, user_id: int, o_que: str) -> list:
    for it in itens:   # falha fechada: nada é escrito se algum item é de outro usuário
        dono = _sg(_sg(it, "metadata"), "finbot_user_id")
        if dono is not None and str(dono) != str(user_id):
            raise RuntimeError(f"{o_que} de outro usuario no customer")
    return itens


def _abertas(stripe, customer: str, user_id: int) -> list:
    try:
        # ponytail: 1 página (100); customer com >100 checkouts abertos é fora da realidade.
        r = stripe.checkout.Session.list(customer=customer, status="open", limit=100)
    except Exception as exc:  # noqa: BLE001
        if _is_missing_stripe_customer(stripe, exc):
            return []   # customer apagado no Stripe = nada a expirar
        raise
    return _checar_dono(_sg(r, "data", []) or [], user_id, "checkout")


def _vivas(stripe, customer: str, user_id: int) -> list:
    try:
        # ponytail: 1 página (100); customer com >100 assinaturas é fora da realidade.
        subs = stripe.Subscription.list(customer=customer, status="all", limit=100)
    except Exception as exc:  # noqa: BLE001
        if _is_missing_stripe_customer(stripe, exc):
            return []   # customer apagado no Stripe = sem assinatura
        raise
    vivas = [s for s in (_sg(subs, "data", []) or []) if _sg(s, "status") not in _JA_MORTA]
    return _checar_dono(vivas, user_id, "assinatura")


def _expirar_checkouts(stripe, customer: str, user_id: int, lista: list) -> None:
    for sessao in lista:
        try:
            stripe.checkout.Session.expire(sessao["id"])
        except Exception as exc:  # noqa: BLE001 — quem decide é a nova listagem
            _log.warning("exclusao_stripe_expire_falhou cs=%s: %s", _sg(sessao, "id"), type(exc).__name__)
    if lista and _abertas(stripe, customer, user_id):
        raise RuntimeError("sobrou checkout aberto depois da expiração")


def cancelar_assinaturas(user_id: int) -> None:
    """Toda a fase Stripe roda sob o MESMO advisory lock do checkout
    (`_billing_user_lock`, `billing_checkout:<uid>`): checkout que ganha o lock antes
    é listado e expirado aqui; quem ganha depois vê a conta `processing` e leva 403."""
    chave = f"billing_checkout:{int(user_id)}"
    with get_conn() as conn:
        conn.execute("select pg_advisory_lock(hashtext(%s))", (chave,))
        try:
            _cancelar(conn, user_id)
        finally:
            conn.rollback()   # lock de sessão sobrevive ao rollback; o unlock não pode cair em transação abortada
            conn.execute("select pg_advisory_unlock(hashtext(%s))", (chave,))
            conn.commit()


def _cancelar(conn, user_id: int) -> None:
    customers = [r["stripe_customer_id"] for r in conn.execute(
        "select distinct stripe_customer_id from auth_accounts"
        " where user_id = %s and stripe_customer_id is not null", (user_id,)).fetchall()]
    conn.commit()   # sem transação aberta durante as chamadas à Stripe
    if not customers:
        return
    import stripe
    stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")
    if not stripe.api_key:
        raise RuntimeError("STRIPE_SECRET_KEY ausente")
    # 1) só leitura, de TODOS os customers: item de outro usuário em qualquer um
    #    levanta antes da primeira escrita (nada expirado nem cancelado)
    abertas = {c: _abertas(stripe, c, user_id) for c in customers}
    for customer in customers:
        _vivas(stripe, customer, user_id)
    # 2) escrita
    for customer in customers:
        _expirar_checkouts(stripe, customer, user_id, abertas[customer])   # antes de listar: nada novo nasce depois
        for sub in _vivas(stripe, customer, user_id):
            try:
                sch = _sg(sub, "schedule")
                if sch:   # assinatura presa a um schedule: solta antes de cancelar
                    stripe.SubscriptionSchedule.release(sch if isinstance(sch, str) else sch["id"])
                stripe.Subscription.cancel(
                    sub["id"], cancellation_details={"comment": MARCA_EXCLUSAO})
            except Exception as exc:  # noqa: BLE001 — quem decide é a nova listagem
                _log.warning("exclusao_stripe_cancel_falhou sub=%s: %s", _sg(sub, "id"), type(exc).__name__)
        if _vivas(stripe, customer, user_id):
            raise RuntimeError("sobrou assinatura viva depois do cancelamento")
