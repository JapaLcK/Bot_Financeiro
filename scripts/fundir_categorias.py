#!/usr/bin/env python3
"""Funde as categorias gêmeas e as em inglês do catálogo (issue #149, PR C).

O #713 traduz as linhas do Open Finance, mas o 1º seed (`ensure_user_categories_seeded`)
já tinha copiado para `user_categories` os nomes da Pluggy ("groceries") e grafias
gêmeas ("cafe"/"café"). Este script, por usuário e numa transação só:

  * linha em inglês (chave de `PLUGGY_PARA_PIGBANK`) sem uso fora do OF vai para a
    categoria do PigBank; com uso fora do OF (lançamento manual, regra, recorrente,
    orçamento, conta avulsa) é pulada e listada;
  * gêmeas de grafia (mesmo `normalize_text`) viram uma: vence a de sistema, depois
    a ativa, depois a mais usada, depois o menor id; se mais de uma do grupo tem
    orçamento, ou se a vencedora está arquivada e alguma perdedora ativa (que não seja
    a em inglês), pula.

Fundir = `cascata_nome_categoria` (a mesma do rename) + apagar os alertas de
orçamento que ela não moveu + apagar a linha perdedora.

O dry-run é READ-ONLY de verdade (armadilha do `cleanup_poisoned_category_rules`):
faz tudo na transação e dá ROLLBACK, e não chama `ensure_user`, o seed nem
`list_user_categories_full`, que escrevem. `--user` inexistente aborta.

Uso:
    .venv/bin/python -m scripts.fundir_categorias                    # dry-run global
    .venv/bin/python -m scripts.fundir_categorias --user 123         # dry-run de 1 user
    .venv/bin/python -m scripts.fundir_categorias [--user 123] --apply
"""
from __future__ import annotations

import argparse
from collections import defaultdict

import db
from db.categories import cascata_nome_categoria
from db.open_finance_categories import PLUGGY_PARA_PIGBANK, garantir_no_catalogo
from utils_text import CATEGORY_LABELS, normalize_text

_NAO_OF = " and coalesce(source,'') <> 'open_finance'"
# (tabela, coluna, filtro): onde um nome em inglês pode estar em uso fora do OF.
_FORA_DO_OF = (
    ("launches", "categoria", _NAO_OF),
    ("credit_transactions", "categoria", _NAO_OF),
    ("user_category_rules", "category", ""),
    ("recurring_expenses", "category", ""),
    ("recurring_incomes", "category", ""),
    ("category_budgets", "categoria", ""),
    ("bill_instances", "category", ""),
)


def _chave(x: str) -> str:
    return normalize_text(x) or x  # `or x`: emoji puro normaliza para ""


def _conta(cur, tabela: str, coluna: str, user_id: int, nome: str, filtro: str = "") -> int:
    cur.execute(
        f"select count(*) as n from {tabela} where user_id=%s and lower({coluna})=lower(%s){filtro}",
        (user_id, nome),
    )
    return int(cur.fetchone()["n"])


def _ids() -> list[int]:
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select distinct user_id from user_categories order by 1")
        return [r["user_id"] for r in cur.fetchall()]


