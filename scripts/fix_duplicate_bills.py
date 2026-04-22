"""
Script de limpeza pontual: consolida credit_bills duplicadas para todos os cartões.

Uso:
  cd "Bot Financeiro"
  python scripts/fix_duplicate_bills.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
from db.cards import consolidate_duplicate_bills

def main():
    conn = db.get_conn()
    with conn.cursor() as cur:
        cur.execute("select distinct user_id, id, closing_day, name from credit_cards")
        cards = cur.fetchall()

    if not cards:
        print("Nenhum cartão encontrado.")
        return

    total_merges = 0
    for card in cards:
        merges = consolidate_duplicate_bills(card["user_id"], card["id"], int(card["closing_day"]))
        if merges:
            print(f"  Cartão '{card['name']}' (id={card['id']}): {merges} fatura(s) mesclada(s)")
            total_merges += merges

    if total_merges == 0:
        print("Nenhuma fatura duplicada encontrada. Tudo OK!")
    else:
        print(f"\nTotal: {total_merges} fatura(s) duplicada(s) removida(s).")

if __name__ == "__main__":
    main()
