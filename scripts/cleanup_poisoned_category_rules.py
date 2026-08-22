#!/usr/bin/env python3
"""Limpa regras de categoria ENVENENADAS em user_category_rules.

Uma regra está envenenada quando o keyword dela colide com o token distintivo
de uma categoria CUSTOM do próprio usuário, mas aponta pra uma categoria
DIFERENTE. Como a regra de usuário (passo B em infer_category) vence a categoria
custom (passo B2), todo lançamento que menciona aquele token era sequestrado pra
categoria errada.

Contexto: antes do fix (commit 2013c17), o auto-aprendizado gravava regras como
`namorada -> lazer` mesmo o usuário tendo a categoria custom "gastos com
namorada" (o gatilho foi "namorada cinema", que casa cinema->lazer nas
LOCAL_RULES). O guard novo impede NOVAS regras assim; este script varre as que
já foram gravadas — em qualquer cliente que criou categoria custom antes do fix.

Reusa `custom_category_match` — a MESMA função do guard — então detecta
exatamente a classe que o guard previne, sem reimplementar a heurística em SQL.

Uso:
    .venv/bin/python -m scripts.cleanup_poisoned_category_rules            # dry-run (só reporta)
    .venv/bin/python -m scripts.cleanup_poisoned_category_rules --apply    # deleta de verdade
    .venv/bin/python -m scripts.cleanup_poisoned_category_rules --user 314149836
"""
from __future__ import annotations

import argparse

import db
from db.categories import list_user_category_rules
from core.services.category_service import custom_category_match
from utils_text import normalize_text


def _all_user_ids() -> list[int]:
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select id from users order by id")
        rows = cur.fetchall() or []
    return [r["id"] if isinstance(r, dict) else r[0] for r in rows]


def find_poisoned(user_id: int) -> list[tuple[str, str, str]]:
    """[(keyword, categoria_da_regra, categoria_custom_que_ela_rouba)]."""
    out: list[tuple[str, str, str]] = []
    for keyword, category in list_user_category_rules(user_id):
        custom = custom_category_match(user_id, normalize_text(keyword))
        if custom and normalize_text(custom) != normalize_text(category or ""):
            out.append((keyword, category, custom))
    return out


def _delete_rule(user_id: int, keyword: str) -> None:
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "delete from user_category_rules where user_id=%s and keyword=%s",
            (user_id, keyword),
        )
        conn.commit()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="deleta de verdade (sem isso, só reporta — dry-run)")
    ap.add_argument("--user", type=int, help="limita a um único user_id")
    args = ap.parse_args()

    user_ids = [args.user] if args.user else _all_user_ids()
    total = 0
    afetados = 0
    for uid in user_ids:
        poisoned = find_poisoned(uid)
        if not poisoned:
            continue
        afetados += 1
        print(f"user {uid}:")
        for keyword, cat, custom in poisoned:
            total += 1
            print(f"  '{keyword}' -> '{cat}'  (rouba a categoria custom '{custom}')")
            if args.apply:
                _delete_rule(uid, keyword)

    verbo = "deletada(s)" if args.apply else "encontrada(s) — dry-run, nada foi deletado"
    print(f"\n{total} regra(s) envenenada(s) {verbo} em {afetados} cliente(s).")
    if total and not args.apply:
        print("Rode de novo com --apply pra deletar.")


if __name__ == "__main__":
    main()
