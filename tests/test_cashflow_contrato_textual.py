"""Contrato textual de previsão pelo core e pelo adapter reais, com PostgreSQL."""
from datetime import date, timedelta

import pytest
import db
from tests._fusao_of_helpers import manda, ia_fora
from tests.conftest import usuario_pagante
from tests.test_cashflow_snapshot import q, _conversa_previsao_preserva_pendencia
from tests.test_bill_amount_pending import _monta_conta_variavel, _toca_ja_paguei, _manda_texto_no_wa
from core.services.ai_chat.tools.bills import _check_cashflow, _forecast_balance
from utils_text import fmt_brl


@pytest.fixture
def chamadas(monkeypatch):
    """Espia sem substituir execução financeira: tools e motor continuam reais."""
    import core.services.ai_chat.tools.bills as tools
    calls = []
    for nome in ('_check_cashflow', '_forecast_balance'):
        original = getattr(tools, nome)
        def executa(uid, args, original=original, nome=nome):
            calls.append((nome, dict(args)))
            return original(uid, args)
        monkeypatch.setattr(tools, nome, executa)
    return calls


def estado(uid):
    """Dinheiro e perguntas completos; activity do handler não é snapshot financeira."""
    from core.services.ai_chat_commands import pergunta_aberta_da_ia
    return {
        'saldo': q('select balance from accounts where user_id=%s', (uid,)),
        'bills': q('select * from bill_instances where user_id=%s order by id', (uid,)),
        'launches': q('select * from launches where user_id=%s order by id', (uid,)),
        'pending': db.get_pending_action(uid),
        'ai_pending': db.ai_get_pending_action(uid),
        'pergunta': pergunta_aberta_da_ia(uid),
        'history': q('select * from ai_messages where user_id=%s order by id', (uid,)),
    }


VALIDAS = [
    ('tô tranquilo até 17/10 com meus boletos?', {'date': '17/10'}),
    ('previsão de saldo de 30 dias', {'days': 30}),
    ('tô tranquilo até dia 17?', {'date': '17'}),
    ('tô tranquilo até dia 16/08?', {'date': '16/08'}),
    ('como tô de prazo no dia 17 com os boletos?', {'date': '17'}),
    ('como tô de boletos no dia 17?', {'date': '17'}),
    ('dá pra pegar um boleto de 800 pra dia 20?', {'date': '20', 'amount': 800}),
    ('previsão de saldo em 30 dias com minhas contas a pagar?', {'days': 30}),
    ('previsão de saldo em 30 dias com as contas a pagar?', {'days': 30}),
    ('como estou de boletos no dia 17?', {'date': '17'}),
    ('como tô de contas a pagar no dia 17?', {'date': '17'}),
    ('aguento pagar até dia 17?', {'date': '17'}),
    ('consigo pagar até dia 17?', {'date': '17'}),
    ('previsão de saldo para 17/10.', {'date': '17/10'}),
    ('tô tranquilo até dia 17.', {'date': '17'}),
    ('previsão de saldo para 17-10?', {'date': '17-10'}),
    ('previsão de saldo para 17/10/26?', {'date': '17/10/26'}),
    ('previsão de saldo para 17-10-2026?', {'date': '17-10-2026'}),
    ('previsão de saldo para 2026-10-17?', {'date': '2026-10-17'}),
    ('previsão de saldo em 30 dias considerando uma entrada de R$ 800?', {'days': 30, 'amount': -800}),
    ('previsão de saldo em 30 dias com meus boletos considerando uma saída de 800?', {'days': 30, 'amount': 800}),
    ('previsão de saldo em 30 dias considerando uma receita de 800?', {'days': 30, 'amount': -800}),
    ('previsão de saldo em 30 dias com um novo boleto de 800?', {'days': 30, 'amount': 800}),
    ('dá pra aceitar um boleto de 800 para dia 20?', {'date': '20', 'amount': 800}),
    ('qual meu saldo daqui 30 dias e o que é bitcoin?', {'days': 30}),
    ('PREVISÃO de SALDO de\n30\nDIAS', {'days': 30}),
    ('previsão de saldo nos próximos 30 dias', {'days': 30}),
    ('piggy previsão de saldo nos próximos 30 dias', {'days': 30}),
    ('previsão de saldo próximos 30 dias', {'days': 30}),
    ('PREVISAO de SALDO nos PROXIMOS\n30\nDIAS', {'days': 30}),
    ('previsão de saldo no próximo 1 dia', {'days': 1}),
    ('previsão de saldo próximo 1 dia', {'days': 1}),
    ('previsão de saldo para os próximos 30 dias', {'days': 30}),
    ('previsão de saldo pelos próximos 30 dias', {'days': 30}),
    ('tô tranquilo nos próximos 30 dias?', {'days': 30}),
    ('previsão de saldo nos próximos 30 dias com meus boletos?', {'days': 30}),
    ('previsão de saldo nos próximos 30 dias considerando uma saída de 800?', {'days': 30, 'amount': 800}),
    ('previsão de saldo nos próximos 30 dias considerando uma entrada de 800?', {'days': 30, 'amount': -800}),
]


