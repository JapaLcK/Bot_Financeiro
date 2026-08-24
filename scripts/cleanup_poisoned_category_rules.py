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

ATENÇÃO: `user_category_rules` não registra proveniência — não dá pra saber, pela
linha, se uma regra foi auto-aprendida (veneno) ou criada DE PROPÓSITO pelo
usuário via "linkar" (que não passa pelo guard). Uma regra deliberada
`cinema -> lazer` de quem tem a custom "cinema da família" apareceria aqui como
colisão, mas apagá-la seria perder config do usuário. Por isso `--apply` exige
`--user`: rode global em dry-run, revise a lista, e só então aplique cliente a
cliente conferindo que nenhuma daquelas regras é intencional.

O dry-run é READ-ONLY de verdade: nenhuma das duas leituras (`_all_user_ids`,
`_rules_somente_leitura`) escreve, e `--user` inexistente aborta em vez de ser
criado. Sem isso o próprio conselho de segurança deste script ("rode o dry-run
primeiro") criava dado em produção.

Uso:
    .venv/bin/python -m scripts.cleanup_poisoned_category_rules              # dry-run global (só reporta)
    .venv/bin/python -m scripts.cleanup_poisoned_category_rules --user 314149836            # dry-run de 1 user
    .venv/bin/python -m scripts.cleanup_poisoned_category_rules --user 314149836 --apply    # deleta (revisado)
"""
from __future__ import annotations

import argparse

import db
from core.services.category_service import custom_category_match
from utils_text import normalize_text


def _all_user_ids() -> list[int]:
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select id from users order by id")
        rows = cur.fetchall() or []
    return [r["id"] if isinstance(r, dict) else r[0] for r in rows]


def _user_existe(user_id: int) -> bool:
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select 1 from users where id=%s", (user_id,))
        return cur.fetchone() is not None


def _rules_somente_leitura(user_id: int) -> list[tuple[str, str]]:
    """As regras do usuário, SEM `ensure_user`. Mesma query, sem o write.

    `list_user_category_rules` (db/categories.py) abre com `ensure_user`, que
    INSERE linha em `users` E em `accounts` e commita antes de devolver lista
    nenhuma. Num script cujo argumento de segurança inteiro é "rode o dry-run
    primeiro", isso significava que o dry-run CRIAVA em produção o cliente que o
    operador digitou errado — e reportava "0 regras", com cara de nada a fazer.

    Não é caso isolado do `--user`: todo leitor de regra do repo passa pelo
    mesmo `ensure_user` (`list_category_rules`, `get_memorized_category`,
    `list_user_category_rules`), não existe versão read-only pra reusar. Por
    isso a query mora aqui, e é a ÚNICA porta de leitura do script.
    """
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select keyword, category from user_category_rules "
            "where user_id=%s order by length(keyword) desc",
            (user_id,),
        )
        rows = cur.fetchall() or []
    return [
        (
            (r["keyword"] if isinstance(r, dict) else r[0]) or "",
            (r["category"] if isinstance(r, dict) else r[1]) or "",
        )
        for r in rows
    ]


def find_poisoned(user_id: int) -> list[tuple[str, str, str]]:
    """[(keyword, categoria_da_regra, categoria_custom_que_ela_rouba)]."""
    out: list[tuple[str, str, str]] = []
    for keyword, category in _rules_somente_leitura(user_id):
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

    # Segurança: apagar exige escopo de 1 user (revisão manual), porque uma regra
    # colidente pode ter sido criada de propósito e não há como distinguir em lote.
    if args.apply and not args.user:
        ap.error("--apply exige --user: revise a lista global (dry-run) e aplique "
                 "cliente a cliente, conferindo que nenhuma regra é intencional.")

    # `--user` digitado errado não pode virar "0 regras, nada a fazer": aborta.
    # (O write que ele causava morreu em `_rules_somente_leitura`; isto é o
    # aviso, pra não confundir "cliente limpo" com "cliente que não existe".)
    if args.user is not None and not _user_existe(args.user):
        ap.error(f"user {args.user} não existe — confira o id.")

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
