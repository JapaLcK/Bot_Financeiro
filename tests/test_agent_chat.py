"""Contrato do chat especialista: isolamento, acesso, consultas e cota."""
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
import db
import db.ai_chat as quota
from core.services import agent_chat as chat


def test_contexto_isolado_por_usuario_agente_e_reload():
    history = [{"role": "user", "content": "minha assinatura"}]
    token = chat.encode_context(42, "detetive", history)
    assert chat.decode_context(token, 42, "detetive") == history
    assert chat.decode_context(None, 42, "detetive") == []
    for uid, kind in [(43, "detetive"), (42, "barao")]:
        with pytest.raises(chat.ChatError, match="expirou"):
            chat.decode_context(token, uid, kind)
    with pytest.raises(chat.ChatError):
        chat.decode_context(token[:-8] + 'tampered', 42, 'detetive')


def test_contexto_limitado_a_20_mensagens():
    messages = [{"role": "user", "content": str(i)} for i in range(30)]
    assert chat.decode_context(chat.encode_context(42, 'xerife', messages), 42, 'xerife') == messages[-20:]


@pytest.mark.parametrize('kind', chat.DOMAINS)
def test_nenhum_agente_executa_write_ou_tool_de_outro_tema(monkeypatch, kind):
    calls = []
    monkeypatch.setattr(chat, 'get_tool', lambda name: SimpleNamespace(is_write=True, execute=lambda *a: calls.append(a)))
    assert 'error' in chat.execute_read(42, kind, 'delete_launch', {})
    for name in chat.READ_TOOLS[kind]:
        assert 'error' in chat.execute_read(42, kind, name, {})
    assert calls == []


def test_detetive_nao_consulta_investimentos(monkeypatch):
    monkeypatch.setattr(chat, 'get_tool', lambda name: pytest.fail('lookup fora do tema'))
    assert 'error' in chat.execute_read(42, 'detetive', 'list_investments', {})


@pytest.mark.parametrize('budget,active,expected', [(0, [], 'upgrade'), (4, [], 'activate'),
    (4, ['detetive'], 'ready'), (4, ['detetive', 'xerife'], 'no_energy'),
    (4, ['xerife'], 'no_energy'), (14, list(chat.DOMAINS), 'ready')])
def test_acesso_energia_e_downgrade(monkeypatch, budget, active, expected):
    from core.services import plan_service as ps
    monkeypatch.setattr(ps, 'plans_v2_enabled', lambda: True)
    monkeypatch.setattr(ps, 'agents_energy_budget', lambda uid: budget)
    monkeypatch.setattr(db, 'list_agents', lambda uid: [{'kind': k, 'status': 'active'} for k in active])
    assert chat.access_state(42, 'detetive') == expected


@pytest.fixture
def armed(monkeypatch):
    import openai
    from core.services import plan_service as ps
    monkeypatch.setenv('OPENAI_API_KEY', 'test-only')
    monkeypatch.setattr(openai, 'OpenAI', lambda **kw: object())
    monkeypatch.setattr(chat, 'require_access', lambda *a: None)
    monkeypatch.setattr(chat, 'access_state', lambda *a: 'ready')
    monkeypatch.setattr(ps, 'ai_monthly_limit_for', lambda uid: 10)
    monkeypatch.setattr(db, 'ai_get_usage_this_month', lambda uid: 2)
    for name in ['ai_append_message', 'ai_get_recent_messages', 'ai_get_pending_action', 'ai_set_pending_action']:
        monkeypatch.setattr(db, name, lambda *a, **kw: pytest.fail('tocou no chat geral'))
    charges = []
    monkeypatch.setattr(quota, 'try_consume_usage', lambda *a: charges.append(a) or 3)
    return charges


def test_redirecionamento_puro_nao_consulta_dados_nem_desconta(monkeypatch, armed):
    monkeypatch.setattr(chat, '_route', lambda *a: {'own_question': '', 'redirects': [{'kind': 'barao', 'question': 'O que é CDI?'}], 'outside': False})
    monkeypatch.setattr(chat, '_answer', lambda *a: pytest.fail('respondeu fora do tema'))
    monkeypatch.setattr(chat, 'access_state', lambda *a: 'upgrade')
    result = chat.chat(42, 'detetive', 'O que é CDI?')
    assert result['redirects'][0]['access'] == 'upgrade'
    assert result['usage']['used'] == 2
    assert armed == []