@pytest.mark.parametrize('texto,args', VALIDAS)
def test_contrato_consulta_tool_real_sem_recontar_existing(pro_small_uid, ia_fora, chamadas, monkeypatch, texto, args):
    uid = pro_small_uid
    assert 'registrada' in manda(uid, 'recebi 1500 de salário em dinheiro').lower()
    from db.bills import create_boleto
    create_boleto(uid, 'Existente', 100, date.today()+timedelta(days=1))
    antes = estado(uid)
    monkeypatch.setattr('core.handle_incoming.classify', lambda *_a, **_k: pytest.fail('consulta chegou ao classificador'))
    monkeypatch.setattr('core.handle_incoming.route', lambda *_a, **_k: pytest.fail('consulta chegou ao resolver'))
    resposta = manda(uid, texto)
    esperado = _check_cashflow(uid, args)
    assert chamadas == [('_check_cashflow', args)], (resposta, chamadas)
    if esperado.get('error'):
        assert resposta == (esperado.get('message') or esperado['error'])
    elif esperado['estado'] == 'indisponivel':
        assert 'indisponível' in resposta
    else:
        assert 'Saldo previsto condicional' in resposta and fmt_brl(esperado['projetado']) in resposta
        assert esperado['target'] in resposta
    assert estado(uid) == antes and not ia_fora


RECUSAS = [
    'aguento esse prazo?', 'aguento o prazo?', 'tranquilo nesse prazo?',
    'previsão de saldo dia 0?', 'previsão de saldo dia 32?', 'previsão de saldo em -30 dias?',
    'previsão de saldo em 3.0 dias?', 'previsão de saldo em 30 ou em 60 dias?',
    'previsão de saldo em 99999999999999999999999999999999999999999999 dias?',
    'previsão de saldo em 30 dias ou para 2026-10-17?',
    'previsão de saldo para dia 17 ou 20?', 'previsão de saldo para 31/02?',
    'previsão de saldo amanhã?',
    'previsão de saldo para 2026-02-31?', 'previsão de saldo para 17/10/266?',
    'previsão de saldo para 2026-10-17-20?', 'previsão de saldo para 17/10 ou 17/10?',
    'previsão de saldo em 30 dias com um boleto de 800?',
    'previsão de saldo em 30 dias sem meus boletos?',
    'previsão de saldo em 30 dias ignorando minhas contas a pagar?',
    'previsão de saldo em 30 dias com um novo boleto?',
    'previsão de saldo com uma saída de 800?',
    'previsão de saldo em 30 dias considerando uma saída de -800?',
    'previsão de saldo em 30 dias considerando uma saída de 0?',
    'previsão de saldo em 30 dias considerando uma saída de infinito?',
    'previsão de saldo em 30 dias considerando uma saída de 1.2.3?',
    'previsão de saldo em 30 dias considerando uma saída de 800 900?',
    'previsão de saldo em 30 dias considerando uma saída de 800 ou 900?',
    'previsão de saldo em 30 dias considerando uma entrada de 800 e saída de 900?',
    'previsão de saldo em 30 dias se comprar um carro de 180 mil?',
    'dá pra pegar dois boletos de 800 pra dia 20?',
    'previsão de saldo nos próximos -30 dias?',
    'previsão de saldo nos próximos +30 dias?',
    'previsão de saldo nos próximos 3.0 dias?',
    'previsão de saldo nos próximos 30,5 dias?',
    'previsão de saldo nos próximos 99999999999999999999999999999999999999999999 dias?',
    'previsão de saldo nos próximos dias?',
    'previsão de saldo nos próximos 30 ou 60 dias?',
    'previsão de saldo nos próximos 30 dias ou em 60 dias?',
    'previsão de saldo nos próximos 30 dias ou próximos 30 dias?',
    'previsão de saldo nos próximos 30 dias ou para 2026-10-17?',
    'previsão de saldo nos próximos 30 dias ou 60?',
]


