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


def assinatura_da_fatura(objeto) -> str | None:
    """Id da assinatura, seja `subscription` string ou objeto EXPANDIDO, da fatura ou da
    sessão de checkout. Os dois lados precisam da mesma chave: com o objeto expandido lido
    cru, o mesmo assinante ficava com DOIS grants da mesma assinatura. Na API >= 2025-09 ela
    mora em `parent.subscription_details`, que não casa em objeto de sessão."""
    ref = _ler(objeto, "subscription") or _ler(_ler(_ler(objeto, "parent"), "subscription_details"), "subscription")
    return ref if ref is None or isinstance(ref, str) else _ler(ref, "id")


def extras_liquido_cents(linhas, precos: set[str]) -> int:
    """Centavos LÍQUIDOS das `linhas` da fatura (`linhas_da_fatura`) dos produtos extras.
    Os extras são os preços da foto da metadata da assinatura (`da_metadata`), não os da env
    do momento. Medido: `amount` da linha é BRUTO; o cupom vem só em `discount_amounts`.
    `price` vem string ou expandido (`.id`)."""
    if not precos:
        return 0
    total = 0
    for line in linhas:
        price = _ler(_ler(_ler(line, "pricing"), "price_details"), "price")
        if (price if isinstance(price, str) else _ler(price, "id")) not in precos:
            continue
        desconto = sum(_ler(d, "amount") or 0 for d in _ler(line, "discount_amounts") or [])
        total += (_ler(line, "amount") or 0) - desconto
    return total


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


# ── Página própria (checkout `ui_mode="elements"`) ──────────────────────────

CAIXAS = 3   # extras na página própria (dono, 2026-10-03)


def pagina_propria_ligada() -> bool:
    """A flag `CHECKOUT_PAGINA_PROPRIA`, lida a cada sessão (molde do
    `pix_annual_available`). Desligada, o checkout é o de antes."""
    return (os.getenv("CHECKOUT_PAGINA_PROPRIA") or "").strip() in ("1", "true", "True")


def _na_tela(price) -> dict:
    """Texto e capa do Product do Stripe. Capa só https (vai para um `img.src`)."""
    produto = _ler(price, "product")
    capa = (_ler(produto, "images") or [None])[0]
    return {"nome": _ler(produto, "name"), "descricao": _ler(produto, "description"),
            "imagem": capa if str(capa).startswith("https://") else None,
            "valor_centavos": _ler(price, "unit_amount")}


def _recusa(user_id: int, mensagem: str, precos: list[str], exc=None) -> None:
    from core.system_event_log import log_system_event_sync
    details = {"precos": precos}
    if exc is not None:   # o `code` do Stripe, ou o tipo (conexão não tem); nunca a mensagem (#657)
        details["code"] = getattr(exc, "code", None) or type(exc).__name__
    # Só preços: a URL é o acesso ao PDF pago.
    log_system_event_sync("error", "ebook_oferta_recusada", mensagem,
                          source="billing", user_id=int(user_id), details=details)


def ofertas_da_pagina(stripe_mod, user_id: int) -> list[tuple[str, str, dict]]:
    """[(preço, url, tela)] dos primeiros `CAIXAS` de `da_env()` que o Stripe
    vende AGORA (ativo, BRL, avulso, valor fixo > 0, produto ativo). Filtra ANTES de recortar:
    o 1º inativo cede a vaga ao 4º. Falha do Stripe = nenhuma caixa (o plano
    vende sem elas); as duas saídas logam `ebook_oferta_recusada`."""
    ofertas, recusados = [], []
    try:
        for preco, url in da_env():
            if len(ofertas) == CAIXAS:
                break
            p = stripe_mod.Price.retrieve(preco, expand=["product"])
            valor = _ler(p, "unit_amount")   # None = valor livre (custom_unit_amount)
            if (_ler(p, "active") and _ler(p, "currency") == "brl"
                    and _ler(p, "type") == "one_time" and _ler(_ler(p, "product"), "active")
                    and isinstance(valor, int) and valor > 0):
                ofertas.append((preco, url, _na_tela(p)))
            else:
                recusados.append(preco)
    except stripe_mod.error.StripeError as exc:
        _recusa(user_id, "Stripe falhou ao ler os produtos extras; página sem eles.",
                [p for p, _ in da_env()], exc)
        return []
    if recusados:
        _recusa(user_id, "Produto extra inativo ou fora de BRL avulso; ficou fora da página.",
                recusados)
    return ofertas


def para_tela(ofertas, no_carrinho=frozenset()) -> list[dict]:
    """As caixas da resposta. `posicao` = posição na foto (a do /bump)."""
    return [{"posicao": n, **tela, "no_carrinho": preco in no_carrinho}
            for n, (preco, _, tela) in enumerate(ofertas, 1)]


def tela_da_sessao(stripe_mod, session) -> list[dict]:
    """Caixas de uma sessão REAPROVEITADA: a foto dela (sem refiltrar: é o que
    foi oferecido), marcadas as que já estão no carrinho. Falha sobe."""
    foto = da_metadata(_ler(session, "metadata"))
    if not foto:
        return []
    linhas = stripe_mod.checkout.Session.list_line_items(_ler(session, "id"), limit=100)
    no_carrinho = {_ler(_ler(linha, "price"), "id") for linha in (_ler(linhas, "data") or [])}
    return para_tela([(p, u, _na_tela(stripe_mod.Price.retrieve(p, expand=["product"])))
                      for p, u in foto], no_carrinho)


def linhas_do_bump(atuais, precos_da_foto, desejados) -> list[dict] | None:
    """`line_items` do `Session.modify` que deixa no carrinho só os extras
    `desejados` (preços da FOTO). `atuais` = o `data` do `list_line_items`. O
    plano (não-extra) e o extra desejado já presente ficam pelo `id`; o desejado
    ausente entra por preço; o extra não desejado sai. None = nada muda."""
    desejados = list(dict.fromkeys(desejados))   # repetido entraria duas vezes
    foto, quero = set(precos_da_foto), set(desejados)
    ficam, presentes = [], set()
    for linha in atuais:
        preco = _ler(_ler(linha, "price"), "id")
        if preco not in foto or preco in quero:
            ficam.append({"id": _ler(linha, "id")})
            presentes.add(preco)
    entram = [{"price": p, "quantity": 1} for p in desejados if p not in presentes]
    if not entram and len(ficam) == len(atuais):
        return None
    return ficam + entram
