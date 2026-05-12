"""
Diagnóstico: dump das bills e transações de um usuário.

Uso:
  DATABASE_URL="..." python scripts/diag_bills.py <user_id>

Imprime, pra cada bill do user:
  - id, period_start/end, total armazenado, paid_amount, status
  - soma das credit_transactions (pra cross-check com total)
  - contagem de transações ligadas
  - últimas N transações (id, valor, bill_id, group_id, installment_no/total, purchased_at)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    if len(sys.argv) < 2:
        print("uso: python scripts/diag_bills.py <user_id>")
        sys.exit(1)
    user_id = int(sys.argv[1])

    if not os.getenv("DATABASE_URL"):
        print("ERRO: DATABASE_URL não setado.")
        sys.exit(1)

    import db
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select b.id, b.card_id, c.name as card_name,
                       b.period_start, b.period_end, b.total,
                       coalesce(b.paid_amount, 0) as paid_amount,
                       b.status,
                       (select coalesce(sum(t.valor), 0) from credit_transactions t where t.bill_id = b.id) as sum_tx,
                       (select count(*) from credit_transactions t where t.bill_id = b.id) as n_tx
                from credit_bills b
                join credit_cards c on c.id = b.card_id
                where b.user_id = %s
                order by b.period_end asc, b.id asc
                """,
                (user_id,),
            )
            bills = cur.fetchall()

            print(f"=== {len(bills)} BILLS do user {user_id} ===")
            for b in bills:
                marker = ""
                if float(b["total"]) != float(b["sum_tx"]):
                    marker += " ⚠️ total ≠ sum_tx"
                if b["status"] in ("paid", "closed") and float(b["total"]) > float(b["paid_amount"]):
                    marker += " ⚠️ paid/closed com saldo"
                print(
                    f"  #{b['id']:>4} {b['card_name']:<10} "
                    f"{b['period_start']} → {b['period_end']} "
                    f"total={float(b['total']):>9.2f} "
                    f"paid={float(b['paid_amount']):>9.2f} "
                    f"status={b['status']:<6} "
                    f"n_tx={b['n_tx']} sum_tx={float(b['sum_tx']):>9.2f}{marker}"
                )

            print(f"\n=== ÚLTIMAS 20 transações ===")
            cur.execute(
                """
                select t.id, t.bill_id, t.valor, t.categoria, t.nota,
                       t.purchased_at, t.group_id, t.installment_no,
                       t.installments_total, t.is_refund
                from credit_transactions t
                where t.user_id = %s
                order by t.id desc
                limit 20
                """,
                (user_id,),
            )
            for t in cur.fetchall():
                inst = ""
                if t.get("installments_total") and int(t["installments_total"] or 0) > 1:
                    inst = f" [{t['installment_no']}/{t['installments_total']} grp={str(t['group_id'])[:8]}]"
                print(
                    f"  tx#{t['id']} bill#{t['bill_id']} "
                    f"R$ {float(t['valor']):>8.2f} "
                    f"{t['categoria'] or '-':<15} "
                    f"{t.get('nota') or '-':<20} "
                    f"{t['purchased_at']}{inst}"
                )


if __name__ == "__main__":
    main()
