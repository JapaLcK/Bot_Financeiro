"""Prazo e próxima fatura: conversa e PostgreSQL reais, sem mock financeiro."""
from datetime import date, timedelta
from decimal import Decimal

import pytest
import db
from core.services.ai_chat.tools.bills import _check_cashflow, _forecast_balance
from core.services.ai_chat.tools.cards import _forecast_next_bill, _get_total_debt
from db.cards import ler_faturas
from tests._fusao_of_helpers import manda, ia_fora
from tests.test_cashflow_snapshot import q, snapshot, _conversa_previsao_preserva_pendencia
from tests.conftest import usuario_pagante
from utils_text import fmt_brl

PRAZOS = ['daqui 30 dias', 'daqui a 30 dias', 'em 30 dias', 'DAQUI A 30 DIAS',
          'daqui\na\n30\ndias', 'iso', 'br', 'ausente', 'multiplo', 'misto', 'multiplo_sem_a', 'duas_datas', 'misto_sem_a']


def consulta(prazo, estado):
    alvo = date.today() + timedelta(days=30)
    sufixo = {'iso': 'para '+alvo.isoformat(), 'br': 'para '+alvo.strftime('%d/%m/%Y'),
              'ausente': '', 'multiplo': 'daqui a 30 dias ou em 60 dias',
              'misto': 'daqui a 30 dias ou para '+(alvo+timedelta(days=30)).isoformat(),
              'multiplo_sem_a': 'daqui 30 dias ou em 60 dias',
              'duas_datas': 'para '+alvo.isoformat()+' ou '+(alvo+timedelta(days=30)).strftime('%d/%m/%Y'),
              'misto_sem_a': 'daqui 30 dias ou para '+(alvo+timedelta(days=30)).isoformat()}.get(prazo, prazo)
    texto = ('qual meu saldo ' if estado == 'pura' and prazo not in ('iso', 'br', 'ausente', 'misto', 'duas_datas', 'misto_sem_a') else 'previsão de saldo ')+sufixo
    if estado == 'aceito':
        texto += ' considerando uma saída de R$ 800'
    elif estado == 'ambiguo':
        texto += ' considerando uma saída de valor desconhecido'
    return texto.strip()+'?'


@pytest.mark.parametrize('prefixo', ['', 'piggy '])
@pytest.mark.parametrize('prazo', PRAZOS)
@pytest.mark.parametrize('estado', ['pura', 'aceito', 'ambiguo'])
def test_prazo_na_conversa_preserva_consulta_e_cenario(pro_small_uid, ia_fora, prefixo, prazo, estado):
    uid = pro_small_uid
    assert 'registrada' in manda(uid, 'recebi 1500 de salário em dinheiro').lower()
    antes = q('select id,valor,efeitos from launches where user_id=%s order by id', (uid,))
    pendente = db.get_pending_action(uid)
    resposta = manda(uid, prefixo+consulta(prazo, estado))
    recusada = estado == 'ambiguo' or prazo in ('multiplo', 'misto', 'multiplo_sem_a', 'duas_datas', 'misto_sem_a') or (prazo == 'ausente' and estado == 'aceito')
    if recusada:
        assert 'não consegui interpretar' in resposta.lower(), resposta
        assert 'saldo previsto' not in resposta.lower()
    else:
        assert 'condicional' in resposta.lower(), resposta
        if prazo == 'ausente':
            esperado = _forecast_balance(uid, {})['horizons']['30']
        else:
            esperado = _check_cashflow(uid, {'days': 30, **({'amount': 800} if estado == 'aceito' else {})})
            assert str(date.today()+timedelta(days=30)) in resposta
        assert fmt_brl(esperado['projetado']) in resposta
    assert q('select id,valor,efeitos from launches where user_id=%s order by id', (uid,)) == antes
    assert db.get_pending_action(uid) == pendente and not ia_fora


@pytest.mark.parametrize('prefixo', ['', 'piggy '])
@pytest.mark.parametrize('tipo', ['bill_amount_expected', 'payment_method_choice', 'confirm_media_launch'])
@pytest.mark.parametrize('prazo,estado', [
    ('daqui a 30 dias', 'pura'), ('DAQUI A 30 DIAS', 'aceito'),
    ('daqui\na\n30\ndias', 'aceito'), ('iso', 'pura'), ('ausente', 'pura'),
    ('daqui a 30 dias', 'ambiguo'), ('ausente', 'aceito'),
    ('multiplo', 'pura'), ('misto', 'pura'), ('multiplo', 'aceito'), ('multiplo_sem_a', 'pura'), ('duas_datas', 'pura'),
])
def test_prazo_preserva_tres_pendencias(prefixo, tipo, prazo, estado, monkeypatch, ia_fora):
    recusada = estado == 'ambiguo' or prazo in ('multiplo', 'misto', 'multiplo_sem_a', 'duas_datas', 'misto_sem_a') or (prazo == 'ausente' and estado == 'aceito')
    _conversa_previsao_preserva_pendencia(prefixo, tipo, consulta(prazo, estado), monkeypatch, ia_fora, recusada=recusada)


