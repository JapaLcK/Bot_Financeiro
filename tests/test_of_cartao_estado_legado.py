"""Estado OF segue os vínculos preservados, inclusive em cartões legados separados."""
import pytest

import db
import db.cards as cards
from tests.test_pr3_tester2_cartoes import account, snapshot, tx
from tests.test_of_identidade_e_campos_bancarios import conta, ciclo, conecta, q


def legado_reconectado(uid, amount=20):
    old_connections, card_ids = [], []
    for num in (1, 2):
        cid = conecta(uid, f"legado-{num}-{uid}")
        old_connections.append(cid)
        transaction = tx(f"t{num}", amount)
        db.save_open_finance_sync(cid, [conta("estavel", "CREDIT", transaction)])
        aid = account(uid, cid)
        card = q("insert into credit_cards(user_id,name,closing_day,due_day,open_finance_account_id) "
                 "values (%s,%s,1,10,%s) returning id", (uid, f"legado-{num}", aid), True)[0]["id"]
        card_ids.append(card)
        purchase, _ = cards.add_imported_credit_purchase(
            uid, card, amount, "lazer", transaction["transaction_date"], transaction["provider_transaction_id"])
        q("update open_finance_transactions set imported_credit_tx_id=%s where account_id=%s", (purchase, aid))
    newest = conecta(uid, f"novo-{uid}")
    a = conta("estavel", "CREDIT")
    a["transactions"] = [tx("t1", amount), tx("t2", amount)]
    ciclo(uid, newest, [a])
    return old_connections, newest, card_ids


@pytest.mark.parametrize("status", ["PAUSED", "DELETED"])
@pytest.mark.parametrize("disconnect_old", [False, True])
def test_estado_novo_prevalece_sobre_fk_antiga(user_id, status, disconnect_old):
    old, newest, card_ids = legado_reconectado(user_id)
    before = snapshot(user_id)
    assert all(cards.get_card_by_id(user_id, card)["of_sync_active"] for card in card_ids)
    q("update open_finance_connections set status=%s where user_id=%s and id=%s", (status, user_id, newest))
    assert not any(cards.get_card_by_id(user_id, card)["of_sync_active"] for card in card_ids)
    if disconnect_old:
        for cid in old:
            db.disconnect_open_finance_connection(user_id, cid)
    assert not any(cards.get_card_by_id(user_id, card)["of_sync_active"] for card in card_ids)
    q("update open_finance_connections set status='ACTIVE' where user_id=%s and id=%s", (user_id, newest))
    assert all(cards.get_card_by_id(user_id, card)["of_sync_active"] for card in card_ids)
    assert snapshot(user_id) == before


def test_estorno_sem_fk_mantem_cobertura_ativa(user_id):
    old, newest, card_ids = legado_reconectado(user_id, amount=-20)
    before = snapshot(user_id)
    for cid in old:
        db.disconnect_open_finance_connection(user_id, cid)
    second = cards.get_card_by_id(user_id, card_ids[1])
    assert second["open_finance_account_id"] is None
    assert second["of_sync_active"] is True
    from core.handlers.credit import compra_fica_com_o_of
    cards.set_default_card(user_id, card_ids[1])
    assert compra_fica_com_o_of(user_id, "gastei 10 no crédito") is True
    db.pause_open_finance_connection(newest)
    assert compra_fica_com_o_of(user_id, "gastei 10 no crédito") is False
    assert all(row["valor"] == -20 for row in before[0])
    assert snapshot(user_id) == before


@pytest.mark.parametrize("status,active", [("ACTIVE", True), ("PAUSED", False), ("DELETED", False)])
def test_sem_compras_usa_fk_e_manual_permanece_manual(user_id, status, active):
    cid = conecta(user_id, f"sem-compra-{user_id}")
    a = conta("estavel", "CREDIT")
    a["transactions"] = []
    db.save_open_finance_sync(cid, [a])
    card = cards.get_or_create_open_finance_card(user_id, account(user_id, cid), "Banco", {})
    manual = q("insert into credit_cards(user_id,name,closing_day,due_day) "
               "values (%s,'Manual',1,10) returning id", (user_id,), True)[0]["id"]
    q("update open_finance_connections set status=%s where user_id=%s and id=%s", (status, user_id, cid))
    assert cards.get_card_by_id(user_id, card)["of_sync_active"] is active
    assert cards.get_card_by_id(user_id, manual)["of_sync_active"] is False
    assert snapshot(user_id)[0] == []


@pytest.mark.parametrize("foreign_link", ["connection", "purchase"])
def test_vinculo_de_outro_usuario_nao_ativa_cartao(user_id, foreign_link):
    other = user_id + 1
    db.ensure_user(other)
    owner = other if foreign_link == "connection" else user_id
    cid = conecta(owner, f"estrangeiro-{user_id}")
    ciclo(owner, cid, [conta("estavel", "CREDIT")])
    purchase = snapshot(owner)[0][0]
    target = q("insert into credit_cards(user_id,name,closing_day,due_day) "
               "values (%s,'Manual',1,10) returning id", (user_id,), True)[0]["id"]
    # As FKs simples permitem legado inconsistente; a leitura não pode confiar nele.
    q("update credit_transactions set card_id=%s, user_id=%s where user_id=%s and id=%s",
      (target, user_id if foreign_link == "connection" else other, owner, purchase["id"]))
    assert cards.get_card_by_id(user_id, target)["of_sync_active"] is False
    assert cards.get_card_by_id(other, target) is None
