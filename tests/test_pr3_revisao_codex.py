"""Regressões apontadas no PR 831; banco descartável do conftest."""
import pytest
import db
from db.bank_movements import _bind
from tests.test_of_identidade_e_campos_bancarios import (
    conecta, ciclo, conta, espelho, fundida, lancamento, q,
)


def test_movimento_bancario_compartilhado_nao_bloqueia_sync(user_id):
    uid = user_id
    cid, lid, _, _ = fundida(uid)
    db.save_open_finance_sync(cid, [conta('outra')])
    tx = espelho(uid)[-1]
    with db.get_conn() as conn, conn.cursor() as cur:
        _bind(cur, uid, {'launch_id': lid}, {'id': tx['id'], 'imported_launch_id': None}, 'manual')
        conn.commit()
    before = lancamento(uid, lid)
    db.import_open_finance_launches(uid, cid)
    db.sync_imported_open_finance_updates(uid, cid)
    assert lancamento(uid, lid) == before
    assert len([r for r in espelho(uid) if r['imported_launch_id'] == lid]) == 2


@pytest.mark.parametrize('kind', ['BANK', 'CREDIT', 'FUSED'])
def test_desconectar_nova_preserva_importacao_do_alias(user_id, kind):
    uid = user_id
    if kind == 'FUSED':
        old, ident, _, _ = fundida(uid)
        key = 'conta'
    else:
        old = conecta(uid, f'old-{uid}')
        key = 'estavel'
        ciclo(uid, old, [conta(key, kind)])
        ident = espelho(uid)[0]['imported_launch_id' if kind == 'BANK' else 'imported_credit_tx_id']
    new = conecta(uid, f'new-{uid}')
    ciclo(uid, new, [conta(key, 'BANK' if kind == 'FUSED' else kind)])
    table = 'credit_transactions' if kind == 'CREDIT' else 'launches'
    before = q(f'select * from {table} where user_id=%s and id=%s', (uid, ident), True)
    bills = q('select id,total from credit_bills where user_id=%s order by id', (uid,), True)
    db.disconnect_open_finance_connection(uid, new)
    assert q(f'select * from {table} where user_id=%s and id=%s', (uid, ident), True) == before
    assert q('select id,total from credit_bills where user_id=%s order by id', (uid,), True) == bills
    link = 'imported_credit_tx_id' if kind == 'CREDIT' else 'imported_launch_id'
    assert [r[link] for r in espelho(uid)] == [ident]


@pytest.mark.parametrize('kind', ['BANK', 'CREDIT', 'FUSED'])
@pytest.mark.parametrize('status,event,preserve', [
    ('PAUSED', 'disconnect', True), ('DELETED', 'disconnect', False),
    ('ACTIVE', 'all', False), ('ACTIVE', 'deleted', False),
])
def test_saida_por_estado_e_evento(user_id, kind, status, event, preserve):
    uid = user_id
    if kind == 'FUSED':
        old, ident, _, original = fundida(uid)
        key = 'conta'
    else:
        old = conecta(uid, f'old-{uid}')
        key = 'estavel'
        ciclo(uid, old, [conta(key, kind)])
        ident = espelho(uid)[0]['imported_launch_id' if kind == 'BANK' else 'imported_credit_tx_id']
    new = conecta(uid, f'new-{uid}')
    ciclo(uid, new, [conta(key, 'BANK' if kind == 'FUSED' else kind)])
    q('update open_finance_connections set status=%s where user_id=%s and id=%s', (status, uid, old))
    table = 'credit_transactions' if kind == 'CREDIT' else 'launches'
    before = q(f'select * from {table} where user_id=%s and id=%s', (uid, ident), True)
    if event == 'deleted':
        db.delete_open_finance_transactions(f'new-{uid}', ['igual'])
    else:
        db.disconnect_open_finance_connection(uid, None if event == 'all' else new)
    after = q(f'select * from {table} where user_id=%s and id=%s', (uid, ident), True)
    if preserve:
        assert after == before
        if kind == 'CREDIT':
            assert db.get_card_by_id(uid, before[0]['card_id'])['of_sync_active'] is False
    elif kind == 'FUSED':
        assert after[0]['efeitos'] == original['efeitos']
        assert after[0]['criado_em'] == original['criado_em']
    else:
        assert after == []


