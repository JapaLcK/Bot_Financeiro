"""
Reconciliação forte de bills de UM usuário.

Recalcula `total` de cada bill somando `credit_transactions` reais e reabre
bills paid/closed que voltam a ter saldo devedor. Útil quando o DB ficou
inconsistente por bug passado.

Uso:
  cd "Bot Financeiro"
  DATABASE_URL="postgresql://..." python scripts/rebuild_bill_totals.py <user_id>

Pegar o DATABASE_URL no Railway: service Postgres → Variables → DATABASE_URL.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    if len(sys.argv) < 2:
        print("uso: python scripts/rebuild_bill_totals.py <user_id>")
        sys.exit(1)

    try:
        user_id = int(sys.argv[1])
    except ValueError:
        print(f"user_id inválido: {sys.argv[1]!r}")
        sys.exit(1)

    if not os.getenv("DATABASE_URL"):
        print("ERRO: DATABASE_URL não setado.")
        print('  ex: DATABASE_URL="postgresql://..." python scripts/rebuild_bill_totals.py 88648360')
        sys.exit(1)

    from db import rebuild_bill_totals

    print(f"Reconciliando bills do user {user_id}...")
    out = rebuild_bill_totals(user_id)
    print(f"  totais corrigidos: {out['totals_updated']}")
    print(f"  bills reabertas:   {out['reopened']}")
    print("Pronto.")


if __name__ == "__main__":
    main()
