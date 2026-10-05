"""Ataques direcionados do Tester2 ao delta de seleção/avanço de cartões."""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from threading import Event, current_thread
from decimal import Decimal
import pytest
import db
import db.cards as cards
from tests.test_of_identidade_e_campos_bancarios import conta, ciclo, conecta, q, transacao


def account(uid, cid):
    return q('select a.id from open_finance_accounts a join open_finance_connections c on c.id=a.connection_id where c.user_id=%s and c.id=%s', (uid,cid),True)[0]['id']


def tx(ident, value=-12):
    result = transacao(value)
    result['provider_transaction_id'] = ident
    return result


def snapshot(uid):
    return tuple(q(f'select * from {table} where user_id=%s order by id',(uid,),True) for table in ['credit_transactions','credit_bills'])


@pytest.mark.parametrize('with_purchases', [False,True])
def test_prioriza_cartao_com_compra_e_preserva_ambos(user_id, with_purchases):
    uid=user_id
    c1=conecta(uid,f'a-{uid}')
    c2=conecta(uid,f'b-{uid}')
    for c in [c1,c2]:
        db.save_open_finance_sync(c,[conta('estavel','CREDIT')])
    ids=[]
    for c in [c1,c2]:
        ids.append(q("insert into credit_cards(user_id,name,closing_day,due_day,open_finance_account_id) values (%s,%s,1,10,%s) returning id",(uid,f'cartao-{c}',account(uid,c)),True)[0]['id'])
    if with_purchases:
        cards.add_imported_credit_purchase(uid,ids[1],-12,'lazer',transacao()['transaction_date'],'legado')
    before=snapshot(uid)
    c3=conecta(uid,f'novo-{uid}')
    db.save_open_finance_sync(c3,[conta('estavel','CREDIT')])
    result=cards.get_or_create_open_finance_card(uid,account(uid,c3),None,{})
    assert result == ids[1 if with_purchases else 0]
    assert snapshot(uid)==before
    assert len(q('select id from credit_cards where user_id=%s',(uid,),True))==2


@contextmanager
def interception(monkeypatch, callback):
    original=cards.get_conn
    class Cursor:
        def __init__(self, cursor): self.inner=cursor
        def __getattr__(self,key): return getattr(self.inner,key)
        def execute(self,query,params=None):
            return callback(self.inner,query,params)
    class Conn:
        def __init__(self, conn): self.inner=conn
        def __getattr__(self,key): return getattr(self.inner,key)
        @contextmanager
        def cursor(self):
            with self.inner.cursor() as cur:
                yield Cursor(cur)
    @contextmanager
    def wrapped():
        with original() as conn:
            yield Conn(conn)
    with monkeypatch.context() as patch:
        patch.setattr(cards,'get_conn',wrapped)
        yield


def setup_three(uid):
    first=conecta(uid,f'a-{uid}')
    ciclo(uid,first,[conta('estavel','CREDIT')])
    card=q('select id from credit_cards where user_id=%s',(uid,),True)[0]['id']
    middle=conecta(uid,f'b-{uid}')
    last=conecta(uid,f'c-{uid}')
    for c in [middle,last]: db.save_open_finance_sync(c,[conta('estavel','CREDIT')])
    return card,account(uid,first),account(uid,middle),account(uid,last)


def test_update_rele_associacao_apos_select_concorrente(user_id, monkeypatch):
    card,first,middle,last=setup_three(user_id)
    ready,release=Event(),Event()
    def intercept(cur,query,params):
        if 'update credit_cards cc set open_finance_account_id=new.id' in query and current_thread().name.startswith('stale'):
            ready.set()
            assert release.wait(5)
        return cur.execute(query,params)
    before=snapshot(user_id)
    with interception(monkeypatch,intercept), ThreadPoolExecutor(max_workers=1,thread_name_prefix='stale') as pool:
        future=pool.submit(cards.get_or_create_open_finance_card,user_id,middle,'estavel',{})
        try:
            assert ready.wait(5)
            assert cards.get_or_create_open_finance_card(user_id,last,'estavel',{})==card
        finally:
            release.set()
        assert future.result(timeout=5)==card
    assert cards.get_card_by_id(user_id,card)['open_finance_account_id']==last
    assert snapshot(user_id)==before


def test_falha_depois_update_desfaz_associacao(user_id,monkeypatch):
    card,first,middle,last=setup_three(user_id)
    before=snapshot(user_id)
    def intercept(cur,query,params):
        result=cur.execute(query,params)
        if 'update credit_cards cc set open_finance_account_id=new.id' in query:
            raise RuntimeError('falha entre UPDATE e COMMIT')
        return result
    with interception(monkeypatch,intercept):
        with pytest.raises(RuntimeError,match='entre UPDATE e COMMIT'):
            cards.get_or_create_open_finance_card(user_id,last,None,{})
    assert cards.get_card_by_id(user_id,card)['open_finance_account_id']==first
    assert snapshot(user_id)==before


def test_reconexao_isola_cartoes_entre_usuarios(user_id):
    uid=user_id
    foreign=uid+1
    db.ensure_user(foreign)
    for owner in [uid,foreign]:
        c=conecta(owner,f'original-{owner}')
        ciclo(owner,c,[conta('estavel','CREDIT')])
    before=snapshot(foreign)
    foreign_cards=q('select * from credit_cards where user_id=%s',(foreign,),True)
    c2=conecta(uid,f'novo-{uid}')
    ciclo(uid,c2,[conta('estavel','CREDIT')])
    assert snapshot(foreign)==before
    assert q('select * from credit_cards where user_id=%s',(foreign,),True)==foreign_cards


def test_dois_legados_com_compras_reconecta_e_desconecta_antigas(user_id):
    uid=user_id
    old_connections=[]
    card_ids=[]
    for num in [1,2]:
        cid=conecta(uid,f'legado-{num}-{uid}')
        old_connections.append(cid)
        transaction=tx(f't{num}',-num*10)
        db.save_open_finance_sync(cid,[conta('estavel','CREDIT',transaction)])
        aid=account(uid,cid)
        card=q("insert into credit_cards(user_id,name,closing_day,due_day,open_finance_account_id) values (%s,%s,1,10,%s) returning id",(uid,f'legado-{num}',aid),True)[0]['id']
        card_ids.append(card)
        purchase,_=cards.add_imported_credit_purchase(uid,card,transaction['amount'],'lazer',transaction['transaction_date'],transaction['provider_transaction_id'])
        q('update open_finance_transactions set imported_credit_tx_id=%s where account_id=%s',(purchase,aid))
    before=snapshot(uid)
    newest=conecta(uid,f'novo-{uid}')
    a=conta('estavel','CREDIT')
    a['transactions']=[tx('t1',-10),tx('t2',-20)]
    ciclo(uid,newest,[a])
    # Chaves externas podem ser canonizadas, mas IDs/faturas/totais não mudam.
    after=snapshot(uid)
    assert [(r['id'],r['card_id'],r['bill_id'],r['valor']) for r in before[0]] == [(r['id'],r['card_id'],r['bill_id'],r['valor']) for r in after[0]]
    assert after[1]==before[1]
    for cid in old_connections: db.disconnect_open_finance_connection(uid,cid)
    assert snapshot(uid)==after
    states=[cards.get_card_by_id(uid,card) for card in card_ids]
    assert all(c['of_sync_active'] for c in states), [(c['id'],c['open_finance_account_id'],c['of_sync_active']) for c in states]
