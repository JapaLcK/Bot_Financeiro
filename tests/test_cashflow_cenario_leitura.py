"""Cenários textuais passam pelo handle_incoming real e preservam dinheiro/pendência."""
from datetime import date, timedelta

import pytest

import db
from core.services.ai_chat.tools.bills import _check_cashflow
from tests._fusao_of_helpers import manda, ia_fora
from tests.test_cashflow_snapshot import q
from utils_text import fmt_brl


@pytest.mark.parametrize('prefixo', ['', 'piggy '])
@pytest.mark.parametrize('prazo', ['daqui 30 dias', 'EM 30 DIAS', 'data'])
@pytest.mark.parametrize('direcao,valor,amount', [
    ('saída', 'R$ 800', 800), ('entrada', '800 reais', -800),
    ('despesa', 'R$ 800,25', 800.25), ('receita', '1.000,50', -1000.50),
])
def test_cenario_informado_chega_ao_mesmo_motor_sem_lancamento(
        pro_small_uid, ia_fora, prefixo, prazo, direcao, valor, amount):
    uid = pro_small_uid
    # Duas mensagens de assuntos diferentes; a primeira altera o saldo pelo fluxo real.
    assert 'registrada' in manda(uid, 'recebi 1500 de salário em dinheiro').lower()
    target = date.today() + timedelta(days=30)
    if prazo == 'data':
        prazo = 'para ' + target.strftime('%d/%m/%Y')
        pergunta = 'previsão de saldo'
    else:
        pergunta = 'qual o saldo'
    antes = q('select id,valor,efeitos from launches where user_id=%s order by id', (uid,))
    pendente = db.get_pending_action(uid)
    esperado = _check_cashflow(uid, {'date': str(target), 'amount': amount})
    resposta = manda(uid, prefixo + f'{pergunta} {prazo} considerando uma {direcao} de {valor}?')
    assert 'condicional' in resposta.lower()
    assert fmt_brl(esperado['projetado']) in resposta
    assert fmt_brl(abs(amount)) in resposta and ('entrada' if amount < 0 else 'saída') in resposta.lower()
    assert 'não autoriza uma compra' in resposta
    assert q('select id,valor,efeitos from launches where user_id=%s order by id', (uid,)) == antes
    assert db.get_pending_action(uid) == pendente and not ia_fora


@pytest.mark.parametrize('cenario', [
    'considerando uma saída', 'considerando uma entrada de valor desconhecido',
    'considerando R$ 800', 'considerando uma saída de 800\ne uma entrada de 200',
    'considerando uma saída de 800 ou 900', 'considerando uma entrada de -800',
    'considerando uma saída de 132 50', 'considerando uma saída de 1.23.456',
    'considerando uma saída de ,50', 'considerando uma saída ,50',
    'sem uma entrada de R$ 800',
    'se eu gastar R$ 800', 'com um ajuste de R$ 800',
])
def test_cenario_nao_interpretavel_nunca_vira_previsao_normal(pro_small_uid, ia_fora, cenario):
    uid = pro_small_uid
    resposta = manda(uid, f'qual o saldo daqui 30 dias {cenario}?')
    assert 'cenário' in resposta.lower() and 'não consegui interpretar' in resposta.lower()
    assert 'saldo previsto' not in resposta.lower()
    assert cenario in resposta
    assert db.get_balance(uid) == 0 and not q('select id from launches where user_id=%s', (uid,))
    assert not ia_fora


def test_cenario_sem_prazo_nao_descarta_valor_em_horizontes(pro_small_uid, ia_fora):
    resposta = manda(pro_small_uid, 'previsão de saldo considerando uma saída de R$ 800')
    assert 'cenário' in resposta.lower() and 'saldo previsto' not in resposta.lower()
    assert '800' in resposta and not ia_fora


@pytest.mark.parametrize('consulta', [
    'previsão de saldo em 30 ou em 60 dias considerando uma saída de R$ 800',
    'previsão de saldo daqui 30 dias ou em 60 dias considerando uma saída de R$ 800',
    'previsão de saldo para {primeira} ou {segunda} considerando uma entrada de R$ 800',
])
def test_cenario_com_prazo_ambiguo_nao_escolhe_um_horizonte(pro_small_uid, ia_fora, consulta):
    primeira = date.today() + timedelta(days=30)
    segunda = date.today() + timedelta(days=60)
    consulta = consulta.format(primeira=primeira.isoformat(), segunda=segunda.isoformat())
    resposta = manda(pro_small_uid, consulta)
    assert 'não consegui interpretar o cenário' in resposta.lower()
    assert 'saldo previsto' not in resposta.lower() and '800' in resposta and not ia_fora
