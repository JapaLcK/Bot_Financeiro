"""core/services/extras_assinar.py — os produtos extras do checkout (funil v3).

A env tem SLOTS (`da_env`); a foto tem POSIÇÕES na lista oferecida: a posição 1
usa as chaves de hoje (`ebook_price`/`ebook_url`) e a posição n (2..SLOTS) usa
`ebook_{n}_price`/`ebook_{n}_url`. A metadata da sessão é a FOTO do que foi
oferecido: quem entrega lê dela, nunca da env.
"""

from __future__ import annotations

import logging
import os

SLOTS = 10   # teto do `optional_items` do Stripe
_MAX_META = 500   # o Stripe recusa valor de metadata acima disso (medido)


def _chave(n: int, campo: str) -> str:
    return f"ebook_{campo}" if n == 1 else f"ebook_{n}_{campo}"


def _ler(meta, chave: str):
    # StripeObject (SDK v8+) não tem `.get`: mesmo acesso do `_g` do monólito.
    try:
        return meta[chave] or None
    except (KeyError, TypeError, AttributeError):
        return None


def linhas_da_fatura(invoice) -> list:
    """Todas as linhas da fatura. O objeto embute no máximo 10 (plano + 10
    extras = 11); com `has_more`, busca pela API. Sem try: no webhook, falha
    vira 5xx e o Stripe reentrega. Usa o `stripe.api_key` global que o webhook
    seta, por isso só serve dentro dele."""
    linhas = _ler(invoice, "lines")
    if _ler(linhas, "has_more"):
        import stripe
        linhas = stripe.Invoice.list_lines(_ler(invoice, "id"), limit=100)
    return _ler(linhas, "data") or []


def da_env() -> list[tuple[str, str]]:
    """[(preço, url)] oferecíveis agora, na ordem do slot. Slot 1 =
    `STRIPE_PRICE_ID_EBOOK`/`EBOOK_URL`; slot n = `..._EBOOK_n`/`EBOOK_URL_n`.
    Cada slot vale sozinho: preço sem URL venderia algo que não se entrega."""
    log = logging.getLogger(__name__)
    itens: list[tuple[str, str]] = []
    for n in range(1, SLOTS + 1):
        sufixo = "" if n == 1 else f"_{n}"
        # strip: espaço ou quebra de linha colados no Railway iriam crus ao Stripe.
        preco = os.getenv(f"STRIPE_PRICE_ID_EBOOK{sufixo}", "").strip()
        url = os.getenv(f"EBOOK_URL{sufixo}", "").strip()
        if not preco:
            continue
        if not url or len(url) > _MAX_META:
            # Nunca logar a URL: é o acesso ao PDF pago.
            log.warning("ebook_nao_oferecido: slot %d, EBOOK_URL vazia ou com %d caracteres (max 500)",
                        n, len(url))
        elif any(p == preco for p, _ in itens):
            log.warning("ebook_nao_oferecido: slot %d repete o preço de um slot anterior", n)
        else:
            itens.append((preco, url))
    return itens


def para_metadata(itens) -> dict[str, str]:
    """Numera pela POSIÇÃO na lista oferecida, não pelo slot da env: o 1º item
    sempre grava `ebook_price`/`ebook_url` (com um produto, o metadata de hoje)."""
    meta = {}
    for n, (preco, url) in enumerate(itens, 1):
        meta[_chave(n, "price")] = preco
        meta[_chave(n, "url")] = url
    return meta


def da_metadata(meta) -> list[tuple[str, str | None]]:
    """[(preço, url | None)] das posições com preço, na ordem da posição."""
    itens = []
    for n in range(1, SLOTS + 1):
        preco = _ler(meta, _chave(n, "price"))
        if preco:
            itens.append((preco, _ler(meta, _chave(n, "url"))))
    return itens