def test_cartao_renomeado_reimporta_na_antiga_sem_novo_cartao(user_id):
    from tests.test_of_identidade_e_campos_bancarios import transacao
    uid = user_id
    old = conecta(uid, f'old-{uid}')
    ciclo(uid, old, [conta('estavel', 'CREDIT')])
    card = q('select id from credit_cards where user_id=%s', (uid,), True)[0]['id']
    q("update credit_cards set name='Meu cartão' where user_id=%s and id=%s", (uid, card))
    new = conecta(uid, f'new-{uid}')
    ciclo(uid, new, [conta('estavel', 'CREDIT')])
    db.disconnect_open_finance_connection(uid, new)
    tx = transacao(-25)
    tx['provider_transaction_id'] = 'nova-compra'
    ciclo(uid, old, [conta('estavel', 'CREDIT', tx)])
    assert q('select id from credit_cards where user_id=%s', (uid,), True) == [{'id': card}]
    assert {r['card_id'] for r in q('select card_id from credit_transactions where user_id=%s', (uid,), True)} == {card}
    db.delete_open_finance_transactions(f'old-{uid}', ['igual', 'nova-compra'])
    assert q('select id from credit_transactions where user_id=%s', (uid,), True) == []
    tx['provider_transaction_id'] = 'compra-depois-da-exclusao'
    ciclo(uid, old, [conta('estavel', 'CREDIT', tx)])
    assert q('select id from credit_cards where user_id=%s', (uid,), True) == [{'id': card}]
    assert q('select card_id from credit_transactions where user_id=%s', (uid,), True) == [{'card_id': card}]


def test_reconectar_movimento_nao_rouba_vinculo_da_outra_identidade(user_id):
    uid = user_id
    cid, lid, _, _ = fundida(uid)
    db.save_open_finance_sync(cid, [conta('outra')])
    second = espelho(uid)[-1]
    with db.get_conn() as conn, conn.cursor() as cur:
        _bind(cur, uid, {'launch_id': lid}, {'id': second['id'], 'imported_launch_id': None}, 'manual')
        conn.commit()
    before = lancamento(uid, lid)
    new = conecta(uid, f'new-{uid}')
    ciclo(uid, new, [conta('outra')])
    rows = espelho(uid)
    assert rows[0]['imported_launch_id'] == lid
    assert rows[-1]['imported_launch_id'] == lid
    assert rows[-1]['reconciliation_status'] == 'bank_movement_confirmed'
    assert lancamento(uid, lid) == before


def test_movimento_declarado_preserva_prova_ao_desconectar_alias_novo(user_id):
    from tests.test_bank_movements import _bank, _deposit, _sync, _tx
    uid = user_id
    old, source = _bank(uid)
    lid = _deposit(uid, source, 100)
    _sync(old, 900, _tx(-100))
    db.import_open_finance_launches(uid, old)
    new = conecta(uid, f'new-{uid}')
    a = conta(f'account-{old}', tx=_tx(-100))
    ciclo(uid, new, [a])
    # A conferência manual já existe no banco. A desconexão não apaga sua prova.
    current = espelho(uid)[-1]
    from db.bank_movements import confirm_bank_movement
    confirm_bank_movement(uid, lid, current['id'])
    before = q('select * from bank_movement_declarations where user_id=%s', (uid,), True)[0]
    assert before['matched_transaction_id'] == current['id']
    db.disconnect_open_finance_connection(uid, new)
    after = q('select * from bank_movement_declarations where user_id=%s', (uid,), True)[0]
    assert after['matched_transaction_id'] == espelho(uid)[0]['id']
    assert after['confirmation_method'] == before['confirmation_method']
    assert after['requires_review'] == before['requires_review']


def test_bind_transfere_prova_ainda_presente_e_disconnect_antiga_preserva(user_id):
    from tests.test_bank_movements import _bank, _deposit, _sync, _tx
    from db.of_identity import bind_import
    from db.bank_movements import _lock_user
    uid = user_id
    old, source = _bank(uid)
    lid = _deposit(uid, source, 100)
    _sync(old, 900, _tx(-100))
    db.import_open_finance_launches(uid, old)
    new = conecta(uid, f'new-{uid}')
    # Semeia o alias sem chamar o reconciliador de save_open_finance_sync:
    # o objeto deste teste é a prova ainda presente no contrato de bind_import.
    aid = q("""insert into open_finance_accounts(connection_id,provider_account_id,name,type,currency,balance)
               select %s,provider_account_id,name,type,currency,balance
                 from open_finance_accounts where id=%s returning id""",
            (new, source['of_account_id']), True)[0]['id']
    q("""insert into open_finance_transactions(account_id,provider_transaction_id,description,
                amount,transaction_date,category)
         select %s,provider_transaction_id,description,amount,transaction_date,category
           from open_finance_transactions where account_id=%s""", (aid, source['of_account_id']))
    tx = espelho(uid)[-1]
    # Contrato do helper quando recebe prova existente. O reconciliador normal
    # pode invalidá-la antes de chegar aqui; este teste não altera essa política.
    with db.get_conn() as conn, conn.cursor() as cur:
        _lock_user(cur, uid)
        bind_import(cur, uid, dict(of_tx_id=tx['id'], provider='pluggy',
                    provider_account_id=f'account-{old}', provider_transaction_id='tx1'), lid)
        conn.commit()
    db.disconnect_open_finance_connection(uid, old)
    proof = q('select * from bank_movement_declarations where user_id=%s', (uid,), True)[0]
    assert proof['matched_transaction_id'] == tx['id']
    assert proof['account_id'] == tx['account_id']
    assert proof['confirmation_method'] == 'automatic'
    assert proof['requires_review'] is False
    assert 'of_original' not in lancamento(uid, lid)['efeitos']
