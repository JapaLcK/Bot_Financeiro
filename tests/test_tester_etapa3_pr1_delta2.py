"""Segunda passada independente: delta T1–T5 e classes irmãs, banco UUID."""
from datetime import date, timedelta
from decimal import Decimal as D
from pathlib import Path
import pytest
from psycopg.types.json import Jsonb
import db
from test_cashflow_snapshot import q, snapshot
from core.services.cashflow_contract import centavos, somar, Ocorrencia
from core.services.cashflow import _projection, validar_extra
from core.services.cashflow_forecast import _trajectory
from core.services.cashflow_snapshot import _datas, Snapshot, valores_fatura
from core.services.ai_chat.tools.cards import _forecast_next_bill
from db.bills import create_boleto, mark_bill_paid
from db.recurring import create_recurring_expense
from db.recurring_income import create_recurring_income

@pytest.mark.parametrize('income', [False, True])
@pytest.mark.parametrize('boundary', ['today', 'end', 'after', 'none'])
def test_delta_calendario_bordas_sem_apagar_incerto(user_id, income, boundary):
    today = date.today(); end = today + timedelta(days=30)
    start = {'today': today, 'end': end, 'after': end+timedelta(days=1), 'none': today}[boundary]
    if income:
        r=create_recurring_income(user_id,'Renda fronteira',10,'outros',start.day,start_date=start)
        table='recurring_incomes'
    else:
        r=create_recurring_expense(user_id,'Gasto fronteira',10,'outros',start.day,'account',frequency='once',start_date=start)
        table='recurring_expenses'
    q(f'update {table} set frequency=%s,start_date=%s where id=%s and user_id=%s',
      ('legada',None if boundary=='none' else start,r['id'],user_id))
    s=snapshot(user_id,30)
    events=[e for e in s.ocorrencias if e.origem_id==r['id']]
    reasons=[m for m in s.motivos if m.origem_id==r['id']]
    if boundary=='after':
        assert not events and not reasons
    else:
        assert len(events)==1 and events[0].data is None
        assert any(m.codigo=='calendario_recorrente_desconhecido' for m in reasons)

@pytest.mark.parametrize('income', [False, True])
def test_delta_calendario_legitimo_hoje_e_fim_inclusivos(user_id,income):
    today=date.today(); end=today+timedelta(days=30)
    for d in (today,end):
        if income:
            create_recurring_income(user_id,f'Renda {d}',10,'outros',d.day,start_date=d)
        else:
            create_recurring_expense(user_id,f'Gasto {d}',10,'outros',d.day,'account',frequency='once',start_date=d)
    events=snapshot(user_id,30).ocorrencias
    assert today in {e.data for e in events} and end in {e.data for e in events}

@pytest.mark.parametrize('amount',['0e1000000000','-0e-1000000000','1e-323','-1e-323','2.685','-2.685'])
def test_delta_zero_subnormal_e_meio_centavo(amount):
    d=validar_extra(amount)
    expected=D('-2.68') if amount=='-2.685' else D('2.68') if amount=='2.685' else D(0)
    assert centavos(d)==expected
    assert somar([D('.01'),d.copy_negate(),d])==D('.01')

@pytest.mark.parametrize('amount',['1e400','-1e400','1e-1000000','-1e-1000000','sNaN',False])
def test_delta_magnitude_ilegivel_recusada(amount):
    with pytest.raises(ValueError): validar_extra(amount)

def test_delta_cancelamento_reordenado_interno_e_trajetoria():
    today=date.today(); big=D('1'+'0'*150+'.01')
    base=dict(saldo=D('.01'),balance_source='manual',of_bank_count=0,banks_excluded=False)
    events=[Ocorrencia('receita',1,'x',today,'receita','Entrada',big,'entrada'),
            Ocorrencia('boleto',2,'x',today,'boleto','Saida',big,'saida'),
            Ocorrencia('receita',3,'x',today,'receita','Centavo',D('.01'),'entrada')]
    assert _projection(today,base,events,today)['projetado']==D('.02')
    assert _trajectory(today,base,events,1,0)['trajectory'][0]['saldo_projetado']==D('.02')
    assert somar([big,D('.01'),big.copy_negate()])==D('.01')

@pytest.mark.parametrize('funding',[False,'bank',{},[],{'kind':'carteira'}])
def test_delta_metadado_nao_fisico_nao_prova_realizacao(user_id,funding):
    db.add_launch_and_update_balance(user_id,'receita',100,'Dinheiro',None)
    b=create_boleto(user_id,'Pagamento auditado',20,date.today()+timedelta(days=2))
    mark_bill_paid(user_id,b['id'],20,metodo='carteira')
    lid=q('select launch_id from bill_instances where id=%s and user_id=%s',(b['id'],user_id))[0]['launch_id']
    q('update launches set efeitos=%s where id=%s and user_id=%s',
      (Jsonb({'delta_conta':-20,'funding_source':funding}),lid,user_id))
    s=snapshot(user_id); e=next(e for e in s.ocorrencias if e.fonte=='instancia')
    assert e.realizacao=='a_conferir' and e.assinado==D(-20)
    assert 'carteira_nao_confirmada' in {m.codigo for m in s.motivos}

