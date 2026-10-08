"""Antigo × novo do "quanto tenho investido?" (PR F). SÓ LÊ.

Antigo = `sum(balance)` de `investments` (o cadastro manual que o WhatsApp e a IA somavam;
sem o accrual que a leitura antiga aplicava — a diferença dele não muda o balde). Novo =
`db/investido.calcular()["total"]` (só posições dos bancos conectados; `None` = não sabe).

Saída padrão: SÓ contagens por balde, nenhum user_id —
  igual            antigo == novo
  manual_sem_banco R$ X -> null  (passa a ouvir "ainda não sei, conecte seu banco")
  manual_com_banco o manual sai do número
  so_banco         R$ 0 -> total do banco
  nada             0 -> null
`--usuarios 12 34` imprime por usuário: só para contas de teste.

Rodar (na raiz do repositório):
    PROD_DATABASE_URL='postgres://...' .venv/bin/python scripts/comparar_investido.py
A conexão abre com `default_transaction_read_only=on`.

Teste: tests/test_comparar_investido.py.
"""
import argparse
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.investido import calcular  # noqa: E402

BALDES = ("igual", "manual_sem_banco", "manual_com_banco", "so_banco", "nada")
# Todos de `users`, como scripts/cleanup_poisoned_category_rules.py:48: conta apagada e a
# fundida pelo merge_users já saíram da tabela; quem não tem nada também muda (0 -> null).
_TODOS_SQL = "select id as user_id from users order by id"


def balde(antigo, novo) -> str:
    if novo is not None and novo == antigo:
        return "igual"
    if antigo > 0:
        return "manual_sem_banco" if novo is None else "manual_com_banco"
    return "nada" if novo is None else "so_banco"


def comparar(conn, usuarios=None) -> list[str]:
    """`conn` com linhas em dict e transação só de leitura. Devolve as linhas impressas."""
    with conn.cursor() as cur:
        if usuarios is None:
            cur.execute(_TODOS_SQL)
            usuarios_ = [r["user_id"] for r in cur.fetchall()]
        else:
            usuarios_ = usuarios
        contagem, linhas = Counter(), []
        for uid in usuarios_:
            cur.execute("select coalesce(sum(balance), 0) as s from investments where user_id=%s", (uid,))
            antigo = cur.fetchone()["s"]
            novo = calcular(cur, uid)["total"]
            b = balde(antigo, novo)
            contagem[b] += 1
            if usuarios is not None:
                linhas.append(f"{uid} | antigo {antigo} -> novo {novo} | {b}")
    return linhas + [f"{b}: {contagem[b]}" for b in BALDES]


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--usuarios", nargs="+", type=int)
    args = p.parse_args()
    url = os.environ.get("PROD_DATABASE_URL")
    if not url:
        sys.exit("defina PROD_DATABASE_URL (de propósito não lê o .env)")
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(url, row_factory=dict_row,
                         options="-c default_transaction_read_only=on") as conn:
        print("\n".join(comparar(conn, args.usuarios)))
        conn.rollback()


if __name__ == "__main__":
    main()