def test_pergunta_mista_responde_apenas_parte_propria(monkeypatch, armed):
    monkeypatch.setattr(chat, '_route', lambda *a: {'own_question': 'Há duplicatas?', 'redirects': [{'kind': 'barao', 'question': 'O que é CDI?'}], 'outside': False})
    def answer(client, uid, kind, question, history):
        assert question == 'Há duplicatas?'
        assert history == []
        return 'Há indícios que podemos conferir.'
    monkeypatch.setattr(chat, '_answer', answer)
    result = chat.chat(42, 'detetive', 'Há duplicatas e o que é CDI?')
    assert len(armed) == 1
    assert result['redirects'][0]['kind'] == 'barao'
    assert chat.decode_context(result['context'], 42, 'detetive')[0]['content'] == 'Há duplicatas?'


def test_falha_na_ia_nao_desconta(monkeypatch, armed):
    monkeypatch.setattr(chat, '_route', lambda *a: (_ for _ in ()).throw(ValueError('failure')))
    with pytest.raises(chat.ChatError) as exc:
        chat.chat(42, 'detetive', 'Há duplicatas?')
    assert exc.value.status == 503
    assert armed == []


def test_cota_esgotada_nao_chama_modelo(monkeypatch, armed):
    monkeypatch.setattr(db, 'ai_get_usage_this_month', lambda uid: 10)
    monkeypatch.setattr(chat, '_route', lambda *a: pytest.fail('chamou modelo'))
    with pytest.raises(chat.ChatError) as exc:
        chat.chat(42, 'detetive', 'Há duplicatas?')
    assert exc.value.code == 'quota_exhausted'


def test_detetive_consulta_duplicidades_sem_emitir_alertas(monkeypatch):
    from core.services import piggy_agents as agents
    monkeypatch.setattr(agents, 'find_duplicate_charges', lambda uid, today: [{'descricao': 'Mercado', 'repeticoes': 2}])
    monkeypatch.setattr(agents, 'find_recurring_charges', lambda uid, today: [])
    monkeypatch.setattr(db, 'record_agent_event', lambda *a, **k: pytest.fail('gravou evento'))
    assert chat.execute_read(42, 'detetive', 'consultar_dados_do_agente', {})['possiveis_duplicidades'][0]['repeticoes'] == 2


def test_classificador_descarta_destinos_invalidos():
    output = {'own_question': '', 'redirects': [{'kind': 'admin', 'question': 'x'}, {'kind': 'barao', 'question': 'CDI'}]}
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(output)))]))))
    assert chat._route(client, 'detetive', 'CDI', [])['redirects'] == [{'kind': 'barao', 'question': 'CDI'}]


def test_endpoint_autoriza_usuario_e_valida_corpo(monkeypatch):
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from frontend.routes import agents
    app = FastAPI()
    app.state.limiter = agents.shared.limiter
    app.include_router(agents.router)
    monkeypatch.setattr(agents, '_require_agents_beta', lambda uid: None)
    def authorize(request, uid):
        if uid != 42:
            raise HTTPException(status_code=403)
    monkeypatch.setattr(agents.shared, 'authorize_dashboard_access', authorize)
    monkeypatch.setattr(chat, 'chat', lambda *a: {'reply': 'ok'})
    client = TestClient(app)
    assert client.post('/agents/43/detetive/chat', json={'message': 'oi'}).status_code == 403
    assert client.post('/agents/42/detetive/chat', json={'message': ' '}).status_code == 400
    assert client.post('/agents/42/detetive/chat', json={'message': 'x' * 2001}).status_code == 422
    assert client.post('/agents/42/detetive/chat', json={'message': 'oi'}).json()['reply'] == 'ok'