def test_delta_funding_none_fisico_legitimo(user_id):
    from core.services.funding import to_db_arg
    assert to_db_arg({'kind':'carteira'}) is None
    assert to_db_arg({'kind':'bank','of_account_id':1})['kind']=='bank'
    db.add_launch_and_update_balance(user_id,'receita',100,'Dinheiro',None)
    b=create_boleto(user_id,'Pagamento legitimo',20,date.today()+timedelta(days=2))
    mark_bill_paid(user_id,b['id'],20,metodo='carteira')
    lid=q('select launch_id from bill_instances where id=%s and user_id=%s',(b['id'],user_id))[0]['launch_id']
    q('update launches set efeitos=%s where id=%s and user_id=%s',
      (Jsonb({'delta_conta':-20,'funding_source':None}),lid,user_id))
    s=snapshot(user_id);e=next(e for e in s.ocorrencias if e.fonte=='instancia')
    assert e.realizacao=='realizada' and e.assinado==0 and s.base['saldo']==D(80)
    assert 'carteira_nao_confirmada' not in {m.codigo for m in s.motivos}

@pytest.mark.parametrize('total,paid',[('NaN','0'),('100','NaN'),('Infinity','0'),('100','-Infinity')])
def test_delta_fatura_nao_finita_preserva_observacao(user_id,total,paid):
    card=db.create_card(user_id,'Fatura nao finita',10,17)
    db.add_credit_purchase(user_id,card,100,'outros','Compra',date.today())
    q('update credit_bills set total=%s,paid_amount=%s,status=%s where user_id=%s and card_id=%s',(D(total),D(paid),'paid',user_id,card))
    before=q('select total::text,paid_amount::text,status from credit_bills where user_id=%s',(user_id,))
    out=_forecast_next_bill(user_id,{})
    assert out['count']==1 and out['cards'][0]['restante'] is None
    assert 'valor_fatura_a_conferir' in out['cards'][0]['motivos']
    assert next(e for e in snapshot(user_id,90).ocorrencias if e.fonte=='fatura').valor is None
    assert q('select total::text,paid_amount::text,status from credit_bills where user_id=%s',(user_id,))==before

@pytest.mark.parametrize("magnitude",["100","1e100"],ids=["legitimo","grande"])
def test_delta_fatura_total_agregado_preserva_centavo_apos_cancelamento(pro_small_uid,magnitude):
    user_id=pro_small_uid
    # Total observado inclui crédito a conferir: mesma classe irmã de T2/T5.
    for i,total in enumerate([D(magnitude),D('.01'),D(magnitude).copy_negate()]):
        card=db.create_card(user_id,f'Fatura agregada {i}',10,17)
        db.add_credit_purchase(user_id,card,1,'outros','Compra',date.today())
        q('update credit_bills set total=%s,paid_amount=0,status=%s where user_id=%s and card_id=%s',(total,'open',user_id,card))
    before=q('select total,paid_amount,status from credit_bills where user_id=%s order by id',(user_id,))
    out=_forecast_next_bill(user_id,{})
    assert out['count']==3 and out['cards'][2]['restante'] is None
    assert out['total']==0.01,out
    assert q('select total,paid_amount,status from credit_bills where user_id=%s order by id',(user_id,))==before

def test_delta_gate_predicado_real_novos():
    import test_max_lines_python as gate
    root=Path(__file__).resolve().parents[1]
    for rel in ('core/services/cashflow_contract.py','core/services/cashflow_snapshot.py','db/carteira_qualidade.py'):
        assert not gate._estoura(rel,(root/rel).read_bytes())
    assert gate._estoura('core/services/novo.py',b'x\n'*351)

@pytest.mark.parametrize('value,expected',[('2.68',5.36),('2.685',5.37)],ids=['centavos','meio_centavo'])
def test_delta_total_faturas_soma_brutos_antes_quantizar(pro_small_uid,value,expected):
    for i in range(2):
        card=db.create_card(pro_small_uid,f'Total bruto {i}',10,17)
        db.add_credit_purchase(pro_small_uid,card,1,'outros','Compra',date.today())
        q('update credit_bills set total=%s,paid_amount=0 where user_id=%s and card_id=%s',
          (D(value),pro_small_uid,card))
    out=_forecast_next_bill(pro_small_uid,{})
    assert out['count']==2
    assert out['total']==expected,out
