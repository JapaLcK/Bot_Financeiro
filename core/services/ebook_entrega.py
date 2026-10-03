"""core/services/ebook_entrega.py — o job que entrega o e-book da /assinar.

O webhook do checkout grava a pendência (`db/ebook_entregas.py`); este job,
chamado pelo `_ebook_worker` do lifespan a cada 5 min, só envia quando a conta
já provou o e-mail (senha, Google ou Apple), confirma a compra pelas linhas da
sessão no Stripe e manda o link para o e-mail ATUAL da conta.

Entrega "pelo menos uma vez": se o processo cair entre o envio e o `fechar`, o
claim expira e o e-mail sai de novo. Nunca logar a `ebook_url` (é o acesso ao
PDF pago).
"""

from __future__ import annotations

import os

from core.observability import log_system_event_sync
from core.services.email_service import send_ebook_email
from db import get_auth_user
from db.ebook_entregas import abertas, fechar, reivindicar
from db.google_auth import conta_sem_credencial


def _entregar(uid: int, sid: str, key: str) -> bool:
    import stripe  # noqa: PLC0415

    if conta_sem_credencial(uid):
        return False                      # espera a prova do e-mail
    linha = reivindicar(uid, sid)
    if linha is None:
        return False                      # outra rodada pegou
    itens = stripe.checkout.Session.list_line_items(sid, api_key=key)
    if not any(i["price"]["id"] == linha["ebook_price"] for i in itens["data"]):
        fechar(uid, sid, "nao_comprou")
        return False
    email = ((get_auth_user(uid) or {}).get("email") or "").strip()
    if email and send_ebook_email(email, linha["ebook_url"]):
        return fechar(uid, sid, "enviado")
    return False                          # claim expira; próximo ciclo tenta


def entregar_pendentes() -> int:
    """Uma passada. Devolve quantos e-books saíram. Inerte sem
    `STRIPE_SECRET_KEY` (também impede a suíte de consumir pendências)."""
    key = os.getenv("STRIPE_SECRET_KEY")
    if not key:
        return 0
    n = 0
    for uid, sid in abertas():
        try:
            n += _entregar(uid, sid, key)
        except Exception as exc:          # uma linha não derruba a passada
            log_system_event_sync(
                "error", "ebook_entrega_falhou",
                "Entrega do e-book falhou; o claim expira e o próximo ciclo tenta.",
                source="billing", user_id=uid,
                details={"session_id": sid, "erro": f"{type(exc).__name__}: {exc}"[:300]},
            )
    return n
