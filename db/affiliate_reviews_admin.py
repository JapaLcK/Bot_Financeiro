"""
db/affiliate_reviews_admin.py — o que o admin lê das pendências de reembolso: as abertas, as
recuperações e o histórico. Só leitura; as regras moram em `db/affiliate_reviews.py`.

`left join` em tudo: a pendência e o histórico ficam sem comissão e sem afiliado quando ele exclui a
conta (A3), e o saque aparece pelo vínculo do saque órfão ou pelo evento (D8).
"""
from .connection import get_conn

# O saque da pendência: o da comissão, ou o do evento mais recente quando a comissão saiu com o
# afiliado (D8). `left join` em tudo: a pendência e o histórico ficam sem comissão e sem afiliado (A3).
_SAQUE_DA_PENDENCIA = """coalesce(c.payout_id, r.orphan_payout_id, (select e.payout_id from affiliate_refund_review_events e
                                                  where e.review_id = r.id and e.payout_id is not null
                                                  order by e.id desc limit 1))"""


def listar_admin() -> list[dict]:
    """Pendências abertas e recuperações, para o admin. Sem fatura e sem dado do indicado."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            select r.id, r.tipo, r.status, r.refunded_cents, r.amount_cents, r.recovery_cents, r.refunded_at,
                   r.notified_at, c.id as commission_id, coalesce(c.amount_cents, r.commission_cents) as commission_cents,
                   c.status as commission_status, p.id as payout_id, p.status as payout_status,
                   p.amount_cents as payout_cents, p.paid_at as payout_paid_at,
                   coalesce(a.code, r.affiliate_code) as code, a.id is null as affiliate_deleted,
                   (select e.action from affiliate_refund_review_events e
                     where e.review_id = r.id
                       and e.action in ('paid_before_refund', 'payment_recorded', 'partial_approved')
                     order by e.id desc limit 1) as origem,
                   (select e.receipt_ref from affiliate_refund_review_events e
                     where e.review_id = r.id and e.receipt_ref is not null
                     order by e.id desc limit 1) as referencia
              from affiliate_refund_reviews r
              left join affiliate_commissions c on c.id = r.commission_id
              left join affiliates a on a.id = c.affiliate_id
              left join affiliate_payouts p on p.id = {_SAQUE_DA_PENDENCIA}
             where r.status in ('open', 'recovery')
             order by r.id
            """
        )
        return cur.fetchall()


def listar_historico(limite: int = 50) -> list[dict]:
    """Os eventos mais recentes. Só leitura: é o que sobra de uma pendência fechada."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select e.id, e.review_id, e.action, e.actor, e.before_cents, e.after_cents, e.payout_id, e.event_on,
                   e.receipt_ref, e.justification, e.created_at, r.tipo, c.id as commission_id,
                   coalesce(a.code, r.affiliate_code, p.affiliate_code) as code
              from affiliate_refund_review_events e
              left join affiliate_refund_reviews r on r.id = e.review_id
              left join affiliate_commissions c on c.id = r.commission_id
              left join affiliates a on a.id = c.affiliate_id
              left join affiliate_payouts p on p.id = e.payout_id
             order by e.id desc limit %s
            """,
            (int(limite),),
        )
        return cur.fetchall()
