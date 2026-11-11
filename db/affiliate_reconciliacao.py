"""
db/affiliate_reconciliacao.py — a fila da reconciliação de comissão já paga (#1501): quais faturas pagas
reconferir na Stripe e quando cada uma foi conferida. A regra do reembolso mora em `registrar_reembolso`;
aqui só custo e cadência. Sem pessoa: fatura e data.
"""
from .connection import get_conn


def faturas_pagas_a_conferir(limite: int, janela_dias: int, cadencia_s: int) -> list[str]:
    """As faturas de comissão paga dentro da janela, a nunca conferida primeiro e depois a conferida há
    mais tempo, passada a cadência. Total já registrado nunca reabre, então não consulta; parcial
    registrada segue (um total depois é fato novo). Pix fica fora: não é fatura da Stripe."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            with pagas as (
              select c.stripe_invoice_id as fatura, c.created_at as desde
                from affiliate_commissions c
               where c.status = 'paid' and left(c.stripe_invoice_id, 4) <> 'pix:'
              union all
              -- comissão paga de afiliado que excluiu a conta: o valor ficou na linha da fatura (N1)
              select r.stripe_invoice_id, r.created_at
                from affiliate_refund_reviews r
               where r.commission_id is null and r.paid_cents is not null and left(r.stripe_invoice_id, 4) <> 'pix:'
            )
            select p.fatura from pagas p
              left join affiliate_paid_refund_checks k on k.stripe_invoice_id = p.fatura
             where p.desde > now() - make_interval(days => %(janela)s)
               and not exists (select 1 from affiliate_refund_reviews v
                                where v.stripe_invoice_id = p.fatura and v.tipo = 'total')
               and (k.checked_at is null or k.checked_at < now() - make_interval(secs => %(cadencia)s))
             order by k.checked_at nulls first, p.fatura
             limit %(limite)s
            """,
            {"janela": int(janela_dias), "cadencia": int(cadencia_s), "limite": int(limite)},
        )
        return [linha["fatura"] for linha in cur.fetchall()]


def marcar_conferida(fatura: str) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("insert into affiliate_paid_refund_checks(stripe_invoice_id, checked_at) values (%s, now())"
                    " on conflict (stripe_invoice_id) do update set checked_at = excluded.checked_at", (fatura,))
        conn.commit()


def limpar_conferidas(janela_dias: int) -> None:
    """Quem saiu da janela não é mais consultado nem escrito: a linha só ocuparia espaço."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("delete from affiliate_paid_refund_checks where checked_at < now() - make_interval(days => %s)",
                    (int(janela_dias) + 1,))
        conn.commit()
