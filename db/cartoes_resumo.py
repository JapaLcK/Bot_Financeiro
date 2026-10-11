"""Resumo dos cartões (fatura aberta, limite usado) em 1 query agregada.

Extraído de frontend/routes/cards.py::cards_summary_route sem mudar a conta:
a rota chama esta função, e o show_card do chat da IA (PR B1) vai chamar a mesma.
"""

from db.connection import get_conn


def resumo_cartoes(user_id: int) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select
                  c.id, c.name, c.color, c.flag, c.last4,
                  c.closing_day, c.due_day, c.credit_limit,
                  (u.default_card_id = c.id) as is_default,
                  coalesce((
                    select b.total - coalesce(b.paid_amount, 0)
                      from credit_bills b
                     where b.card_id = c.id and b.user_id = c.user_id and b.status = 'open'
                     order by b.period_end desc
                     limit 1
                  ), 0) as open_due,
                  coalesce((
                    select b.total
                      from credit_bills b
                     where b.card_id = c.id and b.user_id = c.user_id and b.status = 'open'
                     order by b.period_end desc
                     limit 1
                  ), 0) as open_total,
                  coalesce((
                    select b.paid_amount
                      from credit_bills b
                     where b.card_id = c.id and b.user_id = c.user_id and b.status = 'open'
                     order by b.period_end desc
                     limit 1
                  ), 0) as open_paid,
                  (
                    select b.id
                      from credit_bills b
                     where b.card_id = c.id and b.user_id = c.user_id and b.status = 'open'
                     order by b.period_end desc
                     limit 1
                  ) as open_bill_id,
                  (
                    select b.period_end
                      from credit_bills b
                     where b.card_id = c.id and b.user_id = c.user_id and b.status = 'open'
                     order by b.period_end desc
                     limit 1
                  ) as open_period_end,
                  coalesce((
                    select sum(b.total - coalesce(b.paid_amount, 0))
                      from credit_bills b
                     where b.card_id = c.id and b.user_id = c.user_id
                  ), 0) as credit_used
                from credit_cards c
                left join users u on u.id = c.user_id
                where c.user_id = %s
                order by c.name
                """,
                (user_id,),
            )
            rows = cur.fetchall()

    out = []
    for r in rows:
        credit_limit = float(r["credit_limit"]) if r.get("credit_limit") is not None else None
        usage = float(r["credit_used"] or 0)
        available = (credit_limit - usage) if credit_limit is not None else None
        out.append({
            "id": int(r["id"]),
            "name": r["name"],
            "color": r.get("color"),
            "flag": r.get("flag"),
            "last4": r.get("last4"),
            "closing_day": r["closing_day"],
            "due_day": r["due_day"],
            "credit_limit": credit_limit,
            "is_default": bool(r.get("is_default")),
            "open_bill": {
                "id": int(r["open_bill_id"]) if r.get("open_bill_id") else None,
                "total": float(r["open_total"] or 0),
                "paid_amount": float(r["open_paid"] or 0),
                "due_amount": float(r["open_due"] or 0),
                "period_end": r["open_period_end"].isoformat() if r.get("open_period_end") else None,
            },
            "credit_used": usage,
            "credit_available": available,
        })
    return out
