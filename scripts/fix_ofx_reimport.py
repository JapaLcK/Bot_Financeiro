"""
scripts/fix_ofx_reimport.py

Remove transações OFX de um cartão específico e limpa o registro de importação
do arquivo, permitindo que o OFX seja reimportado com o parser corrigido.

Uso:
  cd "Bot Financeiro"
  python scripts/fix_ofx_reimport.py
"""
import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Carrega variáveis de ambiente do .env antes de qualquer import do projeto
from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))

import db


def list_cards_with_ofx(conn):
    with conn.cursor() as cur:
        cur.execute("""
            select cc.user_id, cc.id as card_id, cc.name as card_name,
                   count(ct.id) as tx_ofx,
                   oi.file_hash, oi.dt_start, oi.dt_end,
                   oi.inserted_count, oi.created_at
            from credit_cards cc
            join credit_transactions ct on ct.card_id = cc.id and ct.source = 'ofx'
            left join ofx_imports oi on oi.user_id = cc.user_id and oi.acct_type = 'CREDITLINE'
            group by cc.user_id, cc.id, cc.name, oi.file_hash, oi.dt_start, oi.dt_end,
                     oi.inserted_count, oi.created_at
            order by oi.created_at desc
        """)
        return cur.fetchall()


def main():
    conn = db.get_conn()

    print("=" * 60)
    print("Importações OFX de cartão de crédito no banco:")
    print("=" * 60)

    rows = list_cards_with_ofx(conn)
    if not rows:
        print("Nenhuma transação OFX de cartão encontrada.")
        return

    for i, r in enumerate(rows):
        print(f"\n[{i}] Cartão: {r['card_name']} (card_id={r['card_id']}, user_id={r['user_id']})")
        print(f"    Período OFX:  {r['dt_start']} → {r['dt_end']}")
        print(f"    Transações OFX no banco: {r['tx_ofx']}")
        print(f"    Inseridas na importação: {r['inserted_count']}")
        print(f"    Importado em: {r['created_at']}")
        print(f"    file_hash: {(r['file_hash'] or '')[:20]}...")

    print()
    idx = input("Digite o número do registro para limpar (ou 'q' para sair): ").strip()
    if idx.lower() == "q":
        return

    try:
        r = rows[int(idx)]
    except (ValueError, IndexError):
        print("Índice inválido.")
        return

    user_id  = r["user_id"]
    card_id  = r["card_id"]
    card_name = r["card_name"]
    file_hash = r["file_hash"]

    # Mostra o que será deletado
    with conn.cursor() as cur:
        cur.execute("""
            select id, nota, valor, purchased_at, is_refund, external_id
            from credit_transactions
            where user_id=%s and card_id=%s and source='ofx'
            order by purchased_at desc
        """, (user_id, card_id))
        txs = cur.fetchall()

    print(f"\n{len(txs)} transação(ões) OFX a remover do cartão '{card_name}':")
    for t in txs:
        flag = " [ESTORNO]" if t["is_refund"] else ""
        print(f"  R${t['valor']:>8.2f}{flag}  {t['purchased_at']}  {t['nota'][:50]}")

    confirm = input(f"\nConfirma remoção + limpeza do file_hash para reimportar? (sim/não): ").strip().lower()
    if confirm not in ("sim", "s"):
        print("Cancelado.")
        return

    with conn.cursor() as cur:
        # 1. Deleta as transações OFX do cartão
        cur.execute("""
            delete from credit_transactions
            where user_id=%s and card_id=%s and source='ofx'
        """, (user_id, card_id))
        deleted_tx = cur.rowcount

        # 2. Recalcula o total de todas as faturas afetadas
        cur.execute("""
            update credit_bills
            set total = (
                select coalesce(sum(case when is_refund=false then valor else -abs(valor) end), 0)
                from credit_transactions
                where bill_id = credit_bills.id
            )
            where user_id=%s and card_id=%s
        """, (user_id, card_id))

        # 3. Remove faturas que ficaram zeradas (sem nenhuma transação)
        cur.execute("""
            delete from credit_bills
            where user_id=%s and card_id=%s
              and total = 0
              and coalesce(paid_amount, 0) = 0
              and not exists (
                  select 1 from credit_transactions where bill_id = credit_bills.id
              )
        """, (user_id, card_id))
        deleted_bills = cur.rowcount

        # 4. Remove o registro de importação do arquivo (permite reimportar)
        if file_hash:
            cur.execute(
                "delete from ofx_imports where user_id=%s and file_hash=%s",
                (user_id, file_hash)
            )

    conn.commit()

    print(f"\n✅ Pronto!")
    print(f"   {deleted_tx} transação(ões) OFX removida(s)")
    print(f"   {deleted_bills} fatura(s) zerada(s) removida(s)")
    print(f"   Registro de importação limpo — pode reimportar o OFX agora.")


if __name__ == "__main__":
    main()
