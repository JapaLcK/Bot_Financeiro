"""`ler_id` e `um_item` (db/lancamentos*.py): o id `l<id>`/`c<id>` e o item de UMA linha do usuário.

CONTROLE NEGATIVO (rodado na entrega): `ident` ignorado no `listar` (ou a perna da outra tabela sem
o `and false`) deixa vermelho o caso da colisão `l<N>` x `c<N>` e o do item pedido no meio de vários.
CONTROLE POSITIVO: o item de `um_item` é o MESMO que a lista do Extrato devolve (campo a campo).
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest

import db
from db import lancamentos, lancamentos_leitura


@pytest.mark.parametrize("texto,esperado", [
    ("l42", ("l", 42)), ("c1", ("c", 1)), ("l9223372036854775807", ("l", 2**63 - 1)),
    ("l9223372036854775808", None), ("l0", None), ("l042", None), ("L42", None), ("c-1", None),
    ("42", None), ("l", None), ("", None), ("l42\n", None), ("l 42", None), ("l٤٢", None),
    ("l99999999999999999999", None), (42, None), (None, None),
])
def test_ler_id(texto, esperado):
    assert lancamentos.ler_id(texto) == esperado


def _lista(uid):
    from db.connection import get_conn
    with get_conn() as conn, conn.cursor() as cur:
        linhas, _ = lancamentos.listar(cur, uid, date.min, date.max, com_guardado=True)
        conn.rollback()
    return [lancamentos._item(r) for r in linhas]


def test_um_item_acha_o_certo_entre_varios_e_igual_ao_extrato(user_id):
    for i in range(4):
        db.add_launch_and_update_balance(user_id, "despesa", 10 + i, f"Loja {i}", None)
    todos = _lista(user_id)
    assert len(todos) == 4
    for item in todos:  # o do meio e o do fim, nao so o mais novo
        t, n = lancamentos.ler_id(item["id"])
        assert lancamentos_leitura.um_item(user_id, t, n) == item


def test_colisao_l_n_contra_c_n(user_id):
    """`l<N>` olha so `launches.id` e `c<N>` so `credit_transactions.id`, mesmo com N igual."""
    lid, _seq, _ = db.add_launch_and_update_balance(user_id, "despesa", 50, "Mercado", None)
    card = db.create_card(user_id, "Manual", closing_day=10, due_day=17)
    tx, _, _ = db.add_credit_purchase(user_id, card, 30, "outros", "Uber", date.today())
    from db.connection import get_conn
    with get_conn() as conn, conn.cursor() as cur:  # forca o mesmo numero nas duas tabelas
        cur.execute("update credit_transactions set id = %s where id = %s and user_id = %s", (lid, tx, user_id))
        conn.commit()
    l = lancamentos_leitura.um_item(user_id, "l", lid)
    c = lancamentos_leitura.um_item(user_id, "c", lid)
    assert (l["id"], l["descricao"]) == (f"l{lid}", "Mercado")
    assert (c["id"], c["descricao"]) == (f"c{lid}", "Uber")


def test_um_item_de_outro_usuario_ou_inexistente_e_none(user_id):
    outro = int(uuid.uuid4().int % 10_000_000_000)
    db.ensure_user(outro)
    lid, _, _ = db.add_launch_and_update_balance(outro, "despesa", 50, "Do outro", None)
    assert lancamentos_leitura.um_item(outro, "l", lid) is not None  # controle: o dono le
    assert lancamentos_leitura.um_item(user_id, "l", lid) is None
    assert lancamentos_leitura.um_item(user_id, "c", lid) is None
    assert lancamentos_leitura.um_item(user_id, "l", 2**63 - 1) is None
