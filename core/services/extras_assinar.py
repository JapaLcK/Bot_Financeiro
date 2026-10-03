"""core/services/extras_assinar.py — os produtos extras do checkout (funil v3).

Slot 1 usa as chaves de hoje (`ebook_price`/`ebook_url`); o slot n (2..SLOTS)
usa `ebook_{n}_price`/`ebook_{n}_url`. A metadata da sessão é a FOTO do que foi
oferecido: quem entrega lê dela, nunca da env.
"""

from __future__ import annotations

SLOTS = 10   # teto do `optional_items` do Stripe


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


def da_metadata(meta) -> list[tuple[str, str | None]]:
    """[(preço, url | None)] dos slots com preço, na ordem do slot."""
    itens = []
    for n in range(1, SLOTS + 1):
        preco = _ler(meta, _chave(n, "price"))
        if preco:
            itens.append((preco, _ler(meta, _chave(n, "url"))))
    return itens