def test_cota_atomica_entre_conversas(user_id):
    # Conta mínima para o contador compartilhado, sem signup nem envio externo.
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute('insert into auth_accounts (user_id, email) values (%s, %s)', (user_id, f'agent-{user_id}@test.invalid'))
        conn.commit()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: quota.try_consume_usage(user_id, 2), range(6)))
    assert sorted(x for x in results if x is not None) == [1, 2]
    assert db.ai_get_usage_this_month(user_id) == 2


def test_resposta_com_indicacao_de_compra_e_recusada():
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"valid": false}'))]))))
    with pytest.raises(ValueError, match='policy'):
        chat._check_answer(client, 'faria_limer', 'Minha carteira?', 'Você poderia comprar PETR4.')


def test_barao_nao_aplica_juros_ao_consultar(monkeypatch):
    monkeypatch.setattr(db, 'accrue_all_investments', lambda *a: pytest.fail('alterou investimentos'))
    monkeypatch.setattr(db, 'list_investments', lambda uid: [{'name': 'CDB', 'balance': 100}])
    monkeypatch.setattr(db, 'list_of_fixed_income', lambda uid: [])
    result = chat.execute_read(42, 'barao', 'consultar_dados_do_agente', {})
    assert result['investimentos_manuais'][0]['balance'] == 100
    assert 'list_investments' not in chat.READ_TOOLS['barao']


def test_consulta_real_do_detetive_nao_altera_lancamentos(user_id):
    from core.services.piggy_agents import find_duplicate_charges
    from datetime import date
    for _ in range(2):
        db.add_launch_and_update_balance(user_id, 'despesa', 49.90, 'Serviço repetido', 'assinatura', categoria='serviços')
    before = db.list_launches(user_id, limit=10)
    found = find_duplicate_charges(user_id, date.today())
    assert any(int(row['repeticoes']) == 2 for row in found)
    assert db.list_launches(user_id, limit=10) == before
    assert db.list_agent_events(user_id) == []


def test_loop_recusa_tool_fora_do_tema_mesmo_quando_modelo_pede(monkeypatch):
    called = []
    class Call:
        id = 'call1'
        function = SimpleNamespace(name='delete_launch', arguments='{"id": 1}')
        def model_dump(self):
            return {'id': self.id, 'type': 'function', 'function': {'name': self.function.name, 'arguments': self.function.arguments}}
    responses = iter([
        SimpleNamespace(content=None, tool_calls=[Call()]),
        SimpleNamespace(content='Posso consultar possíveis duplicidades, sem alterar seus lançamentos.', tool_calls=[]),
        SimpleNamespace(content='{"valid": true}'),
    ])
    def create(**kwargs):
        called.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=next(responses))])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(chat, 'get_tool', lambda name: pytest.fail('despachou write'))
    answer = chat._answer(client, 42, 'detetive', 'apague a duplicata', [])
    assert 'sem alterar' in answer
    assert 'não permitida' in called[1]['messages'][-1]['content']


@pytest.mark.parametrize('reply,consumed,expected_charge', [('Resposta do Piggy', 3, True), ('Resposta do Piggy', None, True), (None, None, False)])
def test_chat_geral_usa_mesma_cota_atomica(monkeypatch, armed, reply, consumed, expected_charge):
    from core.services.ai_chat import runner
    monkeypatch.setattr(db, 'ai_get_pending_action', lambda uid: None)
    monkeypatch.setattr(db, 'ai_get_recent_messages', lambda *a, **k: [])
    monkeypatch.setattr(db, 'ai_append_message', lambda *a, **k: None)
    monkeypatch.setattr(runner, '_run_tool_loop', lambda *a: reply or runner.ERROR_MSG)
    charges = []
    monkeypatch.setattr(quota, 'try_consume_usage', lambda *a: charges.append(a) or consumed)
    result = runner.chat(42, 'Como está meu mês?', monthly_limit=10)
    assert bool(charges) == expected_charge
    if reply and consumed is None:
        assert result == runner.LIMIT_MSG_TEMPLATE.format(limit=10)
    else:
        assert result == (reply or runner.ERROR_MSG)