@pytest.mark.parametrize('texto', RECUSAS)
def test_contrato_recusa_sem_financeiro_e_preserva_conversa(pro_small_uid, ia_fora, chamadas, texto):
    uid = pro_small_uid
    assert 'registrada' in manda(uid, 'recebi 1500 de salário em dinheiro').lower()
    antes = estado(uid)
    resposta = manda(uid, texto)
    assert 'não consegui interpretar' in resposta.lower(), resposta
    assert not chamadas and not ia_fora and estado(uid) == antes


@pytest.mark.parametrize('prefixo', ['', 'piggy ', 'Piggy, ', 'piggy: ', 'pergunta ', 'ia '])
def test_contrato_prefixos_e_pending_de_valor(prefixo, monkeypatch, ia_fora):
    _conversa_previsao_preserva_pendencia(prefixo, 'bill_amount_expected',
        'como tô de boletos no dia 17?', monkeypatch, ia_fora)


@pytest.mark.parametrize('tipo', ['bill_amount_expected', 'payment_method_choice', 'confirm_media_launch'])
@pytest.mark.parametrize('texto', ['tô tranquilo até 17/10 com meus boletos?', 'aguento esse prazo?'])
def test_contrato_tres_estados_preservados(tipo, texto, monkeypatch, ia_fora):
    _conversa_previsao_preserva_pendencia('', tipo, texto, monkeypatch, ia_fora, recusada='aguento' in texto)


@pytest.mark.parametrize('literal', ['hoje', 'ontem'])
def test_contrato_literal_nao_default(pro_small_uid, ia_fora, chamadas, literal):
    from utils_date import extract_date_from_text
    uid = pro_small_uid
    manda(uid, 'recebi 1500 de salário em dinheiro')
    antes = estado(uid)
    resposta = manda(uid, 'previsão de saldo '+literal+'?')
    alvo, _ = extract_date_from_text(literal)
    assert chamadas == [('_check_cashflow', {'date': alvo.date().isoformat()})]
    assert ('indisponível' in resposta) if literal == 'ontem' else ('Saldo previsto condicional' in resposta)
    assert estado(uid) == antes and not ia_fora


def abre_pergunta_ia(uid, monkeypatch):
    def pergunta(u, text, **kwargs):
        db.ai_append_message(u, 'user', text)
        db.ai_append_message(u, 'assistant', 'Qual o valor do orçamento?')
        return 'Qual o valor do orçamento?'
    with monkeypatch.context() as m:
        m.setattr('core.services.ai_chat.chat', pergunta)
        assert manda(uid, 'piggy quero organizar meu orçamento') == 'Qual o valor do orçamento?'
    db.ai_set_pending_action(uid, 'create_budget', {'category': 'transporte', 'amount': 300}, 'Orçamento')


def abre_recategorizacao(uid, monkeypatch, launch_id):
    import adapters.whatsapp.wa_runtime as wr
    from adapters.whatsapp.wa_parse import InboundMessage
    _manda_texto_no_wa(monkeypatch, uid, '')  # transporte/canonical mocks existentes
    wr.process_message(InboundMessage(wa_id='5511999998888', text='', timestamp='3', attachments=[],
        raw={'id': f'wamid.recat.{launch_id}', 'type': 'interactive', 'interactive': {'type': 'list_reply',
             'list_reply': {'id': f'recatother:{launch_id}'}}}))
    assert db.get_pending_action(uid)['action_type'] == 'recategorize_launch_text'


