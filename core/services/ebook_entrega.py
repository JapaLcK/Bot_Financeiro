"""core/services/ebook_entrega.py — o job que entrega os produtos da /assinar.

O webhook do checkout grava a pendência (`db/ebook_entregas.py`); este job,
chamado pelo `_ebook_worker` do lifespan a cada 5 min, só envia quando a conta
já provou o e-mail (senha, Google ou Apple), confirma a compra pelas linhas da
sessão no Stripe e manda o link para o e-mail ATUAL da conta. Cada produto é
uma linha com claim, backoff e tentativas próprios: a falha de um não segura os
outros.

Entrega "pelo menos uma vez": se o processo cair entre o envio e o `fechar`, o
claim expira e o e-mail sai de novo. Nunca logar a `ebook_url` (é o acesso ao
PDF pago).
"""

from __future__ import annotations

import os

from core.observability import log_system_event_sync
from core.services.email_service import send_ebook_email
from core.services.extras_assinar import _ler
from db import get_auth_user
from db.ebook_entregas import abertas, fechar, reivindicar
from db.google_auth import conta_sem_credencial


def _entregar(uid: int, sid: str, preco: str, key: str) -> bool:
    import stripe  # noqa: PLC0415

    if conta_sem_credencial(uid):
        return False                      # espera a prova do e-mail
    linha = reivindicar(uid, sid, preco)
    if linha is None:
        return False                      # outra rodada pegou
    # `limit=100`: o padrão do Stripe é 10, e plano + 10 extras são 11 linhas —
    # a que ficasse fora da página fecharia `nao_comprou` para sempre.
    itens = stripe.checkout.Session.list_line_items(sid, api_key=key, limit=100)
    item = next((i for i in itens["data"] if i["price"]["id"] == preco), None)
    if item is None:
        fechar(uid, sid, preco, "nao_comprou")
        return False
    email = ((get_auth_user(uid) or {}).get("email") or "").strip()
    if email and send_ebook_email(email, linha["ebook_url"], nome=_ler(item, "description")):
        return fechar(uid, sid, preco, "enviado")
    return False                          # claim expira; próximo ciclo tenta


def entregar_pendentes() -> int:
    """Uma passada. Devolve quantos e-books saíram. Inerte sem
    `STRIPE_SECRET_KEY` (também impede a suíte de consumir pendências)."""
    key = os.getenv("STRIPE_SECRET_KEY")
    if not key:
        return 0
    n = 0
    for uid, sid, preco in abertas():
        try:
            n += _entregar(uid, sid, preco, key)
        except Exception as exc:          # uma linha não derruba a passada
            log_system_event_sync(
                "error", "ebook_entrega_falhou",
                "Entrega do e-book falhou; o claim expira e o próximo ciclo tenta.",
                source="billing", user_id=uid,
                details={"session_id": sid, "ebook_price": preco, "erro": f"{type(exc).__name__}: {exc}"[:300]},
            )
    return n