def fundir_usuario(user_id: int, aplicar: bool) -> dict:
    rel = {"user": user_id, "fundidas": [], "puladas": [], "arquivadas": [], "criar": [], "falhou": []}
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select id, name, is_system, is_archived from user_categories "
            "where user_id=%s order by id for update",
            (user_id,),
        )
        grupos: dict[str, list[dict]] = defaultdict(list)
        for c in [dict(r) for r in cur.fetchall()]:
            nome = c["name"]
            c["uso"] = (_conta(cur, "launches", "categoria", user_id, nome)
                        + _conta(cur, "credit_transactions", "categoria", user_id, nome))
            c["orcamento"] = _conta(cur, "category_budgets", "categoria", user_id, nome) > 0
            # Exato em minúsculas: sem o fallback " - " de `categoria_pigbank`, que
            # fundiria a custom "Travel - Japão" em "lazer". Nunca normalize_text.
            c["destino"] = PLUGGY_PARA_PIGBANK.get(nome.strip().lower())
            if c["destino"]:
                fora = sum(_conta(cur, t, col, user_id, nome, f) for t, col, f in _FORA_DO_OF)
                if fora:
                    rel["puladas"].append((nome, f"uso fora do OF ({fora})"))
                    continue
            grupos[_chave(c["destino"] or nome)].append(c)

        for grupo in grupos.values():
            locais = [c for c in grupo if not c["destino"]]
            if len(grupo) < 2 and not any(c["destino"] for c in grupo):
                continue
            # A cascata de orçamento não sobrescreve um orçamento que já existe no
            # alvo (not exists): com dois no grupo, o da perdedora ficaria órfão.
            if sum(c["orcamento"] for c in grupo) > 1:
                rel["puladas"].append((", ".join(c["name"] for c in grupo),
                                       "orçamento em mais de uma"))
                continue
            venc = min(locais, key=lambda c: (not c["is_system"], c["is_archived"], -c["uso"], c["id"])) if locais else None
            alvo = venc["name"] if venc else grupo[0]["destino"]
            if venc and venc["is_archived"]:
                # Só sobra vencedora arquivada de sistema: fundir esconderia o ativo.
                # O alias em inglês (com destino) não conta: ele É o alvo da fusão.
                if any(not c["is_archived"] and not c["destino"] for c in grupo):
                    rel["puladas"].append((", ".join(c["name"] for c in grupo),
                                           "vencedora arquivada com perdedora ativa"))
                    continue
                rel["arquivadas"].append(alvo)
            if not venc and alvo not in CATEGORY_LABELS.values():
                rel["criar"].append(alvo)
            for c in grupo:
                if c is venc:
                    continue
                n = cascata_nome_categoria(cur, user_id, c["name"], alvo)
                # A cascata não move o alerta que colide com um da vencedora (not
                # exists); é só dedupe de aviso, e sem a linha do catálogo fica órfão.
                # `<> alvo`: gêmeas só de caixa ("Cafe"/"cafe") casam no mesmo lower().
                cur.execute("delete from budget_alert_sent where user_id=%s and lower(categoria)=lower(%s) "
                            "and lower(categoria)<>lower(%s)", (user_id, c["name"], alvo))
                cur.execute("delete from user_categories where user_id=%s and id=%s",
                            (user_id, c["id"]))
                rel["fundidas"].append((c["name"], alvo, n))
        conn.commit() if aplicar else conn.rollback()

    if aplicar and rel["criar"]:
        garantir_no_catalogo(user_id, rel["criar"])  # abre conexão própria: fora da transação
        # `ensure_user_category` engole falha de banco: confere o que nasceu de fato.
        # A linha em inglês já foi apagada, então reexecutar não repara.
        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute("select lower(name) as n from user_categories where user_id=%s", (user_id,))
            existe = {r["n"] for r in cur.fetchall()}
        rel["falhou"] = [x for x in rel["criar"] if x.lower() not in existe]
        rel["criar"] = [x for x in rel["criar"] if x.lower() in existe]
    return rel


def _imprime(rel: dict, aplicar: bool) -> None:
    print(f"user {rel['user']}:")
    for de, para, n in rel["fundidas"]:
        movidas = ", ".join(f"{t}={k}" for t, k in n.items() if k)
        print(f"  funde '{de}' -> '{para}'  ({movidas or 'nenhuma linha'})")
    for nome, motivo in rel["puladas"]:
        print(f"  pula '{nome}': {motivo}")
    for nome in rel["arquivadas"]:
        print(f"  vencedora arquivada: '{nome}'")
    for nome in rel["criar"]:
        print(f"  {'criou' if aplicar else 'criaria'} '{nome}' no catálogo")
    for nome in rel["falhou"]:
        print(f"  FALHOU ao criar '{nome}' (linha de catálogo ausente; crie pela tela ou reexecute a sync)")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="grava (sem isso, dry-run)")
    ap.add_argument("--user", type=int, help="limita a um único user_id")
    args = ap.parse_args(argv)
    if args.user is not None and not db.user_exists(args.user):
        ap.error(f"user {args.user} não existe — confira o id.")

    total = falhas = 0
    for uid in [args.user] if args.user is not None else _ids():
        rel = fundir_usuario(uid, args.apply)
        if rel["fundidas"] or rel["puladas"] or rel["criar"] or rel["falhou"]:
            _imprime(rel, args.apply)
        total += len(rel["fundidas"])
        falhas += bool(rel["falhou"])
    verbo = "gravada(s)" if args.apply else "encontrada(s) — dry-run, nada foi gravado"
    print(f"\n{total} fusão(ões) {verbo}.")
    if falhas:
        print(f"{falhas} usuário(s) com categoria de destino que não nasceu no catálogo.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