@pytest.mark.parametrize('texto', [
    'tô tranquilo até 17/10 com meus boletos?', 'previsão de saldo de 30 dias',
    'tô tranquilo até dia 17?', 'qual meu saldo daqui 30 dias?',
    'aguento esse prazo?', 'previsão de saldo dia 32?',
    'previsão de saldo nos próximos 30 dias',
])
@pytest.mark.parametrize('porta', ['bill_pay_amount', 'recategorize_launch_text'])
def test_contrato_adapter_consulta_nao_paga_nem_consume(porta, texto, pro_small_uid, monkeypatch, ia_fora, chamadas):
    uid = pro_small_uid
    manda(uid, 'recebi 1500 de salário em dinheiro')
    launch_id = q('select id from launches where user_id=%s', (uid,))[0]['id']
    db.clear_pending_action(uid)
    abre_pergunta_ia(uid, monkeypatch)
    if porta == 'bill_pay_amount':
        bill = _monta_conta_variavel(uid)
        _toca_ja_paguei(monkeypatch, uid, int(bill['id']))
        assert db.get_pending_action(uid)['action_type'] == porta
    else:
        abre_recategorizacao(uid, monkeypatch, launch_id)
    antes = estado(uid)
    respostas = _manda_texto_no_wa(monkeypatch, uid, texto)
    depois = estado(uid)
    assert depois == antes, {'texto': texto, 'antes': antes, 'depois': depois, 'respostas': respostas}
    assert respostas and ('Saldo previsto condicional' in respostas[-1] or 'não consegui interpretar' in respostas[-1].lower()), respostas
    assert len(chamadas) == (0 if 'aguento' in texto or '32' in texto else 1) and not ia_fora
    if 'próximos' in texto:
        assert chamadas == [('_check_cashflow', {'days': 30})]
        esperado = _check_cashflow(uid, {'days': 30})
        assert esperado['target'] in respostas[-1] and fmt_brl(esperado['projetado']) in respostas[-1]


@pytest.mark.parametrize('texto', ['132,50', 'cancelar'])
def test_contrato_adapter_resposta_legitima_continua(pro_small_uid, monkeypatch, ia_fora, chamadas, texto):
    uid = pro_small_uid
    manda(uid, 'recebi 1500 de salário em dinheiro')
    db.clear_pending_action(uid)
    bill = _monta_conta_variavel(uid)
    _toca_ja_paguei(monkeypatch, uid, int(bill['id']))
    respostas = _manda_texto_no_wa(monkeypatch, uid, texto)
    atual = q('select status,paid_amount from bill_instances where user_id=%s and id=%s', (uid, bill['id']))[0]
    assert atual['status'] == ('paid' if texto == '132,50' else 'pending')
    assert ('Conta paga' if texto == '132,50' else 'deixei a conta') in respostas[-1]
    assert db.get_pending_action(uid) is None and not chamadas and not ia_fora


def test_contrato_adapter_categoria_legitima_continua(pro_small_uid, monkeypatch, ia_fora, chamadas):
    uid = pro_small_uid
    manda(uid, 'recebi 1500 de salário em dinheiro')
    launch_id = q('select id from launches where user_id=%s', (uid,))[0]['id']
    abre_recategorizacao(uid, monkeypatch, launch_id)
    _manda_texto_no_wa(monkeypatch, uid, 'transporte')
    assert q('select categoria from launches where user_id=%s and id=%s', (uid, launch_id))[0]['categoria'] == 'transporte'
    assert db.get_pending_action(uid) is None and not chamadas and not ia_fora


@pytest.mark.parametrize('plan,days,liberado', [('essencial', 30, False), ('plus', 30, True), ('plus', 31, False), ('pro_max', 90, True), ('pro_max', 91, False)])
def test_contrato_gate_canonico_antes_de_snapshot(monkeypatch, ia_fora, chamadas, plan, days, liberado):
    import core.services.cashflow as cashflow
    uid = usuario_pagante(plan)
    original = cashflow.carregar
    leituras = []
    def carregar(*args, **kwargs):
        leituras.append(args)
        return original(*args, **kwargs)
    monkeypatch.setattr(cashflow, 'carregar', carregar)
    resposta = manda(uid, f'previsão de saldo de {days} dias')
    assert chamadas == [('_check_cashflow', {'days': days})]
    assert bool(leituras) == liberado
    assert ('Saldo previsto condicional' in resposta) == liberado and not ia_fora


