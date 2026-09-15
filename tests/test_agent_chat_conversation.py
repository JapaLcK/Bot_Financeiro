"""Continuidade e reparo de política sem liberar operações ou misturar agentes."""
import copy
import json
from types import SimpleNamespace

import pytest

import db
import db.ai_chat as quota
from core.services import agent_chat as chat


def response(content=None, calls=None):
    message = SimpleNamespace(content=content, tool_calls=calls or [])
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class ScriptedClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.requests.append(copy.deepcopy(kwargs))
        return next(self.responses)


class SnapshotCall:
    id = 'snapshot_1'
    function = SimpleNamespace(name='consultar_dados_do_agente', arguments='{}')

    def model_dump(self):
        return {'id': self.id, 'type': 'function', 'function': {
            'name': self.function.name, 'arguments': self.function.arguments}}


@pytest.fixture
def conversation(monkeypatch):
    import openai
    from core.services import plan_service
    charged = []
    monkeypatch.setenv('OPENAI_API_KEY', 'test-only')
    monkeypatch.setattr(chat, 'require_access', lambda *a: None)
    monkeypatch.setattr(chat, 'access_state', lambda *a: 'ready')
    monkeypatch.setattr(plan_service, 'ai_monthly_limit_for', lambda uid: 100)
    monkeypatch.setattr(db, 'ai_get_usage_this_month', lambda uid: len(charged))
    monkeypatch.setattr(quota, 'try_consume_usage', lambda *a: charged.append(a) or len(charged))
    for name in ('ai_append_message', 'ai_get_recent_messages', 'ai_get_pending_action', 'ai_set_pending_action'):
        monkeypatch.setattr(db, name, lambda *a, **kw: pytest.fail('acessou histórico geral'))

    def install(responses):
        client = ScriptedClient(responses)
        monkeypatch.setattr(openai, 'OpenAI', lambda **kw: client)
        return client
    return install, charged


def own_route(question='parte do próprio tema ou vazio', needs_data=False):
    return {'own_question': question, 'redirects': [], 'outside': False, 'needs_data': needs_data}


def test_classificador_placeholder_nao_substitui_pergunta_nem_contexto(monkeypatch, conversation):
    install, charged = conversation
    monkeypatch.setattr(chat, '_route', lambda *a: own_route())
    original = 'Quais ações devo investir?'
    client = install([response('Posso explicar critérios de avaliação, sem escolher uma ação.'),
                      response('{"valid":true,"reason":"ok"}')])
    result = chat.chat(42, 'faria_limer', original)
    assert client.requests[0]['messages'][-1]['content'] == original
    assert chat.decode_context(result['context'], 42, 'faria_limer')[0]['content'] == original
    assert len(charged) == 1


def test_pergunta_pessoal_exige_consulta_antes_da_resposta(monkeypatch):
    reads = []
    monkeypatch.setattr(chat, 'execute_read', lambda *a: reads.append(a) or {'saldo_cadastrado': 48000})
    client = ScriptedClient([response(calls=[SnapshotCall()]),
                             response('No recorte cadastrado há R$ 48 mil.'),
                             response('{"valid":true,"reason":"ok"}')])
    result = chat._answer(client, 42, 'faria_limer', 'Como está minha carteira?', [], needs_data=True)
    assert client.requests[0]['tool_choice'] == 'required'
    assert len(reads) == 1
    assert any(m['role'] == 'tool' for m in client.requests[1]['messages'])
    assert '48 mil' in result


@pytest.mark.parametrize('repaired_valid', [True, False])
def test_reparo_e_revalidado_uma_vez_sem_cobrar_rejeicao(monkeypatch, conversation, repaired_valid):
    install, charged = conversation
    monkeypatch.setattr(chat, '_route', lambda *a: own_route('Quais ações devo investir?'))
    client = install([
        response('Compre TEST3.'), response('{"valid":false,"reason":"financial_action"}'),
        response('Vale avaliar diversificação, prazo e concentração sem escolher um ativo.'),
        response(json.dumps({'valid': repaired_valid, 'reason': 'ok' if repaired_valid else 'financial_action'})),
    ])
    if repaired_valid:
        result = chat.chat(42, 'faria_limer', 'Quais ações devo investir?')
        assert result['reply'].startswith('Vale avaliar')
        assert len(charged) == 1
        history = chat.decode_context(result['context'], 42, 'faria_limer')
        assert not any('Compre TEST3' in m['content'] for m in history)
    else:
        with pytest.raises(chat.ChatError):
            chat.chat(42, 'faria_limer', 'Quais ações devo investir?')
        assert charged == []
    # Um rascunho, uma checagem, um reparo e uma rechecagem; nada é aprovado por omissão.
    assert len(client.requests) == 4


def test_pergunta_mista_nao_entrega_parte_de_outro_agente_ao_especialista(monkeypatch, conversation):
    install, charged = conversation
    own = 'Quais critérios ajudam a avaliar diversificação?'
    monkeypatch.setattr(chat, '_route', lambda *a: {
        **own_route(own), 'redirects': [{'kind': 'carteiro', 'question': 'Quando vence meu boleto?'}]})
    client = install([response('Prazo e concentração ajudam nessa avaliação.'),
                      response('{"valid":true,"reason":"ok"}')])
    result = chat.chat(42, 'faria_limer', own + ' Quando vence meu boleto?')
    assert client.requests[0]['messages'][-1]['content'] == own
    assert chat.decode_context(result['context'], 42, 'faria_limer')[0]['content'] == own
    assert result['redirects'][0]['kind'] == 'carteiro'
    assert len(charged) == 1


def test_tres_turnos_preservam_intencao_original_e_historico_semantico(monkeypatch, conversation):
    install, charged = conversation
    monkeypatch.setattr(chat, '_route', lambda *a: own_route())
    questions = ['Como está a concentração da minha carteira?', 'Quais ações devo investir?', 'Preciso de mais informações']
    answers = ['O retrato cadastrado é parcial.', 'Podemos avaliar diversificação e prazo.',
               'Diversificar envolve observar empresas, setores e classes de ativos.']
    context = None
    for index, (question, answer) in enumerate(zip(questions, answers)):
        client = install([response(answer), response('{"valid":true,"reason":"ok"}')])
        result = chat.chat(42, 'faria_limer', question, context)
        context = result['context']
        sent = client.requests[0]['messages']
        assert [m['content'] for m in sent if m['role'] == 'user'] == questions[:index + 1]
        assert [m['content'] for m in sent if m['role'] == 'assistant'] == answers[:index]
    assert [m['content'] for m in chat.decode_context(context, 42, 'faria_limer') if m['role'] == 'user'] == questions
    assert chat.decode_context(None, 42, 'faria_limer') == []
    assert len(charged) == 3


def test_chat_propaga_necessidade_de_dados_do_classificador(monkeypatch, conversation):
    install, charged = conversation
    monkeypatch.setattr(chat, '_route', lambda *a: own_route('Como está minha carteira?', needs_data=True))
    monkeypatch.setattr(chat, 'execute_read', lambda *a: {'saldo_cadastrado': 48000})
    client = install([response(calls=[SnapshotCall()]), response('O saldo cadastrado é R$ 48 mil.'),
                      response('{"valid":true,"reason":"ok"}')])
    chat.chat(42, 'faria_limer', 'Como está minha carteira?')
    assert client.requests[0].get('tool_choice') == 'required'
    assert len(charged) == 1
