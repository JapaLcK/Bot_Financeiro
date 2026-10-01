"""core/services/stripe_email_sync.py — leva o e-mail trocado no app ao cliente do Stripe.

A PATCH /settings/{uid}/security/contact grava a pendência na mesma transação da
troca (`db/stripe_email_pendente.py`); este job, chamado pelo
`_stripe_email_worker` do lifespan a cada 5 min, manda o e-mail ATUAL da conta
com `stripe.Customer.modify` até o Stripe aceitar. A troca no app nunca é
desfeita. Nunca logar o e-mail.
"""

from __future__ import annotations

import os

from core.crypto import PiiAccessContext, decrypt_pii_optional
from core.observability import log_system_event_sync
from db.stripe_email_pendente import abertas, fechar, reivindicar


def _sincronizar(uid: int, key: str) -> bool:
    import stripe  # noqa: PLC0415

    linha = reivindicar(uid)
    if linha is None:
        return False                      # outra rodada pegou
    email = linha["email"]
    if linha["email_enc"]:
        email = decrypt_pii_optional(linha["email_enc"], ctx=PiiAccessContext(
            purpose="stripe_email_sync", actor="system:stripe_email_sync",
            subject_user_id=uid, field="email"))
    if not linha["stripe_customer_id"] or not email:
        return fechar(uid, linha["versao"])
    try:
        stripe.Customer.modify(linha["stripe_customer_id"], email=email, api_key=key)
    except stripe.InvalidRequestError as exc:   # cliente apagado ou e-mail recusado: não adianta repetir
        log_system_event_sync(
            "error", "stripe_email_sync_recusado",
            "O Stripe recusou o e-mail novo do cliente; a pendência foi fechada.",
            source="billing", user_id=uid,
            details={"code": exc.code, "param": getattr(exc, "param", None)},
        )
        fechar(uid, linha["versao"])
        return False
    return fechar(uid, linha["versao"])


def sincronizar_pendentes() -> int:
    """Uma passada. Devolve quantos clientes ficaram em dia. Inerte sem
    `STRIPE_SECRET_KEY` (também impede a suíte de consumir pendências)."""
    key = os.getenv("STRIPE_SECRET_KEY")
    if not key:
        return 0
    n = 0
    for uid in abertas():
        try:
            n += _sincronizar(uid, key)
        except Exception as exc:          # transitório: o claim expira e o backoff espaça
            log_system_event_sync(
                "error", "stripe_email_sync_falhou",
                "Atualização do e-mail no Stripe falhou; o claim expira e o próximo ciclo tenta.",
                source="billing", user_id=uid,
                details={"erro": type(exc).__name__},
            )
    return n
