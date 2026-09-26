"""Q41 grupo 2: o banco corrige valor/data de um saque já creditado → a Carteira segue."""
from datetime import datetime
from decimal import Decimal

import pytest

import db

from conftest import usuario_pagante
from db.open_finance_cash_answers import answer_link
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


def test_correcao_numa_pergunta_pendente_vale_na_resposta(caixa):
    """Depósito ainda perguntando que o banco corrige (300→350, 10→12/03): a
    resposta "foi dinheiro" debita o valor e a data correntes, não os da 1ª leitura."""
    uid = usuario_pagante()
    c = conecta(uid, f"item-{uid}")
    sync(c, uid, [tx("d1", 300, dia(10), op="DEPOSITO", desc="Transfers")])
    sync(c, uid, [tx("d1", 350, dia(12), op="DEPOSITO", desc="Transfers")])
    (link,) = links(uid)
    assert link["status"] == "perguntar_fraco"

    assert answer_link(uid, link["id"], "cash")["changed"]
    assert carteira(uid) == Decimal("-350"), "debitou o valor da 1ª leitura"
    assert _launch(uid)["criado_em"].date() == dia(12)
    (link,) = links(uid)
    assert (Decimal(str(link["amount"])), link["tx_date"]) == (Decimal("350"), dia(12))


def _renomeia(uid, item, nome="NU PAGAMENTOS S.A."):
    """O upsert do item (mesmo item da Pluggy) troca `institution_name`."""
    db.save_pluggy_open_finance_item(uid, {"id": item, "connector": {"id": 612, "name": nome},
                                           "status": "UPDATED"})


@pytest.mark.parametrize("muda", ["nome", "numero"])
def test_nome_ou_numero_muda_na_conexao_viva_nao_credita_de_novo(caixa, muda):
    """Nome da instituição e número da conta entram na chave e mudam sem reconectar."""
    uid = usuario_pagante()
    c = conecta(uid, f"item-{uid}")
    sync(c, uid, [tx("t1", -200, dia(10))])
    if muda == "nome":
        _renomeia(uid, f"item-{uid}")
    sync(c, uid, [tx("t1", -200, dia(10)), tx("t2", -30, dia(12))], numero="" if muda == "numero" else "0001-9")

    assert carteira(uid) == Decimal("230")
    assert [r["status"] for r in links(uid)] == ["ativo", "ativo"]


def test_nome_novo_e_reconexao_religam(caixa):
    """Depois da troca de nome, desconectar e reconectar (ids novos, mesmo
    providerId, nome novo) religa o vínculo em vez de abrir outro."""
    uid = usuario_pagante()
    c = conecta(uid, f"item-a-{uid}")
    sync(c, uid, [tx("a-1", -200, dia(10), pid="P1")])
    _renomeia(uid, f"item-a-{uid}")
    sync(c, uid, [tx("a-1", -200, dia(10), pid="P1")])
    assert db.disconnect_open_finance_connection(uid, c) == 1
    c2 = conecta(uid, f"item-b-{uid}", desde=datetime(2026, 4, 1, 12), nome="NU PAGAMENTOS S.A.")
    sync(c2, uid, [tx("b-1", -200, dia(10), pid="P1")])

    assert carteira(uid) == Decimal("200")
    (link,) = links(uid)
    assert link["status"] == "ativo" and link["of_transaction_id"]
