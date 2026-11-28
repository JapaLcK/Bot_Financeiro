#!/usr/bin/env python3
"""Liga o modo condomínio numa conta: `users.tipo_conta='condominio'` + plano de contas semeado.

Numa transação só; idempotente (rodar de novo não duplica nem desfaz grupo alterado).
Conta inexistente aborta (não é criada). Avisa, sem forçar, se o plano não libera categorias
personalizadas / categorização por IA.

Uso:
    .venv/bin/python -m scripts.ativar_conta_condominio --user-id 123
"""
from __future__ import annotations

import argparse

import db
from core.services.plan_service import plan_gate_ok
from db.categories import ensure_user_categories_seeded
from db.categorias_condominio import semear_condominio


def ativar(user_id: int) -> dict[str, int]:
    if not db.user_exists(user_id):
        raise ValueError(f"user {user_id} não existe")  # ensure_user criaria a conta
    ensure_user_categories_seeded(user_id)  # conexão própria; cria as canônicas que o seed vai arquivar
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("update users set tipo_conta='condominio' where id=%s", (user_id,))
        resumo = semear_condominio(cur, user_id)
        conn.commit()
    return resumo


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--user-id", type=int, required=True)
    args = ap.parse_args(argv)
    if not db.user_exists(args.user_id):
        ap.error(f"user {args.user_id} não existe — confira o id.")
    resumo = ativar(args.user_id)
    for feature in ("custom_categories", "ai_categorization"):
        if not plan_gate_ok(args.user_id, feature):
            print(f"AVISO: o plano da conta não libera {feature}.")
    print(f"user {args.user_id}: tipo_conta=condominio; {resumo}")


if __name__ == "__main__":
    main()
