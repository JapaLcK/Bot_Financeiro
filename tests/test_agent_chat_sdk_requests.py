"""Contrato serializado pelo SDK, sem rede nem chamadas reais ao provedor."""
import json

import httpx
from openai import OpenAI
import pytest

from core.services import agent_chat as chat


def sdk_client(contents, requests):
    responses = iter(contents)

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        return httpx.Response(200, json={
            'id': 'synthetic-chat-completion', 'object': 'chat.completion',
            'created': 0, 'model': payload['model'],
            'choices': [{'index': 0, 'finish_reason': 'stop', 'message': {
                'role': 'assistant', 'content': next(responses),
            }}],
        })

    # Exercita a serialização real do SDK, com transporte estritamente local;
    # o bloqueio global de transportes de rede do conftest continua ativo.
    return OpenAI(api_key='synthetic-test-only', max_retries=0,
                  http_client=httpx.Client(transport=httpx.MockTransport(respond)))


@pytest.mark.parametrize('answer_budget', [1024, 4096])
@pytest.mark.parametrize('model', ['gpt-4.1-mini', 'gpt-5.4-mini'])
def test_sdk_recebe_parametros_compativeis_em_todas_as_etapas(monkeypatch, answer_budget, model):
    monkeypatch.setattr(chat, 'MODEL', model)
    monkeypatch.setattr(chat, 'MAX_TOKENS', answer_budget)
    requests = []
    question = 'Como diferenciar recorrência de duplicidade?'
    contents = [json.dumps({'parts': [
        {'kind': 'detetive', 'question': question, 'needs_data': False}]}),
        'Recorrência indica periodicidade; duplicidade é um indício a conferir.',
        '{"valid": true, "reason": "ok"}']
    with sdk_client(contents, requests) as client:
        route = chat._route(client, 'detetive', question, [])
        answer = chat._answer(client, 42, 'detetive', route['own_question'], [],
                              needs_data=route['needs_data'])

    assert answer.startswith('Recorrência indica periodicidade')
    assert len(requests) == 3
    for payload, budget, temperature in zip(requests, [900, answer_budget, 180], [0, 0.2, 0]):
        assert payload['model'] == model
        if model == 'gpt-5.4-mini':
            assert payload['max_completion_tokens'] == max(budget, 2048)
            assert payload['reasoning_effort'] == ('none' if 'tools' in payload else 'low')
            assert 'temperature' not in payload
            assert 'max_tokens' not in payload
        else:
            assert payload['max_tokens'] == budget
            assert payload['temperature'] == temperature
            assert 'max_completion_tokens' not in payload
            assert 'reasoning_effort' not in payload
    assert requests[0]['response_format']['json_schema']['strict'] is True
    assert requests[2]['response_format']['json_schema']['strict'] is True


def test_partes_mistas_preservam_todos_os_trechos_sem_misturar_destinos():
    requests = []
    parts = [
        {'kind': 'detetive', 'question': 'Há cobranças duplicadas?', 'needs_data': True},
        {'kind': 'barao', 'question': 'O que é CDI?', 'needs_data': False},
        {'kind': 'detetive', 'question': 'Como avaliar a recorrência?', 'needs_data': False},
        {'kind': 'carteiro', 'question': 'Quais boletos vencem amanhã?', 'needs_data': True},
        {'kind': 'barao', 'question': 'O que significa pós-fixado?', 'needs_data': False},
        {'kind': 'outside', 'question': 'Como ficará o tempo amanhã?', 'needs_data': False},
    ]
    question = ' '.join(part['question'] for part in parts)
    with sdk_client([json.dumps({'parts': parts})], requests) as client:
        route = chat._route(client, 'detetive', question, [])
    assert route == {
        'own_question': 'Há cobranças duplicadas?\nComo avaliar a recorrência?',
        'needs_data': True,
        'outside': True,
        'redirects': [
            {'kind': 'barao', 'question': 'O que é CDI?\nO que significa pós-fixado?'},
            {'kind': 'carteiro', 'question': 'Quais boletos vencem amanhã?'},
        ],
    }
