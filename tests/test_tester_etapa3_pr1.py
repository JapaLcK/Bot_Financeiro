from datetime import date,datetime,timedelta,timezone
from decimal import Decimal as D
from pathlib import Path
import pytest
import psycopg
import db
from test_cashflow_snapshot import q,snapshot
from core.services.cashflow import project
from core.services.ai_chat.tools.cards import _forecast_next_bill
from db.bills import create_boleto, mark_bill_paid
from db.recurring import create_recurring_expense

@pytest.mark.parametrize('total,paid,status',[(100,110,'paid'),(100,-5,'open'),(-10,0,'open')])
def test_tester_fatura_incoerente_preservada(user_id,total,paid,status):
    card=db.create_card(user_id,'Cartao auditado',10,17)
    db.add_credit_purchase(user_id,card,100,'outros','Compra',date.today())
    bid=q('select id from credit_bills where user_id=%s and card_id=%s',(user_id,card))[0]['id']
    q('update credit_bills set total=%s,paid_amount=%s,status=%s where id=%s and user_id=%s',(total,paid,status,bid,user_id))
    e=next(e for e in snapshot(user_id,90).ocorrencias if e.fonte=='fatura')
    assert e.valor is None
    out=_forecast_next_bill(user_id,{})
    assert len(out['cards'])==1,out
    assert out['cards'][0]['restante'] is None,out
    assert 'valor_fatura_a_conferir' in out['cards'][0]['motivos'],out

def test_tester_fatura_legitima_mantem_total_e_restante(user_id):
    card=db.create_card(user_id,'Cartao legitimo',10,17)
    db.add_credit_purchase(user_id,card,100,'outros','Compra',date.today())
    q('update credit_bills set paid_amount=30 where user_id=%s and card_id=%s',(user_id,card))
    out=_forecast_next_bill(user_id,{})
    assert out['total']==100 and out['cards'][0]['restante']==70

def test_tester_base_ausente_banco_nao_permitido_nao_vira_zero(user_id):
    from tests.test_manual_launches_carteira_piggy import _connect_fake_bank
    _connect_fake_bank(user_id)
    q('delete from accounts where user_id=%s',(user_id,))
    from core.services.cashflow_snapshot import ler
    with db.get_conn() as conn,conn.cursor() as cur:
        cur.execute('set transaction isolation level repeatable read,read only')
        s=ler(cur,user_id,date.today(),date.today()+timedelta(days=30),datetime.now(timezone.utc),False)
        conn.rollback()
    assert s.base['saldo'] is None,s.base

def test_tester_recorrente_legada_futura_invalida_nao_contamina(user_id):
    db.add_launch_and_update_balance(user_id,'despesa',50,'Dinheiro fisico',None)
    before=project(user_id,date.today()+timedelta(days=30))
    assert before['orientacao']=='risco'
    future=date.today()+timedelta(days=100)
    r=create_recurring_expense(user_id,'Futura ignorada',10,'outros',future.day,'account',frequency='once',start_date=future)
    q('update recurring_expenses set frequency=%s,amount=0 where user_id=%s and id=%s',('legada',user_id,r['id']))
    s=snapshot(user_id,30)
    after=project(user_id,date.today()+timedelta(days=30))
    assert after['orientacao']==before['orientacao'],after
    assert not s.ocorrencias,s.ocorrencias
    assert not any(m.origem_id==r['id'] for m in s.motivos),s.motivos

def test_tester_extra_finito_grande_nao_estoura_decimal(pro_small_uid):
    from core.services.ai_chat.tools.bills import _check_cashflow
    out=_check_cashflow(pro_small_uid,{'days':30,'amount':'1e100'})
    assert out.get('error') or out['projetado']==-1e100

def test_tester_codigo_novo_respeita_gate_de_350():
    import test_max_lines_python as gate
    name='core/services/cashflow_snapshot.py'
    contents=(Path(__file__).resolve().parents[1]/name).read_bytes()
    assert not gate._estoura(name,contents),f'{name}: {gate._linhas(contents)} linhas'

def test_tester_isolamento_join_recorrente_com_fk_cruzada(user_id):
    from conftest import usuario_pagante
    from db.bills import ensure_bill_instance
    other=usuario_pagante();due=date.today()+timedelta(days=1)
    r=create_recurring_expense(other,'SEGREDO OUTRO DONO',999,'outros',due.day,'account',frequency='once',start_date=due)
    b=create_boleto(user_id,'Nome nosso',10,due)
    q('update bill_instances set recurring_id=%s,name=null where id=%s and user_id=%s',(r['id'],b['id'],user_id))
    s=snapshot(user_id)
    assert all(e.nome!='SEGREDO OUTRO DONO' for e in s.ocorrencias)
    assert sum(e.assinado for e in s.ocorrencias)==-10

def test_tester_falha_dependencia_sem_fallback_saldo_zero(user_id,monkeypatch):
    from db import contas_hoje
    def fail(*a,**kw):raise psycopg.OperationalError('DEPENDENCIA TESTER')
    monkeypatch.setattr(contas_hoje,'listar',fail)
    with pytest.raises(psycopg.OperationalError,match='DEPENDENCIA TESTER'):
        project(user_id,date.today()+timedelta(days=30))

