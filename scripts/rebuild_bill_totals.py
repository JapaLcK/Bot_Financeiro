"""
Reconciliação forte de bills de UM usuário.

Recalcula `total` de cada bill somando `credit_transactions` reais, clampa
`paid_amount > total` (overpayment fantasma) e reabre bills paid/closed que
voltam a ter saldo devedor.

Uso básico (clampa silenciosamente):
  cd "Bot Financeiro"
  DATABASE_URL="postgresql://..." python scripts/rebuild_bill_totals.py <user_id>

Uso com estorno (devolve dinheiro do overpayment pra conta corrente):
  DATABASE_URL="..." python scripts/rebuild_bill_totals.py <user_id> --refund

Pegar o DATABASE_URL no Railway: service Postgres → Variables → DATABASE_URL.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    if len(sys.argv) < 2:
        print("uso: python scripts/rebuild_bill_totals.py <user_id> [--refund]")
        sys.exit(1)

    try:
        user_id = int(sys.argv[1])
    except ValueError:
        print(f"user_id inválido: {sys.argv[1]!r}")
        sys.exit(1)

    refund = "--refund" in sys.argv[2:]

    if not os.getenv("DATABASE_URL"):
        print("ERRO: DATABASE_URL não setado.")
        sys.exit(1)

    from db import rebuild_bill_totals

    flag_label = " com --refund" if refund else ""
    print(f"Reconciliando bills do user {user_id}{flag_label}...")
    out = rebuild_bill_totals(user_id, refund_overpayments=refund)
    print(f"  totais corrigidos: {out['totals_updated']}")
    print(f"  paid clampado:     {out['paid_clamped']}")
    print(f"  bills reabertas:   {out['reopened']}")
    print(f"  estornado (R$):    {out['refunded']:.2f}")
    print("Pronto.")


if __name__ == "__main__":
    main()
