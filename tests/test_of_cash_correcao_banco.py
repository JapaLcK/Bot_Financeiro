"""Q41 grupo 2: o banco corrige valor/data de um saque já creditado → a Carteira segue."""
from decimal import Decimal

from conftest import usuario_pagante
from tests._of_cash_helpers import caixa, carteira, conecta, dia, links, q, sync, tx  # noqa: F401


def _launch(uid):
    (row,) = q("select valor, criado_em, efeitos from launches where user_id=%s and source='manual'",
               (uid,), True)
    return row


def test_correcao_de_valor_e_data_segue_o_banco(caixa):
    uid = usuario_pagante()
    c = conecta(uid, f"item-{uid}")
    sync(c, uid, [tx("t1", -200, dia(10))])
    assert carteira(uid) == Decimal("200")

    sync(c, uid, [tx("t1", -250, dia(11))])

    assert carteira(uid) == Decimal("250")
    lan = _launch(uid)
    assert Decimal(str(lan["valor"])) == Decimal("250")
    assert Decimal(str(lan["efeitos"]["delta_conta"])) == Decimal("250")
    assert lan["criado_em"].date() == dia(11)
    (link,) = links(uid)
    assert (Decimal(str(link["amount"])), link["tx_date"]) == (Decimal("250"), dia(11))


def test_sync_repetido_nao_mexe(caixa):
    """Positivo: o mesmo extrato duas vezes não credita de novo nem reescreve."""
    uid = usuario_pagante()
    c = conecta(uid, f"item-{uid}")
    for _ in range(3):
        sync(c, uid, [tx("t1", -200, dia(10)), tx("t2", -50, dia(12))])
    assert carteira(uid) == Decimal("250")
    assert len(links(uid)) == 2
    assert q("select count(*) as n from launches where user_id=%s and source='manual'", (uid,), True)[0]["n"] == 2
