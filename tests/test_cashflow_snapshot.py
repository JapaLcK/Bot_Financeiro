"""Motor único: PostgreSQL real, snapshot/qualidade/isolamento/conversa; sem mocks de dinheiro."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import psycopg
import pytest

import db
from core.services.cashflow import project, _risco
from core.services.cashflow_forecast import forecast_with_trajectory
from core.services.cashflow_snapshot import carregar, ler, Snapshot, Motivo, Ocorrencia
from core.services.decision_simulator import Simulacao, simulate
from db import contas_hoje, patrimonio
from db.bills import create_boleto, mark_bill_paid
from _fusao_of_helpers import ia_fora

def create_bill(uid, name, due, amount):
    return create_boleto(uid, name, amount, due)
from db.recurring import create_recurring_expense
from conftest import usuario_pagante

D = Decimal


def q(sql, params=()):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall() if cur.description else []
        conn.commit()
        return rows


def snapshot(uid, dias=30):
    return carregar(uid, date.today(), date.today()+timedelta(days=dias))


def test_carteira_nova_especie_residual_e_flags_compartilhadas(user_id):
    assert 'carteira_nao_confirmada' not in {m.codigo for m in snapshot(user_id).motivos}
    db.add_launch_and_update_balance(user_id, 'receita', 25, 'Dinheiro físico', None)
    assert snapshot(user_id).base['saldo'] == D(25)
    assert 'carteira_nao_confirmada' not in {m.codigo for m in snapshot(user_id).motivos}
    db.set_balance(user_id, D(30))  # residual não é fabricação de saldo confiável.
    with db.get_conn() as conn, conn.cursor() as cur:
        a, p = contas_hoje.listar(cur,user_id), patrimonio.calcular(cur,user_id)
    assert 'carteira_nao_confirmada' in a['carteira']['motivos'] and 'carteira_nao_confirmada' in p['motivos']
    assert snapshot(user_id).base['saldo'] == D(30)
    assert 'carteira_nao_confirmada' in {m.codigo for m in snapshot(user_id).motivos}


@pytest.mark.parametrize('efeitos', [{}, {'delta_conta':'NaN'}, {'delta_conta':None}, []])
def test_efeito_legado_invalido_preserva_saldo_e_motivo(user_id, efeitos):
    from psycopg.types.json import Jsonb
    lid,*_=db.add_launch_and_update_balance(user_id,'receita',10,'Legado',None)
    q('update launches set efeitos=%s where id=%s and user_id=%s',(Jsonb(efeitos),lid,user_id))
    s=snapshot(user_id)
    assert s.base['saldo']==D(10)
    assert 'carteira_nao_confirmada' in {m.codigo for m in s.motivos}


def test_manual_sem_instancia_futuro_e_instancia_mesmo_ciclo_uma_vez(user_id):
    due=date.today()+timedelta(days=2)
    rec=create_recurring_expense(user_id,'Manual',10,'outros',due.day,'account',
                                 frequency='once',start_date=due,payment_mode='manual')
    before=snapshot(user_id)
    assert [(e.nome,e.valor) for e in before.ocorrencias]==[('Manual',D(10))]
    from db.bills import ensure_bill_instance
    ensure_bill_instance(rec['id'],user_id,due,12)
    after=snapshot(user_id)
    assert [(e.nome,e.valor) for e in after.ocorrencias]==[('Manual',D(12))]
    assert project(user_id,due)['projetado']==-12


def test_boleto_negativo_variavel_zero_e_calendario_invalido_nao_viram_zero_exato(user_id):
    from db.bills import ensure_bill_instance
    due=date.today()+timedelta(days=1)
    rec=create_recurring_expense(user_id,'Variável',0,'outros',due.day,'account',
                                 frequency='once',start_date=due,variable_amount=True)
    s=snapshot(user_id)
    assert s.ocorrencias[0].valor is None and s.ocorrencias[0].qualidade_valor=='desconhecido'
    q('update recurring_expenses set frequency=%s where id=%s and user_id=%s',('estranha',rec['id'],user_id))
    s=snapshot(user_id)
    assert s.ocorrencias[0].data is None
    bid=create_bill(user_id,'Avulso',due,1)['id']
    q('update bill_instances set amount=-2 where id=%s and user_id=%s',(bid,user_id))
    s=snapshot(user_id)
    b=next(e for e in s.ocorrencias if e.fonte=='instancia')
    assert b.valor is None and b.assinado==0
    assert project(user_id,due)['projetado']==0


def test_cartao_recorrente_nao_sai_duas_vezes_e_ausencia_nao_e_fatura_zero(user_id):
    from core.services.ai_chat.tools.cards import _forecast_next_bill
    card=db.create_card(user_id,'Cartão',date.today().day,date.today().day)
    due=date.today()+timedelta(days=1)
    create_recurring_expense(user_id,'Assinatura',30,'outros',due.day,'credit_card',card_id=card,
                             frequency='once',start_date=due)
    assert _forecast_next_bill(user_id,{})['total'] is None
    s=snapshot(user_id)
    fixed=next(e for e in s.ocorrencias if e.fonte=='gasto_recorrente')
    assert fixed.valor==D(30) and not fixed.incluida
    assert project(user_id,due)['gastos_fixos_previstos']==0
    assert 'incorporacao_cartao_nao_comprovada' in {m.codigo for m in s.motivos}
    db.add_credit_purchase(user_id,card,30,'outros','Assinatura',date.today())
    out=project(user_id,date.today()+timedelta(days=90))
    assert out['gastos_fixos_previstos']==0 and out['faturas_cartao']==30 and out['projetado']==-30


def test_pagamento_carteira_com_vinculo_nao_debita_de_novo(user_id):
    db.add_launch_and_update_balance(user_id,'receita',100,'Dinheiro',None)
    due=date.today()+timedelta(days=3)
    b=create_bill(user_id,'Antecipado',due,20)
    mark_bill_paid(user_id,b['id'],20,metodo='carteira')
    s=snapshot(user_id)
    assert s.base['saldo']==D(80)
    assert s.ocorrencias[0].realizacao=='realizada'
    assert project(user_id,due)['projetado']==80


def test_snapshot_repeatable_read_intercalada_com_pagamento_e_sem_escrita(user_id,monkeypatch):
    db.add_launch_and_update_balance(user_id,'receita',100,'Dinheiro',None)
    due=date.today()+timedelta(days=1)
    b=create_bill(user_id,'Ainda pendente',due,20)
    original=contas_hoje.listar
    def interleave(cur,uid,**kwargs):
        data=original(cur,uid,**kwargs)
        mark_bill_paid(uid,b['id'],20,metodo='carteira')
        return data
    monkeypatch.setattr(contas_hoje,'listar',interleave)
    s=snapshot(user_id)
    assert s.base['saldo']==D(100)
    assert next(e for e in s.ocorrencias if e.fonte=='instancia').valor==D(20)
    assert next(e for e in s.ocorrencias if e.fonte=='instancia').realizacao=='a_conferir'
    assert project(user_id,due)['projetado']==80  # snapshot posterior inteira: saldo debitado + vínculo realizado.
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute('set transaction isolation level repeatable read, read only')
        ler(cur,user_id,date.today(),due,datetime.now(timezone.utc),False)
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            cur.execute('update accounts set balance=0 where user_id=%s',(user_id,))
        conn.rollback()


def test_snapshot_isola_usuario_fonte_instancia_e_nao_repara_fatura_ou_ttl(user_id):
    other=usuario_pagante()
    due=date.today()+timedelta(days=1)
    create_bill(other,'SEGREDO',due,900)
    db.set_pending_action(user_id,'bill_amount_expected',{'bill_id':1},minutes=-1)
    before=q('select count(*) n from pending_actions where user_id=%s',(user_id,))[0]['n']
    assert not snapshot(user_id).ocorrencias
    assert q('select count(*) n from pending_actions where user_id=%s',(user_id,))[0]['n']==before


def test_direcao_item_a_item_nao_neta_duvidas_e_preserva_risco_legitimo():
    today=date.today()
    s=Snapshot(today,datetime.now(timezone.utc),{'saldo':D(-50),'balance_source':'manual',
              'of_bank_count':0,'banks_excluded':False},motivos=[Motivo('gastos_variaveis_nao_estimados','so_melhora')])
    assert _risco(s,today+timedelta(days=1))=='risco'
    e=Ocorrencia('instancia',1,today.isoformat(),today,'boleto','Pago talvez',D(100),'saida',realizacao='a_conferir')
    s.base['saldo']=D(50);s.ocorrencias=[e];s.motivos=[]
    assert _risco(s,today)=='abster'
    s.motivos=[Motivo('entrada_desconhecida','so_piora')]
    s.base['saldo']=D(-50)
    assert _risco(s,today)=='abster'


@pytest.mark.parametrize('prefixo',['','piggy '])
@pytest.mark.parametrize('tipo',['bill_amount_expected','payment_method_choice','confirm_media_launch'])
def test_handle_incoming_previsao_preserva_pendencia_financeira_outro_assunto(prefixo,tipo,monkeypatch,ia_fora):
    _conversa_previsao_preserva_pendencia(prefixo,tipo,'previsão de saldo daqui 30 dias',monkeypatch,ia_fora)


@pytest.mark.parametrize('prefixo',['','piggy '])
@pytest.mark.parametrize('tipo',['bill_amount_expected','payment_method_choice','confirm_media_launch'])
@pytest.mark.parametrize('consulta', [
    'qual o saldo daqui 30 dias considerando uma saída de R$ 800?',
    'qual o saldo daqui 30 dias considerando uma saída de valor desconhecido?',
])
def test_handle_incoming_cenario_preserva_pendencia_financeira_outro_assunto(prefixo,tipo,consulta,monkeypatch,ia_fora):
    _conversa_previsao_preserva_pendencia(prefixo,tipo,consulta,monkeypatch,ia_fora)


def _conversa_previsao_preserva_pendencia(prefixo,tipo,consulta,monkeypatch,ia_fora,recusada=False):
    from core.handle_incoming import handle_incoming
    from core.types import IncomingMessage
    uid=usuario_pagante()
    # Cada primeira mensagem abre a pendência pelo caminho real; só OCR/LLM são mockados.
    def diga(text, attachments=()):
        return handle_incoming(IncomingMessage(platform='whatsapp',user_id=uid,text=text,
               message_id='snapshot',attachments=list(attachments),external_id='',raw={}))
    if tipo == 'bill_amount_expected':
        due=date.today()+timedelta(days=1)
        rec=create_recurring_expense(uid,'Luz',0,'moradia',due.day,'account',variable_amount=True,payment_mode='manual')
        from db.bills import ensure_bill_instance
        ensure_bill_instance(rec['id'],uid,due,0)
        assert diga('paguei a luz')
    elif tipo == 'payment_method_choice':
        from tests.test_manual_launches_carteira_piggy import _connect_fake_bank
        _connect_fake_bank(uid)
        assert diga('gastei 25 no mercado')
    else:
        import core.handle_incoming as hi
        from core.types import Attachment
        monkeypatch.setattr(hi,'analyze_image',lambda *args: {'tem_dado_financeiro':True,
                'tipo':'despesa','valor':25,'alvo':'Mercado','categoria':'outros','data':None})
        assert diga('', [Attachment('cupom.jpg','image/jpeg',b'imagem de teste')])
    assert db.get_pending_action(uid)['action_type'] == tipo
    before=db.get_pending_action(uid)
    count=q('select count(*) n from launches where user_id=%s',(uid,))[0]['n']
    result=diga(prefixo+consulta)
    after=db.get_pending_action(uid)
    assert result
    if recusada or 'desconhecido' in consulta:
        assert 'cenário' in result[0].text.lower() and 'saldo previsto' not in result[0].text.lower()
    else:
        assert 'condicional' in result[0].text.lower()
        if '800' in consulta:
            assert 'saída' in result[0].text.lower() and 'R$ 800,00' in result[0].text
    assert after['action_type']==before['action_type'] and after['payload']==before['payload'] and after['created_at']==before['created_at']
    assert q('select count(*) n from launches where user_id=%s',(uid,))[0]['n']==count
    assert 'acao_financeira_pendente' in {m.codigo for m in snapshot(uid).motivos}


def test_mesmos_centavos_previsao_simulador_tool_e_final_positivo_com_queda(pro_small_uid):
    from core.services.ai_chat.tools.bills import _check_cashflow
    from db.recurring_income import create_recurring_income
    uid=pro_small_uid
    db.add_launch_and_update_balance(uid,'receita',100,'Dinheiro',None)
    due=date.today()+timedelta(days=1)
    create_bill(uid,'Saída',due,200)
    salary=date.today()+timedelta(days=2)
    create_recurring_income(uid,'Renda prevista',500,'salário',salary.day,start_date=date.today())
    fc=forecast_with_trajectory(uid)
    p=project(uid,date.today()+timedelta(days=90),percurso=True)
    sim=simulate(uid,Simulacao(cenarios=[{'nome':'Hipótese','preco':1}]))
    assert p['projetado']==fc['horizons']['90']['projetado']==sim['atual']['saldo_final_90']
    assert p['tranquilo'] and p['minimo_percurso']==-100 and p['cabe_nas_premissas'] is False
    tool=_check_cashflow(uid,{'days':30})
    assert 'não garante' in tool['note'] and tool['orientacao']=='abster'


def test_previsao_assunto_novo_desfazer_e_nova_previsao(pro_small_uid,ia_fora):
    from _fusao_of_helpers import manda
    uid=pro_small_uid
    assert 'condicional' in manda(uid,'previsão de saldo daqui 30 dias')
    assert 'IA-NAO' not in manda(uid,'recebi 100 de salário')
    assert project(uid,date.today()+timedelta(days=30))['projetado']==100
    assert 'condicional' in manda(uid,'piggy previsão de saldo daqui 30 dias')
    assert 'Confirma' in manda(uid,'desfazer')
    assert 'condicional' in manda(uid,'previsão de saldo daqui 30 dias')
    assert db.get_pending_action(uid)['action_type']=='delete_launch'
    assert 'IA-NAO' not in manda(uid,'sim')
    assert project(uid,date.today()+timedelta(days=30))['projetado']==0
    assert 'condicional' in manda(uid,'previsão de saldo daqui 30 dias')
    assert not ia_fora


@pytest.mark.parametrize('raw,period,expected,quality',[
    ({'creditData':{'balanceCloseDate':'2026-10-10','balanceDueDate':'2026-10-19'}},date(2026,10,10),date(2026,10,19),'conhecida'),
    ({'creditData':{'balanceCloseDate':'2026-10-10','balanceDueDate':'2026-10-19'}},date(2026,11,10),date(2026,11,17),'presumida'),
    ({},date(2026,10,10),date(2026,10,17),'presumida')])
def test_calendario_of_do_ciclo_nao_se_aplica_a_outro(raw,period,expected,quality):
    from db.cards import calendario_fatura
    assert calendario_fatura({'account_raw':raw,'period_end':period,'closing_day':10,'due_day':17})==(expected,quality)


def test_pendencia_financeira_avisa_sse_no_commit_e_categoria_nao(user_id,monkeypatch):
    import asyncio
    from tests.test_api_v2_eventos_escrita import ouvindo,barreira
    from db.pending import avisar_mudanca_financeira
    async def cena():
        async with ouvindo(monkeypatch) as avisados:
            await asyncio.to_thread(db.set_pending_action,user_id,'recategorize_launch_text',{'launch_id':1})
            await barreira(avisados)
            assert user_id not in avisados
            await asyncio.to_thread(db.set_pending_action,user_id,'payment_method_choice',{'valor':20})
            await barreira(avisados)
            assert avisados.count(user_id)==1
            await asyncio.to_thread(db.clear_pending_action,user_id)
            await barreira(avisados)
            assert avisados.count(user_id)==2
            with db.get_conn() as conn,conn.cursor() as cur:
                avisar_mudanca_financeira(cur,user_id,None,('bill_amount_expected',{}))
                conn.rollback()
            await barreira(avisados)
            assert avisados.count(user_id)==2
            await asyncio.to_thread(db.ai_set_pending_action,user_id,'add_launch',{'valor':15},'registrar')
            await barreira(avisados)
            assert avisados.count(user_id)==3
            await asyncio.to_thread(db.ai_clear_pending_action,user_id)
            await barreira(avisados)
            assert avisados.count(user_id)==4
    asyncio.run(cena())


@pytest.mark.parametrize('amount',['NaN','Infinity','abc',True])
def test_extra_ia_http_compartilha_recusa_de_invalido(pro_small_uid,monkeypatch,amount):
    import asyncio
    from fastapi import HTTPException
    from core.services.ai_chat.tools.bills import _check_cashflow
    import frontend.finance_bot_websocket_custom as routes
    uid=pro_small_uid
    assert _check_cashflow(uid,{'days':30,'amount':amount})['error']=='invalid_args'
    monkeypatch.setattr(routes,'_authorize_dashboard_access',lambda *args:None)
    due=date.today()+timedelta(days=2)
    rec=create_recurring_expense(uid,'Conta',10,'outros',due.day,'account',frequency='once',start_date=due)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.boleto_projection_route(request=None,user_id=uid,date=str(due),amount=amount))
    assert exc.value.status_code==400


def test_referencia_q18_congelada_por_cenario_e_centavo(monkeypatch):
    import json
    from pathlib import Path
    import core.services.cashflow as cf
    from _cashflow_helpers import _mock_sources
    reference=json.loads((Path(__file__).parent/'fixtures/cashflow_etapa3_dc1d000.json').read_text())
    assert reference['sha_base']=='dc1d0009075f3688dc4ea80ca02da80c7f073a72'
    today=date.fromisoformat(reference['hoje']);due=date.fromisoformat(reference['alvo'])
    class Hoje(date):
        @classmethod
        def today(cls): return today
    monkeypatch.setattr(cf,'date',Hoje)
    for case in reference['casos']:
        name=case['nome'];sources={}
        if name=='centavos_0_3_menos_0_1_0_2':
            sources['bills']=[{'due_date':due,'amount':v,'name':str(v),'status':'pending'} for v in (.1,.2)]
        elif name=='manual_futuro_sem_instancia':
            sources['expenses']=[{'name':'Manual','is_active':True,'amount':10,'frequency':'once','start_date':due,'due_day':due.day,'payment_mode':'manual'}]
        elif name=='cartao_recorrente_e_fatura':
            sources['expenses']=[{'name':'Assinatura','is_active':True,'amount':30,'frequency':'once','start_date':due,'due_day':due.day,'payment_type':'credit_card'}]
            sources['card_bills']=[{'due_date':due,'remaining':30,'card_name':'Cartão'}]
        elif name=='boleto_negativo_desconhecido':
            sources['bills']=[{'due_date':due,'amount':-10,'name':'Legado','status':'pending'}]
        elif name=='obrigacao_hoje':
            sources['expenses']=[{'name':'Hoje','is_active':True,'amount':80,'frequency':'once','start_date':today,'due_day':today.day}]
        _mock_sources(monkeypatch,saldo=case['saldo'],**sources)
        out=cf.project(1,due,case['extra'])
        assert D(str(out['projetado']))==D(str(case['novo'])),name
        delta=D(str(case['novo']))-D(str(case['referencia']))
        expected={'paridade':D(0),'manual_futuro':D(-10),'deduplicacao_cartao':D(30),
                  'desconhecido_nao_receita':D(-10),'hoje_sob_hipotese_nao_realizada':D(-80),
                  'precisao_HALF_EVEN_decimal':D('.01')}[case['mudanca']]
        assert delta==expected,name


def test_carteira_efeito_finito_contraditorio_nao_confirma_composicao(user_id):
    from psycopg.types.json import Jsonb
    lid,*_=db.add_launch_and_update_balance(user_id,'receita',10,'Legado',None)
    q('update launches set efeitos=%s where id=%s and user_id=%s',(Jsonb({'delta_conta':-10}),lid,user_id))
    db.set_balance(user_id,D(-10))
    assert snapshot(user_id).base['saldo']==D(-10)
    assert 'carteira_nao_confirmada' in {m.codigo for m in snapshot(user_id).motivos}


def test_instancia_do_ciclo_substitui_valor_desconhecido_e_futuro_nao_contamina(user_id):
    from db.bills import ensure_bill_instance
    due=date.today()+timedelta(days=1)
    rec=create_recurring_expense(user_id,'Valor ciclo',0,'outros',due.day,'account',
           frequency='once',start_date=due,variable_amount=True,payment_mode='manual')
    ensure_bill_instance(rec['id'],user_id,due,12)
    s=snapshot(user_id)
    assert len(s.ocorrencias)==1 and s.ocorrencias[0].valor==D(12)
    assert 'valor_recorrente_desconhecido' not in {m.codigo for m in s.motivos}
    assert 'valor_boleto_estimado' in {m.codigo for m in s.motivos}
    future=due+timedelta(days=40)
    create_recurring_expense(user_id,'Fora',0,'outros',future.day,'account',frequency='once',start_date=future,variable_amount=True)
    s=snapshot(user_id)
    assert 'valor_recorrente_desconhecido' not in {m.codigo for m in s.motivos}


@pytest.mark.parametrize('total,paid,status,expected',[
    (-10,0,'open',None),(100,-5,'open',None),(100,110,'paid',None),
    (100,30,'open',D(70)),(100,0,'paid',D(100))])
def test_fatura_parcial_negativa_ou_paid_com_divida_sem_reparo(user_id,total,paid,status,expected):
    from core.services.ai_chat.tools.cards import _forecast_next_bill
    card=db.create_card(user_id,'Legado fatura',10,17)
    db.add_credit_purchase(user_id,card,100,'outros','Compra',date.today())
    bid=q('select id from credit_bills where user_id=%s and card_id=%s',(user_id,card))[0]['id']
    q('update credit_bills set total=%s,paid_amount=%s,status=%s where user_id=%s and id=%s',(total,paid,status,user_id,bid))
    before=q('select total,paid_amount,status from credit_bills where user_id=%s and id=%s',(user_id,bid))
    s=snapshot(user_id,90)
    e=next(e for e in s.ocorrencias if e.fonte=='fatura')
    assert e.valor==expected and e.realizacao=='a_conferir'
    assert e.qualidade_data=='presumida'
    if expected is None: assert 'valor_fatura_a_conferir' in {m.codigo for m in e.motivos}
    _forecast_next_bill(user_id,{})
    assert q('select total,paid_amount,status from credit_bills where user_id=%s and id=%s',(user_id,bid))==before


def test_base_nan_nao_gera_curva_zero_e_homonimos_sao_distintos(user_id):
    due=date.today()+timedelta(days=1)
    create_bill(user_id,'Mesmo nome',due,10)
    create_bill(user_id,'Mesmo nome',due,20)
    s=snapshot(user_id)
    assert len({e.chave for e in s.ocorrencias})==2
    assert project(user_id,due)['boletos_ate']==30
    q("update accounts set balance='NaN'::numeric where user_id=%s",(user_id,))
    out=forecast_with_trajectory(user_id)
    assert out['estado']=='indisponivel'
    assert out['horizons']['30']['projetado'] is None
    assert all(p['saldo_projetado'] is None for p in out['trajectory']) and out['worst_day'] is None


def test_risco_real_so_com_saida_omitida_nao_e_bloqueado(pro_small_uid):
    uid=pro_small_uid
    db.add_launch_and_update_balance(uid,'despesa',50,'Dinheiro',None)
    out=project(uid,date.today()+timedelta(days=1))
    assert out['orientacao']=='risco' and out['projetado']==-50 and out['cabe_nas_premissas'] is False


def test_previsao_historica_ia_http_preserva_envelope_sem_reconstruir(pro_small_uid,monkeypatch):
    import asyncio
    from core.services.ai_chat.tools.bills import _check_cashflow
    import frontend.finance_bot_websocket_custom as routes
    uid=pro_small_uid;past=date.today()-timedelta(days=1)
    db.add_launch_and_update_balance(uid,'receita',100,'Dinheiro',None)
    monkeypatch.setattr(routes,'_authorize_dashboard_access',lambda *args:None)
    tool=_check_cashflow(uid,{'date':str(past)})
    response=asyncio.run(routes.boleto_projection_route(request=None,user_id=uid,date=str(past),amount=None))
    for out in (tool,response['projection']):
        assert out['estado']=='indisponivel' and out['projetado'] is None and not out['tranquilo']
        assert 'previsao_historica_indisponivel' in {m['codigo'] for m in out['motivos']}


@pytest.mark.parametrize('tool',['set_budget','delete_budget','recategorize_launch','create_category_rule',
    'delete_category_rule','enable_daily_report','disable_daily_report','set_daily_report_hour','open_dashboard','report_out_of_scope'])
def test_escritores_ia_sem_impacto_em_caixa_nao_contaminam_snapshot(user_id,tool):
    db.ai_set_pending_action(user_id,tool,{'valor':10},'confirme preferência')
    assert 'acao_financeira_pendente' not in {m.codigo for m in snapshot(user_id).motivos}


@pytest.mark.parametrize('intent',['categories.create','report.enable','help','launches.list'])
def test_clarification_conversa_categoria_nao_e_pendencia_de_dinheiro(user_id,intent):
    db.set_pending_action(user_id,'clarification',{'intent':intent,'question':'Qual?'})
    assert 'acao_financeira_pendente' not in {m.codigo for m in snapshot(user_id).motivos}


def test_linha_carteira_ausente_com_fonte_financeira_nao_inventa_base_zero(user_id):
    due=date.today()+timedelta(days=1)
    create_bill(user_id,'Histórico financeiro',due,10)
    q('delete from accounts where user_id=%s',(user_id,))
    s=snapshot(user_id)
    assert s.base['saldo'] is None and s.qualidade()['estado']=='indisponivel'
    assert 'carteira_nao_confirmada' in {m.codigo for m in s.motivos}
    assert project(user_id,due)['projetado'] is None
    assert q('select count(*) n from accounts where user_id=%s',(user_id,))[0]['n']==0
