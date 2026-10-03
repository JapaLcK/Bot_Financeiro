"""Antigo × novo do "Resumo do mês" (Q18/Q30), por usuário e por mês. SÓ LÊ.

Antigo = `get_summary_by_period` (só `launches`, o que o "Gastos em <mês>" e o relatório
mensal mostravam) na MESMA janela do novo: o mês-calendário inteiro, o corrente também
(o WhatsApp lia o corrente só até hoje; aqui não, para a diferença ser só o cartão e um
lançamento com data futura no mês não virar "NÃO"). Novo = `db/resumo_mes.totais` (com o
cartão pela fatura). `hoje` só escolhe o mês mais recente. "só cartão?"
= a receita não mudou e a diferença do Saiu é EXATAMENTE a soma das compras no cartão das
faturas que fecham no mês (conta independente, abaixo). Imprime user_id e valores: use
com a lista fixa de usuários de teste.

Rodar (na raiz do repositório):
    PROD_DATABASE_URL='postgres://...' .venv/bin/python scripts/comparar_resumo_mes.py 12 34 --meses 6
A conexão abre com `default_transaction_read_only=on`; o `ensure_user` do antigo é trocado
por um no-op (o original grava em `users`).

Teste: tests/test_comparar_resumo_mes.py.
"""
import argparse
import os
import sys
from contextlib import nullcontext
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.resumo_mes import mes_de, totais  # noqa: E402
from db_support import get_summary_by_period_impl  # noqa: E402

# A perna do cartão escrita de novo, de propósito: é a régua do "só cartão?", e não pode
# vir de `TOTAIS_SQL` (seria medir a regra com ela mesma).
CARTAO_SQL = """
    select coalesce(sum(ct.valor), 0) as c
      from credit_transactions ct join credit_bills b on b.id = ct.bill_id
     where ct.user_id = %s and coalesce(b.user_id, (select cb.user_id from credit_cards cb where cb.id = b.card_id)) = %s and ct.is_refund = false
       and b.period_end >= %s and b.period_end < %s
"""


def comparar(conn, user_ids, meses: int, hoje: date) -> list[str]:
    """`conn` com linhas em dict e transação só de leitura. Devolve as linhas impressas."""
    linhas = []
    for uid in user_ids:
        inicio = mes_de(hoje)[0]
        for _ in range(meses):
            fim = mes_de(inicio)[1]
            velho = get_summary_by_period_impl(lambda: nullcontext(conn), lambda _u: None, uid,
                                               inicio, fim - timedelta(days=1))
            with conn.cursor() as cur:
                novo = totais(cur, uid, inicio, fim)
                cur.execute(CARTAO_SQL, (uid, uid, inicio, fim))
                cartao = cur.fetchone()["c"]
            v_entrou, v_saiu = Decimal(str(velho["receita"])), Decimal(str(velho["despesa"]))
            d_saiu = novo["saiu"] - v_saiu
            so_cartao = novo["entrou"] == v_entrou and d_saiu == cartao
            linhas.append(f"{uid} {inicio:%Y-%m} | entrou {v_entrou} -> {novo['entrou']} | "
                          f"saiu {v_saiu} -> {novo['saiu']} (dif {d_saiu}) | cartão {cartao} | "
                          f"só cartão? {'sim' if so_cartao else 'NÃO'}")
            inicio = mes_de(inicio - timedelta(days=1))[0]
    return linhas


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("user_ids", nargs="+", type=int)
    p.add_argument("--meses", type=int, default=6)
    args = p.parse_args()
    url = os.environ.get("PROD_DATABASE_URL")
    if not url:
        sys.exit("defina PROD_DATABASE_URL (de propósito não lê o .env)")
    import psycopg
    from psycopg.rows import dict_row

    from utils_date import today_tz
    with psycopg.connect(url, row_factory=dict_row,
                         options="-c default_transaction_read_only=on") as conn:
        print("\n".join(comparar(conn, args.user_ids, max(1, args.meses), today_tz())))
        conn.rollback()


if __name__ == "__main__":
    main()
