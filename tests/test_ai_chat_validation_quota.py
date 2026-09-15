"""Validar uma ação não equivale a tentar gravar a ação ou sua confirmação."""
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

import db
from db.ai_chat import get_usage_this_month
from core.services.ai_chat import runner


def prepare_chat(monkeypatch, user_id, calls, failure_role):
    import openai
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute('insert into auth_accounts (user_id, email) values (%s, %s)',
                    (user_id, f'validation-{user_id}@test.invalid'))
        conn.commit()

    class Call:
        def __init__(self, index, name, args):
            self.value = {'id': f'call_{index}', 'type': 'function',
                          'function': {'name': name, 'arguments': json.dumps(args)}}

        def model_dump(self):
            return self.value

    class Completions:
        def __init__(self):
            self.round = 0

        def create(self, **kwargs):
            self.round += 1
            message = (SimpleNamespace(content=None, tool_calls=[Call(i, *call) for i, call in enumerate(calls)])
                       if self.round == 1 else SimpleNamespace(content='Confirma a ação?', tool_calls=[]))
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    monkeypatch.setenv('OPENAI_API_KEY', 'test-only')
    monkeypatch.setattr(openai, 'OpenAI', lambda **kw: SimpleNamespace(chat=SimpleNamespace(completions=Completions())))
    append = db.ai_append_message

    def persist(uid, role, content, **kwargs):
        # Permite o registro da chamada; falha somente depois do despacho.
        if role == failure_role and (role == 'tool' or content):
            raise RuntimeError('falha ao salvar resultado')
        return append(uid, role, content, **kwargs)

    monkeypatch.setattr(db, 'ai_append_message', persist)


@pytest.mark.parametrize('call', [
    ('delete_launch', {'launch_id': '999999999'}),
    ('delete_budget', {'categoria': 'alimentação'}),
    ('delete_all_launches', {}),
])
@pytest.mark.parametrize('failure_role', ['tool', 'assistant'])
def test_validacao_recusada_restitui_se_historico_falha(monkeypatch, user_id, call, failure_role):
    prepare_chat(monkeypatch, user_id, [call], failure_role)
    with pytest.raises(RuntimeError, match='salvar resultado'):
        runner.chat(user_id, 'Apague esse registro.', monthly_limit=1)
    assert db.ai_get_pending_action(user_id) is None
    assert db.count_launches(user_id) == 0
    assert get_usage_this_month(user_id) == 0


@pytest.mark.parametrize('failure_role', ['tool', 'assistant'])
def test_validacao_aprovada_mantem_cota_apos_criar_pendencia(monkeypatch, user_id, failure_role):
    db.add_launch_and_update_balance(user_id, 'despesa', 25, 'Mercado', 'teste', categoria='alimentação')
    prepare_chat(monkeypatch, user_id, [('delete_all_launches', {})], failure_role)
    with pytest.raises(RuntimeError, match='salvar resultado'):
        runner.chat(user_id, 'Apague todos os lançamentos.', monthly_limit=1)
    assert db.ai_get_pending_action(user_id)['tool_name'] == 'delete_all_launches'
    assert db.count_launches(user_id) == 1
    assert get_usage_this_month(user_id) == 1


def test_recusa_posterior_nao_apaga_marca_de_pendencia_anterior(monkeypatch, user_id):
    db.add_launch_and_update_balance(user_id, 'despesa', 25, 'Mercado', 'teste', categoria='alimentação')
    prepare_chat(monkeypatch, user_id, [
        ('delete_all_launches', {}), ('delete_launch', {'launch_id': '999999999'}),
    ], 'assistant')
    with pytest.raises(RuntimeError, match='salvar resultado'):
        runner.chat(user_id, 'Apague os registros.', monthly_limit=1)
    assert db.ai_get_pending_action(user_id)['tool_name'] == 'delete_all_launches'
    assert get_usage_this_month(user_id) == 1


@pytest.mark.parametrize('stage', ['validate', 'summary'])
def test_falha_antes_da_pendencia_restitui_reserva(monkeypatch, user_id, stage):
    db.add_launch_and_update_balance(user_id, 'despesa', 25, 'Mercado', 'teste', categoria='alimentação')
    prepare_chat(monkeypatch, user_id, [('delete_all_launches', {})], None)
    tool = runner.get_tool('delete_all_launches')

    def fail(*args):
        raise RuntimeError('falha antes da pendência')

    monkeypatch.setattr(runner, 'get_tool', lambda name: replace(tool, **{stage: fail}))
    with pytest.raises(RuntimeError, match='antes da pendência'):
        runner.chat(user_id, 'Apague todos os lançamentos.', monthly_limit=1)
    assert db.ai_get_pending_action(user_id) is None
    assert get_usage_this_month(user_id) == 0


def test_gravacao_da_pendencia_seguida_de_excecao_mantem_cota(monkeypatch, user_id):
    db.add_launch_and_update_balance(user_id, 'despesa', 25, 'Mercado', 'teste', categoria='alimentação')
    prepare_chat(monkeypatch, user_id, [('delete_all_launches', {})], None)
    set_pending = db.ai_set_pending_action

    def persist_then_fail(*args):
        set_pending(*args)
        raise RuntimeError('falha após gravar pendência')

    monkeypatch.setattr(db, 'ai_set_pending_action', persist_then_fail)
    with pytest.raises(RuntimeError, match='após gravar pendência'):
        runner.chat(user_id, 'Apague todos os lançamentos.', monthly_limit=1)
    assert db.ai_get_pending_action(user_id)['tool_name'] == 'delete_all_launches'
    assert db.count_launches(user_id) == 1
    assert get_usage_this_month(user_id) == 1
