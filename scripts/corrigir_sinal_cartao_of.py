#!/usr/bin/env python3
"""Corrige o sinal das compras de cartão do Open Finance já gravadas (PR 0 do sinal).

O sync de cada item já corrige as linhas da conexão que sincroniza
(`_sync_imported_credit_updates` deriva `valor`/`is_refund`/`tipo` do espelho OF e tira da
fatura o pagamento). Este script chega onde o sync não chega: conexão PAUSED/com erro de login.

  * dry-run (padrão): conta, por usuário, o que o --apply corrigiria e removeria. Transação
    READ ONLY com rollback; não chama a função de escrita (ela commita);
  * --apply: chama essa mesma função, por usuário, para todas as conexões dele.

Idempotente: a 2ª execução do --apply dá 0.

Uso (no container de produção, para rodar o mesmo código do deploy; staging antes):
    python -m scripts.corrigir_sinal_cartao_of                  # dry-run, todos
    python -m scripts.corrigir_sinal_cartao_of --user 123       # dry-run de 1 usuário
    python -m scripts.corrigir_sinal_cartao_of [--user 123] --apply
"""
from __future__ import annotations

import argparse
import os
from decimal import Decimal
from urllib.parse import urlparse

import db
from db.cards import sinal_cartao_of
from db.open_finance import CREDIT_LINKS_SQL, _sync_imported_credit_updates, pagamentos_no_cartao_legados


def _ids() -> list[int]:
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select distinct c.user_id from open_finance_connections c "
            "join open_finance_accounts a on a.connection_id = c.id "
            "where upper(a.type) = 'CREDIT' order by 1"
        )
        return [r["user_id"] for r in cur.fetchall()]


def conta_usuario(user_id: int) -> dict:
    """Dry-run de um usuário. Só contagem: nunca descrição nem valor na saída."""
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("set transaction read only")
        cur.execute(CREDIT_LINKS_SQL, (user_id, None, None))
        rows = cur.fetchall()
        remover = pagamentos_no_cartao_legados(cur, user_id)  # inclui o ligado só à conexão velha
        conn.rollback()
    ids = {r["ct_id"] for r in remover}
    corrigir = [
        r for r in rows if r["ct_id"] not in ids
        and sinal_cartao_of(r["amount"])[:2] != (Decimal(str(r["cur_valor"])), bool(r["cur_refund"]))
    ]
    return {"user": user_id, "a_corrigir": len(corrigir), "a_remover": len(ids),
            "faturas": len({r["bill_id"] for r in corrigir + remover})}


def _banco() -> str:
    """Banco e host (sem usuário nem senha) em que o script vai agir, para não errar de alvo."""
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select current_database() as d")
        nome = cur.fetchone()["d"]
    return f"banco={nome} host={urlparse(os.environ.get('DATABASE_URL', '')).hostname}"


def main(argv: list[str] | None = None) -> list[dict]:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="grava (sem isso, dry-run)")
    ap.add_argument("--user", type=int, help="limita a um único user_id")
    args = ap.parse_args(argv)
    if args.user is not None and not db.user_exists(args.user):
        ap.error(f"user {args.user} não existe — confira o id.")

    print(_banco(), "(--apply)" if args.apply else "(dry-run)")
    out = []
    for uid in [args.user] if args.user is not None else _ids():
        if args.apply:
            try:
                rel = {"user": uid, "alteradas": _sync_imported_credit_updates(uid, None)}
            except ValueError as exc:  # OF_IDENTITY_AMBIGUOUS: reporta e segue
                rel = {"user": uid, "erro": str(exc)}
        else:
            rel = conta_usuario(uid)
        print(rel)
        out.append(rel)
    if not args.apply:
        print("dry-run: nada foi gravado.")
    return out


if __name__ == "__main__":
    main()
