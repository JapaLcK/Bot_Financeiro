"""Conta já ocupada por cartão legado prevalece sobre ranking entre aliases."""
import pytest
import db
import db.cards as cards
from tests.test_of_identidade_e_campos_bancarios import conta, ciclo, conecta, q, transacao
from tests.test_pr3_tester2_cartoes import account, snapshot, interception


def legado(uid, current_owner=True):
    first = conecta(uid, f'antiga-{uid}')
    ciclo(uid, first, [conta('estavel', 'CREDIT')])
    old_card = q('select id from credit_cards where user_id=%s', (uid,), True)[0]['id']
    new = conecta(uid, f'nova-{uid}')
    db.save_open_finance_sync(new, [conta('estavel', 'CREDIT')])
    aid = account(uid, new)
    exact = None
    if current_owner:
        exact = q("insert into credit_cards(user_id,name,closing_day,due_day,open_finance_account_id) values (%s,'Nome editado',1,10,%s) returning id", (uid, aid), True)[0]['id']
    return first, new, aid, old_card, exact


@pytest.mark.parametrize('history', [False, True])
def test_import_prioriza_dono_exato_sem_mover_legado(user_id, history):
    first, new, aid, old_card, exact = legado(user_id)
    before = snapshot(user_id)
    target = first if history else new
    owner = old_card if history else exact
    tx = transacao(-25)
    tx['provider_transaction_id'] = 'compra-exclusiva'
    ciclo(user_id, target, [conta('estavel', 'CREDIT', tx)])
    after = snapshot(user_id)
    assert [r for r in after[0] if r['id'] in {x['id'] for x in before[0]}] == before[0]
    assert q('select card_id from credit_transactions where user_id=%s and valor=25', (user_id,), True) == [{'card_id': owner}]
    assert cards.get_card_by_id(user_id, exact)['open_finance_account_id'] == aid
    assert cards.get_card_by_id(user_id, exact)['name'] == 'Nome editado'
    old_bills = {r['id']: r['card_id'] for r in before[1]}
    assert {r['id']: r['card_id'] for r in after[1] if r['id'] in old_bills} == old_bills
    assert sum(r['total'] for r in after[1]) == sum(r['total'] for r in before[1]) + 25
    assert len(q('select id from credit_cards where user_id=%s', (user_id,), True)) == 2


def test_dono_criado_entre_select_e_update_e_respeitado(user_id, monkeypatch):
    first, new, aid, old_card, _ = legado(user_id, current_owner=False)
    before = snapshot(user_id)
    created = []
    def intercept(cur, query, params):
        if 'update credit_cards cc set open_finance_account_id=new.id' in query and not created:
            created.append(q("insert into credit_cards(user_id,name,closing_day,due_day,open_finance_account_id) values (%s,'Outro dono',1,10,%s) returning id", (user_id, aid), True)[0]['id'])
        return cur.execute(query, params)
    with interception(monkeypatch, intercept):
        result = cards.get_or_create_open_finance_card(user_id, aid, 'estavel', {})
    assert result == created[0]
    assert snapshot(user_id) == before
    assert cards.get_card_by_id(user_id, old_card)['open_finance_account_id'] == account(user_id, first)
