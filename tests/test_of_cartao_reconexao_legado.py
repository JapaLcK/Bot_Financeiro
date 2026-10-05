"""Compatibilidade de cartões duplicados históricos sem consolidar suas faturas."""
from decimal import Decimal

import db
from tests.test_of_identidade_e_campos_bancarios import conta, ciclo, conecta, q, transacao


def cartoes(uid):
    return q("select id,name,open_finance_account_id from credit_cards where user_id=%s order by id",
             (uid,), True)


def compra(ident, valor):
    tx = transacao(valor)
    tx["provider_transaction_id"] = ident
    return tx


def test_legado_com_compras_em_dois_cartoes_nao_move_faturas(user_id):
    uid = user_id
    cid1 = conecta(uid, f"antiga-{uid}")
    ciclo(uid, cid1, [conta("estavel", "CREDIT")])
    original_card = cartoes(uid)[0]["id"]
    cid2 = conecta(uid, f"legada-{uid}")
    db.save_open_finance_sync(cid2, [conta("estavel", "CREDIT")])
    acc2 = q("select a.id from open_finance_accounts a join open_finance_connections c "
             "on c.id=a.connection_id where c.user_id=%s and c.id=%s", (uid, cid2), True)[0]["id"]
    second = q("insert into credit_cards(user_id,name,closing_day,due_day,open_finance_account_id) "
               "values(%s,'Outro cartão legado',1,10,%s) returning id", (uid, acc2), True)[0]["id"]
    db.cards.add_imported_credit_purchase(uid, second, -30, "lazer", transacao()["transaction_date"], "segunda")
    before = q("select id,card_id,bill_id,valor from credit_transactions where user_id=%s order by id",
               (uid,), True)
    bills = q("select id,card_id,total from credit_bills where user_id=%s order by id", (uid,), True)
    cid3 = conecta(uid, f"atual-{uid}")
    ciclo(uid, cid3, [conta("estavel", "CREDIT")])
    assert len(cartoes(uid)) == 2
    assert q("select id,card_id,bill_id,valor from credit_transactions where user_id=%s order by id",
             (uid,), True) == before
    assert q("select id,card_id,total from credit_bills where user_id=%s order by id", (uid,), True) == bills
    ciclo(uid, cid3, [conta("estavel", "CREDIT", compra("nova", -20))])
    assert q("select card_id from credit_transactions where user_id=%s and valor=20", (uid,), True) == [
        {"card_id": original_card}]
    assert len(cartoes(uid)) == 2


def test_cartao_renomeado_historico_antigo_e_disconnect_nao_duplica(user_id):
    uid = user_id
    cid1 = conecta(uid, f"antiga-{uid}")
    ciclo(uid, cid1, [conta("estavel", "CREDIT")])
    original_card = cartoes(uid)[0]["id"]
    cid2 = conecta(uid, f"atual-{uid}")
    ciclo(uid, cid2, [conta("estavel", "CREDIT")])
    linked = cartoes(uid)[0]["open_finance_account_id"]
    assert db.cards.update_card_meta(uid, original_card, name="Meu cartão")
    ciclo(uid, cid1, [conta("estavel", "CREDIT", compra("historica", -12))])
    assert cartoes(uid)[0]["open_finance_account_id"] == linked
    db.disconnect_open_finance_connection(uid, cid1)
    ciclo(uid, cid2, [conta("estavel", "CREDIT", compra("nova", -25))])
    assert cartoes(uid) == [{"id": original_card, "name": "Meu cartão", "open_finance_account_id": linked}]
    assert q("select sum(total) as total from credit_bills where user_id=%s", (uid,), True) == [
        {"total": Decimal(125)}]
