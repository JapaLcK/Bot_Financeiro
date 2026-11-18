"""Auto-indicação (#1506): a fatura do indicado foi paga com um cartão do afiliado?

Compara `fingerprint` (estável por cartão na conta Stripe) das cobranças da fatura com o dos
cartões salvos no customer do afiliado. Chamada HTTP: roda FORA da transação da comissão, e
qualquer falha da Stripe propaga (fail-closed: o webhook cai no `affiliate_commission_failed`
sem gravar o marcador). Afiliado sem customer: False, sem HTTP.
"""
from core.services.ebook_entrega import cliente_stripe, cobrancas_da_fatura
from core.services.extras_assinar import _ler
from db.connection import get_conn


def _customers_do_afiliado(referred_user_id: int) -> list[str]:
    """Todos os customers do afiliado (`auth_accounts` não é unique por `user_id`)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select distinct c.stripe_customer_id from affiliate_referrals r"
            " join affiliates a on a.id = r.affiliate_id"
            " join auth_accounts c on c.user_id = a.user_id"
            " where r.referred_user_id = %s and c.stripe_customer_id is not null",
            (int(referred_user_id),))
        return [r["stripe_customer_id"] for r in cur.fetchall()]


def _fingerprints(stripe, customer: str):
    """Pagina `PaymentMethod.list` à mão (`has_more` + `starting_after`): sem isto o 101º cartão passa batido."""
    params = {"customer": customer, "type": "card", "limit": 100}
    while True:
        pagina = stripe.v1.payment_methods.list(params=params)
        dados = _ler(pagina, "data") or []
        yield from (_ler(_ler(c, "card"), "fingerprint") for c in dados)
        if not dados or not _ler(pagina, "has_more"):
            return
        params = {**params, "starting_after": dados[-1]["id"]}


def cartao_do_afiliado(referred_user_id: int, fatura: str, chave_stripe: str) -> bool:
    customers = _customers_do_afiliado(referred_user_id)
    if not customers:
        return False
    stripe = cliente_stripe(chave_stripe)
    dele = {f for c in customers for f in _fingerprints(stripe, c) if f}
    if not dele:
        return False
    for ch in cobrancas_da_fatura(fatura, chave_stripe):
        if _ler(_ler(_ler(ch, "payment_method_details"), "card"), "fingerprint") in dele:
            return True
    return False