def test_tester_extra_finito_grande_http_sem_erro_500(pro_small_uid):
    from tests.test_manual_launches_carteira_piggy import _dashboard_client
    from frontend import finance_bot_websocket_custom as routes
    client,headers=_dashboard_client(pro_small_uid,f'tester-{pro_small_uid}@example.invalid')
    client.raise_server_exceptions=False
    response=client.get(f'/recurring-bills/{pro_small_uid}/projection',params={'date':str(date.today()+timedelta(days=30)),'amount':'1e100'},headers=headers)
    assert response.status_code in (200,400,422),(response.status_code,response.text)

def test_tester_extra_assinado_legitimo_e_meio_centavo(pro_small_uid):
    from core.services.ai_chat.tools.bills import _check_cashflow
    from core.services.cashflow_snapshot import centavos
    out=_check_cashflow(pro_small_uid,{'days':30,'amount':-10})
    assert out['projetado']==10 and out['boleto_novo']==-10
    assert centavos(D('2.685'))==D('2.68')
    assert centavos(D('-0.004'))==0 and not centavos(D('-0.004')).is_signed()

def test_tester_pendencias_frontier_expirada_ia_e_categoria(user_id):
    from core.services.cashflow_snapshot import ler
    from db.ai_chat import PENDING_TTL_MINUTES
    now=datetime.now(timezone.utc)
    db.set_pending_action(user_id,'payment_method_choice',{'valor':20})
    boundary=now+timedelta(seconds=40)
    q('update pending_actions set expires_at=%s where user_id=%s',(boundary,user_id))
    with db.get_conn() as conn,conn.cursor() as cur:
        cur.execute('set transaction isolation level repeatable read,read only')
        s=ler(cur,user_id,date.today(),date.today()+timedelta(days=30),now,False)
        assert s.valido_ate==boundary
        later=ler(cur,user_id,date.today(),date.today()+timedelta(days=30),boundary+timedelta(seconds=1),False)
        assert not any(m.codigo=='acao_financeira_pendente' for m in later.motivos)
        cur.execute('select count(*) n from pending_actions where user_id=%s',(user_id,))
        assert cur.fetchone()['n']==1
        conn.rollback()

def test_tester_recorrente_desconhecida_dentro_janela_fica_visivel(user_id):
    due=date.today()+timedelta(days=1)
    r=create_recurring_expense(user_id,'Dentro',10,'outros',due.day,'account',frequency='once',start_date=due)
    q('update recurring_expenses set frequency=%s where id=%s and user_id=%s',('legada',r['id'],user_id))
    s=snapshot(user_id)
    assert s.ocorrencias[0].data is None
    assert any(m.codigo=='calendario_recorrente_desconhecido' for m in s.motivos)

def test_tester_realizacao_nao_aceita_efeito_de_banco_com_origem_carteira(user_id):
    from psycopg.types.json import Jsonb
    db.add_launch_and_update_balance(user_id,'receita',100,'Dinheiro',None)
    due=date.today()+timedelta(days=2)
    b=create_boleto(user_id,'Legado incoerente',20,due)
    mark_bill_paid(user_id,b['id'],20,metodo='carteira')
    lid=q('select launch_id from bill_instances where id=%s and user_id=%s',(b['id'],user_id))[0]['launch_id']
    q('update launches set efeitos=%s where id=%s and user_id=%s',(Jsonb({'delta_conta':-20,'funding_source':{'kind':'bank'}}),lid,user_id))
    s=snapshot(user_id)
    e=next(e for e in s.ocorrencias if e.fonte=='instancia')
    assert e.realizacao=='a_conferir',e

def test_tester_isolamento_todas_fontes_financeiras_a_b(user_id):
    from conftest import usuario_pagante
    from db.recurring_income import create_recurring_income
    from tests.test_manual_launches_carteira_piggy import _connect_fake_bank
    other=usuario_pagante();due=date.today()+timedelta(days=1)
    db.add_launch_and_update_balance(user_id,'receita',50,'Nosso dinheiro',None)
    db.add_launch_and_update_balance(other,'receita',900,'SEGREDO saldo',None)
    create_recurring_income(other,'SEGREDO renda',999,'outros',due.day,start_date=date.today())
    create_recurring_expense(other,'SEGREDO fixo',888,'outros',due.day,'account',frequency='once',start_date=due)
    create_boleto(other,'SEGREDO boleto',777,due)
    card=db.create_card(other,'SEGREDO cartao',10,17)
    db.add_credit_purchase(other,card,666,'outros','SEGREDO compra',date.today())
    _connect_fake_bank(other,'SEGREDO banco')
    db.set_pending_action(other,'payment_method_choice',{'valor':555})
    s=snapshot(user_id,90)
    assert s.base['saldo']==D(50) and s.base['of_bank_count']==0
    assert not s.ocorrencias and not any(m.codigo=='acao_financeira_pendente' for m in s.motivos)
