"""O replay aceita consultas legítimas sem acessar registros reais."""
import json
import sys
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
    assert failures == ['pure_redirect_queried_data']