def fatura(uid, card, fim, status, total, paid=0):
    return q('insert into credit_bills (user_id,card_id,period_start,period_end,status,total,paid_amount) '
             'values (%s,%s,%s,%s,%s,%s,%s) returning id',
             (uid, card, fim-timedelta(days=29), fim, status, Decimal(str(total)), Decimal(str(paid))))[0]['id']


def observadas(uid):
    return q('select id,card_id,period_start,period_end,status,total::text,paid_amount::text '
             'from credit_bills where user_id=%s order by id', (uid,))


@pytest.mark.parametrize('futuro', [0, 30], ids=['atual', 'futura'])
@pytest.mark.parametrize('nome', [False, True], ids=['todos', 'cartao_nome'])
def test_fatura_closed_nao_oculta_proxima_open_e_divida_permanece(user_id, futuro, nome):
    uid = user_id
    card = db.create_card(uid, 'Próxima', 10, 17)
    antiga = fatura(uid, card, date.today()-timedelta(days=40), 'closed', 100, 30)
    atual = fatura(uid, card, date.today()+timedelta(days=futuro), 'open', 50, 10)
    outro = usuario_pagante()
    secreto = db.create_card(outro, 'Próxima', 10, 17)
    fatura(outro, secreto, date.today()-timedelta(days=60), 'open', 9876)
    antes, antes_outro = observadas(uid), observadas(outro)
    resultado = _forecast_next_bill(uid, {'card_name': 'Próxima'} if nome else {})
    assert resultado['count'] == 1 and resultado['cards'][0]['bill_id'] == atual, resultado
    assert resultado['total'] == 50 and resultado['cards'][0]['restante'] == 40
    with db.get_conn() as conn, conn.cursor() as cur:
        raw = ler_faturas(cur, uid)
    assert [b['id'] for b in raw] == [antiga, atual]
    ocorrencias = {e.origem_id: e.valor for e in snapshot(uid, 90).ocorrencias if e.fonte == 'fatura'}
    assert ocorrencias == {antiga: Decimal(70), atual: Decimal(40)}
    divida = _get_total_debt(uid, {})
    assert divida['total_debt'] == 110, divida
    assert observadas(uid) == antes and observadas(outro) == antes_outro


@pytest.mark.parametrize('status,total,paid,restante', [
    ('open', 50, 10, 40), ('paid', 100, 30, 70), ('paid', -10, -10, None),
    ('paid', 0, -1, None), ('paid', 100, 100, 'ausente'),
    ('closed', 100, 30, 'ausente'),
])
def test_fatura_elegibilidade_preserva_paid_incoerente_e_ausencia(user_id, status, total, paid, restante):
    uid = user_id
    card = db.create_card(uid, 'Elegibilidade', 10, 17)
    fatura(uid, card, date.today()-timedelta(days=40), 'closed', 100, 30)
    bid = fatura(uid, card, date.today(), status, total, paid)
    antes = observadas(uid)
    out = _forecast_next_bill(uid, {})
    if restante == 'ausente':
        assert out['cards'] == [] and out['count'] == 0 and out['total'] is None, out
        assert out['estado'] == 'indisponivel' and out['motivos'] == ['fatura_observada_ausente']
    else:
        assert out['count'] == 1 and out['cards'][0]['bill_id'] == bid, out
        assert out['total'] == total and out['cards'][0]['restante'] == restante
        assert ('valor_fatura_a_conferir' in out['cards'][0]['motivos']) == (restante is None)
    assert observadas(uid) == antes


PEDIDOS_ATIVOS = [
    'qual meu saldo daqui 30 dias se eu comprar bitcoin?',
    'qual meu saldo daqui 30 dias; recomende um CDB?',
    'previsão de saldo em 30 dias considerando uma saída de R$ 800 para comprar bitcoin?',
    'previsão de saldo em 30 dias ou em 60 dias se eu comprar bitcoin?',
]


@pytest.mark.parametrize('prefixo', ['', 'piggy ', 'pergunta ', 'ia '])
@pytest.mark.parametrize('pedido', PEDIDOS_ATIVOS)
def test_previsao_recusa_politica_existente_antes_do_cenario(pro_small_uid, ia_fora, prefixo, pedido):
    from core.intent_router import investment_action_refusal, INVESTMENT_ACTION_REFUSAL_MSG
    from core.response_formatter import format_for_platform
    uid = pro_small_uid
    assert 'registrada' in manda(uid, 'recebi 1500 de salário em dinheiro').lower()
    antes = q('select id,valor,efeitos from launches where user_id=%s order by id', (uid,))
    pendente = db.get_pending_action(uid)
    assert investment_action_refusal(prefixo+pedido) == INVESTMENT_ACTION_REFUSAL_MSG
    resposta = manda(uid, prefixo+pedido)
    assert resposta == format_for_platform(INVESTMENT_ACTION_REFUSAL_MSG, 'whatsapp'), resposta
    assert q('select id,valor,efeitos from launches where user_id=%s order by id', (uid,)) == antes
    assert db.get_pending_action(uid) == pendente and not ia_fora


