from datetime import datetime
from decimal import Decimal
import pytest
import db
from tests.test_of_identidade_e_campos_bancarios import conta, ciclo, conecta, espelho, fundida, lancamento, mes, q, transacao, JAN, FEV, ORIGINAL
from utils_date import _tz


def test_fundida_fim_mes_hora_real(user_id):
    cid, lid, txid, original = fundida(user_id)
    at = datetime(2026, 1, 31, 23, 30, tzinfo=_tz())
    ciclo(user_id, cid, [conta('conta', tx=transacao(-140, JAN, at))])
    row = lancamento(user_id, lid)
    assert row['posted_at'] == JAN
    assert row['criado_em'].astimezone(_tz()) == at
    rows, total = mes(user_id, 1)
    assert total['saiu'] == 140, (rows, total, mes(user_id, 2))
    assert rows[0]['dia'] == JAN


@pytest.mark.parametrize('kind', ['BANK', 'CREDIT'])
def test_identidade_unicode_delimitadores_provedor(user_id, kind):
    c1 = conecta(user_id, f'item-a-{user_id}')
    c2 = conecta(user_id, f'item-b-{user_id}')
    q('update open_finance_connections set provider=%s where id=%s and user_id=%s', ('outro',c2,user_id))
    accs = [conta('a:ç[",]',kind), conta('a',kind)]
    ciclo(user_id,c1,accs)
    ciclo(user_id,c2,accs)
    link = 'imported_launch_id' if kind == 'BANK' else 'imported_credit_tx_id'
    assert len({r[link] for r in espelho(user_id)}) == 4
    assert all(r[link] for r in espelho(user_id))


@pytest.mark.parametrize('amount', ['0','0.01','-0.01','999999999999999.99','-999999999999999.99'])
def test_limites_bancarios_restaura_exato(user_id, amount):
    cid,lid,txid,original = fundida(user_id)
    before = q('select balance from accounts where user_id=%s',(user_id,),True)
    ciclo(user_id,cid,[conta('conta',tx=transacao(amount, FEV))])
    assert lancamento(user_id,lid)['valor'] == abs(Decimal(amount))
    assert q('select balance from accounts where user_id=%s',(user_id,),True) == before
    db.undo_reconciliation(user_id,txid)
    assert lancamento(user_id,lid)['valor'] == original['valor']
    assert lancamento(user_id,lid)['efeitos'] == original['efeitos']


def test_reconexao_cartao_legado_duas_contas_mesma_identidade(user_id):
    cid = conecta(user_id,f'velho-{user_id}')
    ciclo(user_id,cid,[conta('estavel','CREDIT')])
    cid2 = conecta(user_id,f'outro-velho-{user_id}')
    db.save_open_finance_sync(cid2,[conta('estavel','CREDIT')])
    account = q('select a.id from open_finance_accounts a join open_finance_connections c on c.id=a.connection_id where c.user_id=%s and c.id=%s',(user_id,cid2),True)[0]['id']
    # Estado gerado pela main: cartão criado por conta local, compra deduplicada por tx.
    q("insert into credit_cards(user_id,name,closing_day,due_day,open_finance_account_id) values(%s,'Legado segundo',1,10,%s)",(user_id,account))
    cid3 = conecta(user_id,f'novo-{user_id}')
    ciclo(user_id,cid3,[conta('estavel','CREDIT')])
    assert q('select count(*) n from credit_transactions where user_id=%s',(user_id,),True)[0]['n'] == 1


def test_falha_restore_atomica(user_id,monkeypatch):
    import db.reconciliation as reconciliation
    cid,lid,txid,original = fundida(user_id)
    ciclo(user_id,cid,[conta('conta',tx=transacao(-140,FEV))])
    before = lancamento(user_id,lid)
    def falha(*args,**kwargs):
        raise RuntimeError('dependencia interrompida')
    monkeypatch.setattr(reconciliation,'_restore_original',falha)
    with pytest.raises(RuntimeError,match='dependencia interrompida'):
        db.undo_reconciliation(user_id,txid)
    assert lancamento(user_id,lid) == before
    assert len(q('select id from launches where user_id=%s',(user_id,),True)) == 1
    assert espelho(user_id)[0]['imported_launch_id'] == lid




def test_normalizador_data_real_utc_fronteira(user_id):
    from core.services.pluggy_sync import normalize_pluggy_transaction
    cid,lid,txid,original = fundida(user_id)
    normalized = normalize_pluggy_transaction({'id':'igual','amount':-140,'description':'Mercado','category':'Groceries','date':'2026-02-01T01:30:00.000Z'})
    assert normalized['transaction_date'] == FEV
    ciclo(user_id,cid,[conta('conta',tx=normalized)])
    row = lancamento(user_id,lid)
    assert row['posted_at'] == FEV
    # Hora real segue instante local; posted_at literal do espelho difere por fuso.
    assert mes(user_id,1)[1]['saiu'] == 140
    assert mes(user_id,2)[1]['saiu'] == 0
    assert mes(user_id,1)[0][0]['hora'] == '22:30'


def test_snapshot_numerico_grande_sobrevive_edicao_e_restore(user_id):
    db.add_launch_and_update_balance(user_id,'despesa',100,'Mercado',None,'alimentação',ORIGINAL)
    lid = q('select id from launches where user_id=%s',(user_id,),True)[0]['id']
    # Valor válido no numeric, ajustado pelo editor antes de criar pendência.
    value = Decimal('999999999999999.99')
    db.update_launch_fields(user_id,lid,valor=value,exigir_pode=True)
    before = lancamento(user_id,lid)
    cid = conecta(user_id,f'grande-{user_id}')
    ciclo(user_id,cid,[conta('grande',tx=transacao(-value))])
    txid = espelho(user_id)[0]['id']
    db.confirm_reconciliation(user_id,txid)
    db.update_launch_fields(user_id,lid,categoria='viagem',nota='editada')
    db.undo_reconciliation(user_id,txid)
    assert lancamento(user_id,lid)['valor'] == value
    assert lancamento(user_id,lid)['efeitos'] == before['efeitos']


def test_historico_antigo_nao_retoma_cartao_da_conexao_nova(user_id):
    cid1 = conecta(user_id,f'velha-{user_id}')
    ciclo(user_id,cid1,[conta('estavel','CREDIT')])
    cid2 = conecta(user_id,f'nova-{user_id}')
    ciclo(user_id,cid2,[conta('estavel','CREDIT')])
    acc2 = q('select a.id from open_finance_accounts a join open_finance_connections c on c.id=a.connection_id where c.user_id=%s and c.id=%s',(user_id,cid2),True)[0]['id']
    assert q('select open_finance_account_id from credit_cards where user_id=%s',(user_id,),True)[0]['open_finance_account_id'] == acc2
    historical = transacao(-12)
    historical['provider_transaction_id'] = 'historico-ainda-nao-na-resposta-parcial'
    ciclo(user_id,cid1,[conta('estavel','CREDIT',historical)])
    db.disconnect_open_finance_connection(user_id,cid1)
    cards = q('select id,open_finance_account_id from credit_cards where user_id=%s',(user_id,),True)
    purchases = q('select valor from credit_transactions where user_id=%s',(user_id,),True)
    assert purchases == [{'valor':Decimal(100)}]
    assert cards[0]['open_finance_account_id'] == acc2, cards
