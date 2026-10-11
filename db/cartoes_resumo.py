"""Resumo dos cartões (fatura aberta, limite usado) em 1 query agregada.

Extraído de frontend/routes/cards.py::cards_summary_route: a rota e o show_card do chat da IA
chamam esta mesma função.
"""

from db.cards import fatura_a_pagar
from db.connection import get_conn
from utils_date import today_tz


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

    # A fatura do card é a que vence primeiro (`fatura_a_pagar`, a mesma do `get_open_bill`,
    # do "pagar com saldo" e do `pay_bill`), não a última aberta por `period_end`. Fora do `with`
    # acima: cada `fatura_a_pagar` abre a própria conexão, e aninhar seguraria 2 do pool.
    # ponytail: 1 consulta por cartão; um agregado só se alguém tiver dezenas de cartões.
    hoje = today_tz()
    ids = {int(r["id"]): fatura_a_pagar(user_id, int(r["id"]), hoje) for r in rows}
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id, total, coalesce(paid_amount, 0) as paid_amount, period_end"
                " from credit_bills where user_id = %s and id = any(%s)",
                (user_id, [i for i in ids.values() if i]),
            )
            faturas = {int(b["id"]): b for b in cur.fetchall()}

    out = []
    for r in rows:
        credit_limit = float(r["credit_limit"]) if r.get("credit_limit") is not None else None
        usage = float(r["credit_used"] or 0)
        available = (credit_limit - usage) if credit_limit is not None else None
        f = faturas.get(ids[int(r["id"])])
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
                "id": int(f["id"]) if f else None,
                "total": float(f["total"] or 0) if f else 0.0,
                "paid_amount": float(f["paid_amount"] or 0) if f else 0.0,
                "due_amount": float((f["total"] or 0) - f["paid_amount"]) if f else 0.0,
                "period_end": f["period_end"].isoformat() if f else None,
            },
            "credit_used": usage,
            "credit_available": available,
        })
    return out
