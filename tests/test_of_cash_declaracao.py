"""Q41 × declarações bancárias (db/bank_movements.py): a transação que virou
saque da Carteira não pode ser também a prova de uma declaração (conta em dobro)."""
import pytest

import db
from conftest import usuario_pagante
from tests._of_cash_helpers import caixa, conecta, dia, q, sync, tx  # noqa: F401


def _declara(uid, conta_id):
    lid, _, _ = db.add_launch_and_update_balance(uid, "despesa", 200, "caixinha", None)
    q("""insert into bank_movement_declarations(launch_id, user_id, account_id, amount, declared_at)
         values (%s, %s, %s, -200, now())""", (lid, uid, conta_id))
    return lid


def _tx_id(uid, ident):
    return q("""select t.id, t.account_id from open_finance_transactions t
                  join open_finance_accounts a on a.id = t.account_id
                  join open_finance_connections c on c.id = a.connection_id
                 where c.user_id=%s and t.provider_transaction_id=%s""", (uid, ident), True)[0]


def test_saque_da_carteira_nao_confirma_declaracao(caixa):
    uid = usuario_pagante()
    sync(conecta(uid, f"item-{uid}"), uid, [
        tx("s1", -200, dia(10)),
        tx("o1", -200, dia(10), op="TRANSFERENCIA", desc="Transf", category="Same person transfer"),
    ])
    s1, o1 = _tx_id(uid, "s1"), _tx_id(uid, "o1")
    lid = _declara(uid, s1["account_id"])
    q("update bank_movement_declarations set declared_at=%s where launch_id=%s", (dia(10), lid))

    with pytest.raises(LookupError):
        db.bank_movements.confirm_bank_movement(uid, lid, s1["id"])
    db.bank_movements.confirm_bank_movement(uid, lid, o1["id"])  # positivo: a outra serve
