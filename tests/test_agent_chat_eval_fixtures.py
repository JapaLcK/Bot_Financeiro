"""O replay aceita consultas legítimas sem acessar registros reais."""
import calendar
import json
import sys
from datetime import date, timedelta
from unittest.mock import Mock, patch

import pytest

from core.services import agent_chat as chat
from scripts.eval_agent_chat import cases, mechanical_failures, synthetic_read


READ_CASES = [(kind, name) for kind, names in chat.READ_TOOLS.items()
              for name in sorted(names | {'consultar_dados_do_agente'})]


@pytest.mark.parametrize('kind,name', READ_CASES)
def test_todas_as_consultas_expostas_tem_fixture_sem_banco(kind, name):
    arguments = {
        'categoria': 'Alimentação', 'start_date': '2026-09-01', 'end_date': '2026-09-13',
        'period_a_start': '2026-08-01', 'period_a_end': '2026-08-31',
        'period_b_start': '2026-09-01', 'period_b_end': '2026-09-13',
    }
    # Bloqueia aquisição do pool e novas conexões, preservando a função
    # get_conn que módulos importados tardiamente guardam em seus globals.
    with (
        patch('psycopg_pool.ConnectionPool.connection', side_effect=AssertionError('Acessou banco')),
        patch('psycopg.connect', side_effect=AssertionError('Abriu conexão')),
        patch('psycopg.Connection.connect', side_effect=AssertionError('Abriu conexão')),
    ):
        result = synthetic_read(42, kind, name, arguments)
    assert isinstance(result, dict)
    assert not result.get('error')
    assert 'sintétic' in json.dumps(result, ensure_ascii=False, default=str).lower()


def test_fixture_nao_deixa_alias_de_conexao_mockado_nos_modulos():
    synthetic_read(42, 'carteiro', 'get_bills_to_pay', {})
    leaked = [name for name, module in list(sys.modules.items())
              if name.startswith(('db.', 'core.', 'frontend.')) and module is not None
              and isinstance(vars(module).get('get_conn'), Mock)]
    assert leaked == []


@pytest.mark.parametrize('uid,kind,name', [
    (42, 'detetive', 'get_balance'),
    (42, 'carteiro', 'pay_bill'),
    (42, 'faria_limer', 'inexistente'),
    (43, 'faria_limer', 'consultar_dados_do_agente'),
])
def test_fixture_nao_libera_consulta_proibida_ou_outro_usuario(uid, kind, name):
    with pytest.raises(AssertionError):
        synthetic_read(uid, kind, name, {})


def test_barao_e_faria_compartilham_o_mesmo_retrato_de_renda_fixa():
    faria = synthetic_read(42, 'faria_limer', 'consultar_dados_do_agente', {})
    barao = synthetic_read(42, 'barao', 'consultar_dados_do_agente', {})
    for key in ('balance', 'invested', 'pnl', 'count'):
        assert sum(row[key] for row in barao['renda_fixa']) == faria['renda_fixa_brl'][key]
    assert not barao['cobertura_renda_fixa']['patrimonio_completo']


def test_fixture_preserva_validacao_de_argumentos_do_contrato_real():
    result = synthetic_read(42, 'reporter', 'compare_periods', {})
    assert result.get('error')


def test_gates_continuam_recusando_redirecionamento_que_consulta_dados():
    case = next(case for case in cases('extended') if case['id'] == 'carteiro_summary')
    failures = mechanical_failures(case, {
        'reply': 'Esse assunto é com o Repórter.', 'redirects': [{'kind': 'reporter'}],
        'quota_delta': 0, 'data_queries': [{'agent': 'carteiro', 'tool': 'get_bills_to_pay'}],
    })
    assert failures == ['non_answer_queried_data']


def _formas_de_compare_periods():
    from utils_date import today_tz
    h = today_tz()
    primeiro = h.replace(day=1)
    p_ult = primeiro - timedelta(days=1)
    p_pri = p_ult.replace(day=1)
    pp_ult = p_pri - timedelta(days=1)
    ult = h.replace(day=calendar.monthrange(h.year, h.month)[1])

    def q4(a, b):
        return {'period_a_start': a[0].isoformat(), 'period_a_end': a[1].isoformat(),
                'period_b_start': b[0].isoformat(), 'period_b_end': b[1].isoformat()}
    return {
        'mes corrente': {'mes': f'{h:%Y-%m}'},
        'mes anterior': {'mes': f'{p_pri:%Y-%m}'},
        '4 datas de meses inteiros': q4((pp_ult.replace(day=1), pp_ult), (p_pri, p_ult)),
        'corrente x anterior (redireciona)': q4((p_pri, p_ult), (primeiro, h)),
        'corrente com fim cortado': q4((p_pri, p_ult), (primeiro, ult + timedelta(days=60))),
        'A e B trocados': q4((primeiro, h), (p_pri, p_ult)),
        'mais de 12 meses': q4((date(2025, 1, 1), date(2025, 9, 30)), (primeiro, h)),
        'parcial x parcial': q4((p_pri + timedelta(days=2), p_pri + timedelta(days=9)),
                                (primeiro, primeiro + timedelta(days=3))),
    }


@pytest.mark.parametrize('forma', list(_formas_de_compare_periods()))
def test_compare_periods_sem_banco_em_todas_as_formas_de_argumento(forma):
    """`mes=`, meses inteiros, redirecionamento e período livre: nenhuma forma abre conexão (a fixture só tinha datas
    parciais, que caem no período livre, e `mes=` quebrava com KeyError no `totais` sem patch)."""
    with (
        patch('psycopg_pool.ConnectionPool.connection', side_effect=AssertionError('Acessou banco')),
        patch('psycopg.connect', side_effect=AssertionError('Abriu conexão')),
        patch('psycopg.Connection.connect', side_effect=AssertionError('Abriu conexão')),
    ):
        result = synthetic_read(42, 'reporter', 'compare_periods', _formas_de_compare_periods()[forma])
    assert not result.get('error'), result
    assert result['base']['cartao'] in ('fatura', 'data_da_compra')


def test_investido_sintetico_distingue_total_de_renda_fixa():
    faria = synthetic_read(42, 'faria_limer', 'consultar_dados_do_agente', {})['investido']
    barao = synthetic_read(42, 'barao', 'consultar_dados_do_agente', {})
    assert faria['total_investido'] == '64321.00'
    assert barao['total_renda_fixa'] == '48000.00' != faria['total_investido']

