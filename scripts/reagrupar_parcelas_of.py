#!/usr/bin/env python3
"""Reagrupa as parcelas do cartão do Open Finance já gravadas (backfill do #1290).

O #1290 mudou a chave do `group_id` (`chave_grupo_of`: loja sem o "n/N" + dia da compra), mas só
vale para import novo. Aqui cada linha `source='open_finance'` com `group_id` e parcelas recebe a
chave que o import lhe daria hoje (mesma função, mesmo `card_id`, mesmo espelho mais novo do
`CREDIT_LINKS_SQL`). Mexe SÓ em `credit_transactions.group_id`: valor, fatura, saldo, nota e
categoria ficam como estão (nota/categoria de grupos que se juntam NÃO são unificadas).

  * dry-run (padrão): transação READ ONLY com rollback; saída só de contagens (sem descrição
    nem valor);
  * --apply: por usuário, uma transação; trava as linhas (ordem de id) e grava com
    compare-and-set no group_id lido: linha mexida entre a leitura e a escrita fica de fora.

Idempotente: a 2ª execução do --apply dá 0.

Uso (no container; staging antes, dry-run de produção visto antes do --apply):
    python -m scripts.reagrupar_parcelas_of                   # dry-run, todos
    python -m scripts.reagrupar_parcelas_of --user 123        # dry-run de 1 usuário
    python -m scripts.reagrupar_parcelas_of [--user 123] --apply
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date

import db
from db.cards import extract_installment_info
from db.open_finance import CREDIT_LINKS_SQL
from db.parcelas_of import chave_grupo_of, sem_parcela
from scripts.corrigir_sinal_cartao_of import _banco, _ids

CONTADORES = ("linhas_que_mudam", "grupos_antes", "grupos_depois", "grupos_que_se_juntam",
              "juntados_com_nota_divergente", "juntados_com_categoria_divergente",
              "juntados_com_installment_no_repetido", "grupos_que_se_partem", "linhas_sem_purchaseDate",
              "linhas_sem_corte_de_sufixo")


def _sem_data(raw: dict | None) -> bool:
    try:
        date.fromisoformat(str((((raw or {}).get("creditCardMetadata") or {}).get("purchaseDate")))[:10])
        return False
    except ValueError:
        return True


def _plano(cur, user_id: int) -> tuple[dict, list]:
    """(contagens, [(linha, group_id novo)] só das que mudam). Só lê; o dono é a conexão E a linha."""
    cur.execute(CREDIT_LINKS_SQL, (user_id, None, None))
    linhas = cur.fetchall()
    # o raw só aqui (o sync lê o CREDIT_LINKS_SQL a cada rodada e não precisa dele); ids vindos do SQL do dono
    cur.execute("select o.id, o.raw from open_finance_transactions o "
                "join open_finance_accounts a on a.id = o.account_id "
                "join open_finance_connections c on c.id = a.connection_id "
                "where c.user_id = %s and o.id = any(%s)", (user_id, [r["of_tx_id"] for r in linhas]))
    raws = {x["id"]: x["raw"] for x in cur.fetchall()}
    todas = []
    for r in linhas:
        r["tx_raw"] = raws.get(r["of_tx_id"])
        n, total = extract_installment_info(r["tx_raw"])
        if r["source"] != "open_finance" or r["group_id"] is None or r["card_id"] is None or not total:
            continue  # sem parcelas: fica como está; nunca grava null
        todas.append((r, chave_grupo_of(r["card_id"], r["description"], r["tx_raw"],
                                        r["transaction_date"], n, total), n, total))
    por_novo = defaultdict(list)
    for r, novo, _, _ in todas:
        por_novo[novo].append(r)
    juntam = [rs for rs in por_novo.values() if len({r["group_id"] for r in rs}) > 1]
    mudam = [(r, novo) for r, novo, _, _ in todas if novo != r["group_id"]]
    rel = dict(zip(CONTADORES, (
        len(mudam), len({r["group_id"] for r, *_ in todas}), len(por_novo), len(juntam),
        sum(len({(r["nota"] or "").strip() for r in rs}) > 1 for rs in juntam),
        sum(len({r["cur_cat"] for r in rs}) > 1 for rs in juntam),
        sum(len({r["installment_no"] for r in rs}) < len(rs) for rs in juntam),
        # grupo que hoje é um só e vira mais de um (ex.: espelho de reconexão com outro purchaseDate)
        sum(len(v) > 1 for v in _novos_por_antigo(todas).values()),
        sum(_sem_data(r["tx_raw"]) for r, *_ in todas),
        sum(sem_parcela(r["description"], n, t) == (r["description"] or "").strip() for r, _, n, t in todas),
    )))
    return rel, mudam


def _novos_por_antigo(todas) -> dict:
    out = defaultdict(set)
    for r, novo, _, _ in todas:
        out[r["group_id"]].add(novo)
    return out


def conta_usuario(user_id: int) -> dict:
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("set transaction read only")
        rel, _ = _plano(cur, user_id)
        conn.rollback()
    return {"user": user_id, **rel}


def aplica_usuario(user_id: int) -> dict:
    # Trava só `credit_transactions` (uma família, por id): não há 2ª trava que cruze com o
    # `_lock_user` de quem escreve em várias famílias (docs/armadilhas.md, ordem de lock).
    with db.get_conn() as conn, conn.cursor() as cur:
        rel, mudam = _plano(cur, user_id)
        mudam.sort(key=lambda m: m[0]["ct_id"])
        cur.execute("select id from credit_transactions where user_id=%s and id=any(%s) order by id for update",
                    (user_id, [r["ct_id"] for r, _ in mudam]))
        gravadas = 0
        for r, novo in mudam:
            cur.execute("update credit_transactions set group_id=%s where id=%s and user_id=%s "
                        "and group_id is not distinct from %s", (novo, r["ct_id"], user_id, r["group_id"]))
            gravadas += cur.rowcount
        conn.commit()
    return {"user": user_id, **rel, "gravadas": gravadas}


def main(argv: list[str] | None = None, falhas: list[int] | None = None) -> list[dict]:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="grava (sem isso, dry-run)")
    ap.add_argument("--user", type=int, help="limita a um único user_id")
    args = ap.parse_args(argv)
    if args.user is not None and not db.user_exists(args.user):
        ap.error(f"user {args.user} não existe — confira o id.")

    print(_banco(), "(--apply)" if args.apply else "(dry-run)")
    out, falhas = [], ([] if falhas is None else falhas)
    for uid in ([args.user] if args.user is not None else _ids()):
        try:  # um usuário com dado estranho não para os outros (a transação dele faz rollback)
            out.append((aplica_usuario if args.apply else conta_usuario)(uid))
        except Exception as exc:  # noqa: BLE001
            falhas.append(uid)  # tipo + sqlstate (40P01 = deadlock: rode de novo); sem a mensagem, que pode trazer dado
            print({"user": uid, "erro": type(exc).__name__, "sqlstate": getattr(exc, "sqlstate", None)})
    for rel in out:
        if rel["linhas_que_mudam"]:
            print(rel)
    total = {k: sum(r[k] for r in out) for k in (*CONTADORES, *(["gravadas"] if args.apply else []))}
    print("total", {"usuarios": len(out), "usuarios_com_erro": len(falhas), **total})
    if not args.apply:
        print("dry-run: nada foi gravado.")
    return out


if __name__ == "__main__":
    erros: list[int] = []
    main(falhas=erros)
    raise SystemExit(1 if erros else 0)  # rodada parcial não sai com 0
