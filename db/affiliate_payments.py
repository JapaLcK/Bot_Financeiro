"""
db/affiliate_payments.py — pagamentos confirmados de quem veio por um afiliado.

"Clientes que pagaram" é fato do cliente, não da comissão: uma linha por pagamento,
pendurada na referral, sem `user_id`. Nada aqui lê `affiliate_commissions` nem
`commission_bps`; afiliado 0%, desativado ou Pix contam do mesmo jeito.

Quem grava: o webhook da Stripe e o dreno do Pix. Estorno de comissão não toca esta
tabela, e a linha nunca é apagada por reembolso: `refund_status` só a rotula.
"""
from datetime import datetime

from .connection import get_conn


def origem_da_chave(chave: str) -> tuple[str, str]:
    """A chave da comissão (`stripe_invoice_id`) como pagamento: `"pix:7"` é a cobrança Pix 7,
    o resto é fatura Stripe. Par SQL: `PAGAMENTO_DA_COMISSAO_SQL` (um teste compara os dois)."""
    return ("pix", chave[4:]) if chave.startswith("pix:") else ("stripe", chave)


# O mesmo, em SQL: `{p}` (pagamento) casa com a chave da comissão `{c}`. Sem `%`, para servir em
# consulta com parâmetro.
PAGAMENTO_DA_COMISSAO_SQL = """({p}.source, {p}.payment_ref) = (
    case when left({c}.stripe_invoice_id, 4) = 'pix:' then 'pix' else 'stripe' end,
    case when left({c}.stripe_invoice_id, 4) = 'pix:' then substr({c}.stripe_invoice_id, 5)
         else {c}.stripe_invoice_id end)"""


def registrar_pagamento(referred_user_id: int, source: str, payment_ref: str,
                        amount_cents: int, paid_at: datetime) -> bool:
    """Grava o pagamento do indicado. True se criou a linha.

    No-op sem referral, com valor zerado ou com o mesmo `(source, payment_ref)`:
    a Stripe manda `invoice.paid` e `invoice.payment_succeeded` da mesma fatura.
    A referral vale com qualquer status do afiliado.
    """
    if int(amount_cents) <= 0:
        return False
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into affiliate_referral_payments(
                    referral_id, source, payment_ref, amount_cents, paid_at)
                select id, %s, %s, %s, %s
                  from affiliate_referrals
                 where referred_user_id = %s
                on conflict (source, payment_ref) do nothing
                """,
                (source, payment_ref, int(amount_cents), paid_at, int(referred_user_id)),
            )
            conn.commit()
            return cur.rowcount > 0


def _rotular(cur, source: str, payment_ref: str, tipo: str, quando: datetime | None) -> bool:
    """O `update` do rótulo, para quem já tem transação aberta (`registrar_reembolso`)."""
    if tipo not in ("partial", "total"):
        raise ValueError("tipo deve ser 'partial' ou 'total'.")
    cur.execute(
        """
        update affiliate_referral_payments
           set refund_status = %s, refunded_at = %s
         where source = %s and payment_ref = %s
           and refund_status <> 'total' and refund_status <> %s
        """,
        (tipo, quando, source, payment_ref, tipo),
    )
    return cur.rowcount > 0


def marcar_reembolso(source: str, payment_ref: str, tipo: str, quando: datetime | None) -> bool:
    """Rotula o pagamento como reembolsado (`partial` ou `total`). True se mudou.

    Nunca rebaixa `total` para `partial`. Pagamento fora do registro é no-op, e
    quem chama decide o que fazer com o False.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            mudou = _rotular(cur, source, payment_ref, tipo, quando)
            conn.commit()
            return mudou


def resumo_conversao(affiliate_id: int) -> dict:
    """Só contagens. Cliente é a referral: dois pagamentos contam uma vez, e ele só é
    "reembolsado" quando TODOS os pagamentos dele voltaram por inteiro."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                with por_cliente as (
                  select p.referral_id,
                         count(*) filter (where p.refund_status = 'total') as totais,
                         count(*) filter (where p.refund_status = 'partial') as parciais,
                         bool_and(p.refund_status = 'total') as reembolsado
                    from affiliate_referral_payments p
                    join affiliate_referrals r on r.id = p.referral_id
                   where r.affiliate_id = %s
                   group by p.referral_id
                )
                select count(*) as paying_clients,
                       count(*) filter (where reembolsado) as refunded_clients,
                       coalesce(sum(totais), 0) as refunds_total,
                       coalesce(sum(parciais), 0) as refunds_partial
                  from por_cliente
                """,
                (int(affiliate_id),),
            )
            return {k: int(v) for k, v in cur.fetchone().items()}