@pytest.mark.parametrize('texto', ['saldo: previsão?', 'previsão de saldo próximos meses?', 'fôlego daqui pra frente?'])
def test_contrato_visao_geral_sem_alvo(pro_small_uid, ia_fora, chamadas, texto):
    uid = pro_small_uid
    manda(uid, 'recebi 1500 de salário em dinheiro')
    antes = estado(uid)
    resposta = manda(uid, texto)
    assert chamadas == [('_forecast_balance', {})] and '30 dias' in resposta
    assert estado(uid) == antes and not ia_fora


@pytest.mark.parametrize('tipo,payload', [
    ('clarification', {'tipo': 'despesa', 'valor': 25}),
    ('multi_launch_values', {'fila': [{'tipo': 'despesa', 'desc': 'Luz'}]}),
    ('funding_source_choice', {'tipo': 'despesa', 'valor': 25}),
    ('investment_pick', {'valor': 25}),
    ('credit_card_setup', {'step': 'ask_name'}),
    ('delete_launch', {'launch_id': 1}),
    ('recategorize_launch_offer', {'launch_id': 1}),
])
def test_contrato_demais_pendencias_integras(pro_small_uid, monkeypatch, ia_fora, chamadas, tipo, payload):
    uid = pro_small_uid
    manda(uid, 'recebi 1500 de salário em dinheiro')
    db.clear_pending_action(uid)
    abre_pergunta_ia(uid, monkeypatch)
    db.set_pending_action(uid, tipo, payload)
    antes = estado(uid)
    resposta = manda(uid, 'tô tranquilo até dia 17 com meus boletos?')
    assert chamadas == [('_check_cashflow', {'date': '17'})] and 'Saldo previsto condicional' in resposta
    assert estado(uid) == antes and not ia_fora


@pytest.mark.parametrize('tipo', ['bill_amount_expected', 'payment_method_choice', 'confirm_media_launch'])
def test_contrato_proximos_preserva_estado_completo(tipo, monkeypatch, ia_fora, chamadas):
    import core.handle_incoming as hi
    original = hi.handle_incoming
    estados = []
    def observar(msg, **kwargs):
        antes = estado(msg.user_id)
        saida = original(msg, **kwargs)
        estados.append((antes, estado(msg.user_id)))
        return saida
    monkeypatch.setattr(hi, 'handle_incoming', observar)
    _conversa_previsao_preserva_pendencia('piggy ' if tipo == 'bill_amount_expected' else '', tipo,
        'previsão de saldo nos próximos 30 dias', monkeypatch, ia_fora)
    assert len(estados) == 2 and estados[1][0] == estados[1][1]
    assert chamadas == [('_check_cashflow', {'days': 30})] and not ia_fora


@pytest.mark.parametrize('plan,days,liberado', [('essencial', 30, False), ('plus', 30, True), ('plus', 31, False), ('pro_max', 90, True), ('pro_max', 91, False)])
def test_contrato_proximos_gate_antes_de_snapshot(monkeypatch, ia_fora, chamadas, plan, days, liberado):
    import core.services.cashflow as cashflow
    uid = usuario_pagante(plan)
    original = cashflow.carregar
    leituras = []
    def carregar(*args, **kwargs):
        leituras.append(args)
        return original(*args, **kwargs)
    monkeypatch.setattr(cashflow, 'carregar', carregar)
    resposta = manda(uid, f'previsão de saldo nos próximos {days} dias')
    assert chamadas == [('_check_cashflow', {'days': days})]
    assert bool(leituras) == liberado
    assert ('Saldo previsto condicional' in resposta) == liberado and not ia_fora


def test_contrato_proximos_politica_antes_de_tool(pro_small_uid, ia_fora, chamadas):
    from core.intent_router import INVESTMENT_ACTION_REFUSAL_MSG
    from core.response_formatter import format_for_platform
    uid = pro_small_uid
    manda(uid, 'recebi 1500 de salário em dinheiro')
    antes = estado(uid)
    resposta = manda(uid, 'previsão de saldo nos próximos 30 dias se eu comprar bitcoin?')
    assert resposta == format_for_platform(INVESTMENT_ACTION_REFUSAL_MSG, 'whatsapp')
    assert estado(uid) == antes and not chamadas and not ia_fora


def test_contrato_proximos_sem_previsao_nao_amplia_detector():
    from core.handle_incoming import _consulta_de_previsao
    assert not _consulta_de_previsao('qual meu saldo nos próximos 30 dias?')
