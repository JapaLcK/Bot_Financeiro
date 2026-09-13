"""Consulta e cobrança do replay são expectativas independentes dos destinos."""
import pytest

from scripts.eval_agent_chat import cases, mechanical_failures


@pytest.mark.parametrize('case_id,own,needs_data,charge,redirects', [
    ('mixed_due_summary', True, True, 1, ['reporter']),
    ('outside_domain', False, False, 0, []),
    ('carteiro_summary', False, False, 0, ['reporter']),
    ('portfolio', True, True, 1, []),
    ('choose_stocks', True, False, 1, []),
])
def test_casos_declaram_consulta_resposta_e_cobranca(case_id, own, needs_data, charge, redirects):
    case = next(row for row in cases('extended') if row['id'] == case_id)
    assert case['answers_own_topic'] is own
    assert case['requires_data'] is needs_data
    assert case['expected_charge'] == charge
    assert case['expected_redirects'] == redirects


@pytest.mark.parametrize('case_id,redirects,queried,charge,expected', [
    # Responder o tema próprio pode coexistir com encaminhamento e cobrança.
    ('mixed_due_summary', ['reporter'], True, 1, []),
    ('mixed_due_summary', ['reporter'], False, 1, ['missing_data_query']),
    ('mixed_due_summary', ['reporter'], True, 0, ['unexpected_quota_delta']),
    ('mixed_due_summary', ['reporter'], True, 2, ['unexpected_quota_delta']),
    ('mixed_due_summary', [], True, 1, ['unexpected_redirects']),
    # Fora de todos os temas: sem destino, sem consulta e sem cobrança.
    ('outside_domain', [], False, 0, []),
    ('outside_domain', [], True, 0, ['non_answer_queried_data']),
    ('outside_domain', [], False, 1, ['unexpected_quota_delta']),
    # Encaminhamento puro também não autoriza consulta nem cobrança.
    ('carteiro_summary', ['reporter'], False, 0, []),
    ('carteiro_summary', ['reporter'], True, 0, ['non_answer_queried_data']),
    ('carteiro_summary', ['reporter'], False, 1, ['unexpected_quota_delta']),
    # A exigência de consulta permanece independente de haver encaminhamento.
    ('portfolio', [], True, 1, []),
    ('portfolio', [], False, 1, ['missing_data_query']),
    ('portfolio', [], True, 0, ['unexpected_quota_delta']),
    ('choose_stocks', [], False, 1, []),
])
def test_gates_distinguem_partes_proprias_externas_e_encaminhamentos(
        case_id, redirects, queried, charge, expected):
    case = next(row for row in cases('extended') if row['id'] == case_id)
    record = {
        'reply': 'Resposta sintética desta avaliação.',
        'redirects': [{'kind': kind} for kind in redirects],
        'data_queries': [{'agent': case['agent'], 'tool': 'consultar_dados_do_agente'}] if queried else [],
        'quota_delta': charge,
    }
    assert mechanical_failures(case, record) == expected


def test_erro_observado_nao_reescreve_a_cobranca_esperada_do_caso():
    case = next(row for row in cases('extended') if row['id'] == 'portfolio')
    record = {
        'error_class': 'ChatError', 'status': 503, 'redirects': [],
        'data_queries': [{'agent': 'faria_limer', 'tool': 'consultar_dados_do_agente'}],
        'quota_delta': 0,
    }
    assert mechanical_failures(case, record) == [
        'request_failed', 'missing_reply', 'unexpected_quota_delta',
    ]