@pytest.mark.parametrize('prefixo', ['', 'piggy '])
@pytest.mark.parametrize('tipo', ['bill_amount_expected', 'payment_method_choice', 'confirm_media_launch'])
def test_previsao_politica_preserva_pendencia_financeira(prefixo, tipo, monkeypatch, ia_fora):
    from core.intent_router import INVESTMENT_ACTION_REFUSAL_MSG
    from core.response_formatter import format_for_platform
    _conversa_previsao_preserva_pendencia(prefixo, tipo, PEDIDOS_ATIVOS[0], monkeypatch, ia_fora,
        resposta_esperada=format_for_platform(INVESTMENT_ACTION_REFUSAL_MSG, 'whatsapp'))


@pytest.mark.parametrize('pedido', [
    'qual meu saldo daqui 30 dias?',
    'qual meu saldo daqui 30 dias e o que é bitcoin?',
    'previsão de saldo em 30 dias considerando uma saída de R$ 800?',
])
def test_previsao_politica_nao_bloqueia_leitura_legitima(pro_small_uid, ia_fora, pedido):
    from core.intent_router import investment_action_refusal
    uid = pro_small_uid
    assert 'registrada' in manda(uid, 'recebi 1500 de salário em dinheiro').lower()
    assert investment_action_refusal(pedido) is None
    antes = q('select id,valor,efeitos from launches where user_id=%s order by id', (uid,))
    resposta = manda(uid, pedido)
    esperado = _check_cashflow(uid, {'days': 30, **({'amount': 800} if '800' in pedido else {})})
    assert 'saldo previsto condicional' in resposta.lower(), resposta
    assert fmt_brl(esperado['projetado']) in resposta
    assert q('select id,valor,efeitos from launches where user_id=%s order by id', (uid,)) == antes
    assert not ia_fora


@pytest.mark.parametrize('pedido', [PEDIDOS_ATIVOS[0], 'qual meu saldo daqui 30 dias?'])
def test_previsao_politica_mantem_pergunta_e_ai_pending(pro_small_uid, monkeypatch, ia_fora, pedido):
    from core.intent_router import INVESTMENT_ACTION_REFUSAL_MSG, investment_action_refusal
    from core.response_formatter import format_for_platform
    from core.services.ai_chat_commands import pergunta_aberta_da_ia
    uid = pro_small_uid
    def pergunta(u, text, **kwargs):
        db.ai_append_message(u, 'user', text)
        db.ai_append_message(u, 'assistant', 'Qual o valor do orçamento?')
        return 'Qual o valor do orçamento?'
    with monkeypatch.context() as m:
        m.setattr('core.services.ai_chat.chat', pergunta)
        assert manda(uid, 'piggy quero organizar meu orçamento') == 'Qual o valor do orçamento?'
    db.ai_set_pending_action(uid, 'create_budget', {'category': 'transporte', 'amount': 300}, 'Orçamento de transporte')
    pergunta_antes = pergunta_aberta_da_ia(uid)
    pending_antes = db.ai_get_pending_action(uid)
    mensagens_antes = q('select id,role,content from ai_messages where user_id=%s order by id', (uid,))
    resposta = manda(uid, pedido)
    if investment_action_refusal(pedido):
        assert resposta == format_for_platform(INVESTMENT_ACTION_REFUSAL_MSG, 'whatsapp'), resposta
    else:
        assert 'saldo previsto condicional' in resposta.lower(), resposta
    assert pergunta_antes is not None and pergunta_aberta_da_ia(uid) == pergunta_antes
    assert db.ai_get_pending_action(uid) == pending_antes
    assert q('select id,role,content from ai_messages where user_id=%s order by id', (uid,)) == mensagens_antes
    assert not q('select id from launches where user_id=%s', (uid,)) and not ia_fora


def test_politica_educacao_de_ativo_continua_chegando_a_ia(pro_small_uid, ia_fora):
    from core.intent_router import investment_action_refusal
    uid = pro_small_uid
    assert 'registrada' in manda(uid, 'recebi 1500 de salário em dinheiro').lower()
    pedido = 'piggy o que é bitcoin?'
    assert investment_action_refusal(pedido) is None
    assert '[IA-NAO-DEVIA-SER-CHAMADA]' in manda(uid, pedido)
    assert ia_fora == ['o que é bitcoin?']
