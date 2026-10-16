"""Invariantes do PR-A que dependem de outro código: isolamento entre usuários, a ordem de
`_transactions` (o bisect de `janela` assume transações ordenadas por data) e as duas definições de
"fluxo" (Python `e_fluxo` e SQL `DECLARADA_SQL`, §0.7: um teste as compara).

Controles negativos: sem o `order by` de `_transactions`, vermelho em `test_transactions_vem_ordenada_...`;
sem o filtro de usuário em `BANK_ACCOUNTS_SQL` ou em `_declarations`, vermelho em `test_isolamento_...`;
com `DECLARADA_SQL` sem `account_id is null`, vermelho em `test_e_fluxo_e_declarada_sql_concordam`.
"""
from __future__ import annotations

from datetime import timedelta

import db
import db.bank_conferencia as conferencia
import db.bank_movements as movements
from conftest import usuario_pagante
from db.bank_declaracoes import DECLARADA_SQL, e_fluxo
from tests._patrimonio_helpers import q
from tests.test_bank_movements import _bank, _deposit, _sync
from tests.test_declaracao_fluxo_reconcilia import decl, declara, n_linhas, sincroniza, tx


def _dono(lid):
    return q("select t.id from open_finance_transactions t where t.imported_launch_id=%s", (lid,))


def test_isolamento_entre_usuarios_mesmo_valor_e_mesmo_dia():
    a, b = usuario_pagante(), usuario_pagante()
    ca, _ = _bank(a)
    cb, _ = _bank(b)
    la = declara(a)
    _sync(cb, 1000, tx(txid="b1"))  # só o B tem extrato, sem import: a transação ainda não tem dono em `launches`
    assert decl(la)["matched_transaction_id"] is None  # a transação do B não é candidata do A
    lb = declara(b)
    assert decl(lb)["matched_transaction_id"] and decl(la)["matched_transaction_id"] is None
    sincroniza(ca, a, tx(txid="a1"))
    assert decl(la)["matched_transaction_id"]
    assert _dono(la)["id"] != _dono(lb)["id"]
    assert n_linhas(a, 50) == 1


def test_transacoes_do_outro_usuario_nao_tornam_o_candidato_ambiguo():
    a, b = usuario_pagante(), usuario_pagante()
    ca, _ = _bank(a)
    cb, _ = _bank(b)
    _sync(cb, 1000, tx(txid="b1"), tx(txid="b2"))  # sem import, pelo mesmo motivo
    la = declara(a)
    sincroniza(ca, a, tx(txid="a1"))
    assert decl(la)["matched_transaction_id"]  # candidato ÚNICO do A, apesar de 2 iguais no B
    assert [m for m in conferencia.list_bank_movements(b)] == []  # B não declarou nada


def test_transactions_vem_ordenada_por_data_e_id():
    uid = usuario_pagante()
    cid, _ = _bank(uid)
    # fora de ordem na entrada, com datas repetidas (desempate por id)
    sincroniza(cid, uid, tx(txid="d", days=2), tx(txid="a", days=-3), tx(txid="c", days=0),
               tx(txid="b", days=-3), tx(txid="e", days=0))
    with db.get_conn() as conn, conn.cursor() as cur:
        rows = movements._transactions(cur, uid)
        conn.rollback()
    chaves = [(r["transaction_date"], r["id"]) for r in rows]
    assert len(chaves) == 5 and chaves == sorted(chaves)


def test_e_fluxo_e_declarada_sql_concordam():
    """Fluxo = despesa/receita com conta NULL. Aporte e launch `despesa` com conta explícita (fixture do
    `test_of_cash_declaracao`) ficam fora dos dois lados."""
    uid = usuario_pagante()
    _, source = _bank(uid)
    fluxo = declara(uid)
    receita = declara(uid, 30, "receita")
    aporte = _deposit(uid, source)
    despesa_conta = db.add_launch_and_update_balance(uid, "despesa", 200, "x", None)[0]
    q("""insert into bank_movement_declarations(launch_id, user_id, account_id, amount, declared_at)
         values (%s, %s, %s, -200, now()) returning 1""", (despesa_conta, uid, source["of_account_id"]))
    with db.get_conn() as conn, conn.cursor() as cur:
        decls = {d["launch_id"]: d for d in movements._declarations(cur, uid)}
        cur.execute(f"select id, {DECLARADA_SQL} as declarada from launches where user_id=%s", (uid,))
        sql = {r["id"]: r["declarada"] for r in cur.fetchall()}
        conn.rollback()
    assert set(decls) == {fluxo, receita, aporte, despesa_conta}
    for lid, d in decls.items():
        assert sql[lid] is e_fluxo(d), (lid, d["tipo"], d["account_id"])
    assert [lid for lid, d in decls.items() if e_fluxo(d)] == [fluxo, receita]
