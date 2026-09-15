"""Respostas do SDK podem ser truncadas, recusadas ou inválidas."""
import json
from types import SimpleNamespace

import pytest

from core.services import agent_chat as chat


def reply(content, *, finish='stop', refusal=None, calls=None):
    message = SimpleNamespace(content=content, refusal=refusal, tool_calls=calls or [])
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish)])


def client_for(response, requests):
    def create(**kwargs):
        requests.append(kwargs)
        return response
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


@pytest.mark.parametrize('response', [
    reply('{"own_question":', finish='length'),
    reply(None, refusal='Recusa'),
    reply('Não é JSON'),
    reply('[]'),
    reply('{"own_question":null,"redirects":[]}'),
    SimpleNamespace(choices=[]),
])
def test_rota_invalida_tem_erro_especifico_sem_inventar_encaminhamento(response):
    with pytest.raises(chat.ModelResponseError):
        chat._route(client_for(response, []), 'carteiro', 'Entradas do mês?', [])


def test_rota_estruturada_preserva_destino_e_necessidade_de_consulta():
    requests = []
    response = reply(json.dumps({'parts': [
        {'kind': 'reporter', 'question': 'Como estão as entradas do mês?', 'needs_data': True}]}))
    route = chat._route(client_for(response, requests), 'carteiro', 'Como estão as entradas do mês?', [])
    assert route['redirects'] == [{'kind': 'reporter', 'question': 'Como estão as entradas do mês?'}]
    contract = requests[0]['response_format']['json_schema']
    assert contract['strict'] is True
    assert set(contract['schema']['properties']['parts']['items']['properties']['kind']['enum']) == set(chat.DOMAINS) | {'outside'}
    assert route['own_question'] == ''
    assert route['needs_data'] is False


def test_pergunta_mista_consulta_so_a_parte_do_agente():
    response = reply(json.dumps({'parts': [
        {'kind': 'faria_limer', 'question': 'Explique diversificação', 'needs_data': False},
        {'kind': 'carteiro', 'question': 'Quais boletos vencem amanhã?', 'needs_data': True}]}))
    route = chat._route(client_for(response, []), 'faria_limer',
                        'Explique diversificação e quais boletos vencem amanhã?', [])
    assert route['own_question'] == 'Explique diversificação'
    assert route['needs_data'] is False
    assert route['redirects'] == [{'kind': 'carteiro', 'question': 'Quais boletos vencem amanhã?'}]


@pytest.mark.parametrize('model', ['gpt-5.4-mini', 'gpt-4.1-mini'])
def test_rota_usa_parametros_compativeis_com_o_modelo_configurado(monkeypatch, model):
    monkeypatch.setattr(chat, 'MODEL', model)
    requests = []
    response = reply(json.dumps({'parts': [
        {'kind': 'faria_limer', 'question': 'O que é P/L?', 'needs_data': False}]}))
    chat._route(client_for(response, requests), 'faria_limer', 'O que é P/L?', [])
    request = requests[0]
    assert request['model'] == model
    if model == 'gpt-5.4-mini':
        assert request['max_completion_tokens'] > 0
        assert request['reasoning_effort'] == 'low'
        assert 'max_tokens' not in request
        assert 'temperature' not in request
    else:
        assert request['max_tokens'] > 0
        assert request['temperature'] == 0
        assert 'reasoning_effort' not in request


@pytest.mark.parametrize('response', [
    reply('Resposta incompleta', finish='length'),
    reply(None, refusal='Recusa'),
    SimpleNamespace(choices=[]),
])
def test_resposta_interrompida_nao_vira_resposta_do_agente(response):
    with pytest.raises(chat.ModelResponseError):
        chat._answer(client_for(response, []), 42, 'faria_limer', 'O que é P/L?', [])


def test_falha_na_consulta_nao_e_confundida_com_lista_vazia(monkeypatch):
    class Call:
        id = 'snapshot'
        function = SimpleNamespace(name='consultar_dados_do_agente', arguments='{}')

        def model_dump(self):
            return {'id': self.id, 'type': 'function', 'function': {'name': self.function.name, 'arguments': '{}'}}

    def fail(*args):
        raise RuntimeError('Erro de banco')

    monkeypatch.setattr(chat, 'execute_read', fail)
    requests = []
    with pytest.raises(chat.DataQueryError):
        chat._answer(client_for(reply(None, calls=[Call()]), requests), 42, 'faria_limer', 'Minha carteira?', [], needs_data=True)
    assert len(requests) == 1
