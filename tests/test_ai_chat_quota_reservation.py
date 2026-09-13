"""A última vaga da cota deve ser reservada antes de executar ferramentas."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import pytest
import db
from core.services.ai_chat import runner


@pytest.mark.parametrize('failure_after_write', [False, True], ids=['sucesso', 'erro-apos-escrita'])
@pytest.mark.parametrize('confirmation', [False, True], ids=['alteracao-imediata', 'acao-pendente'])
def test_ultima_vaga_nao_executa_ferramenta_da_requisicao_rejeitada(monkeypatch, user_id, confirmation, failure_after_write):
    import openai
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute('insert into auth_accounts (user_id, email) values (%s, %s)', (user_id, f'quota-{user_id}@test.invalid'))
        conn.commit()

    # Ambas as requisições passam pelo precheck com a mesma leitura obsoleta.
    barrier = Barrier(2)
    def stale_usage(uid):
        barrier.wait(timeout=10)
        return 0
    monkeypatch.setattr(db, 'ai_get_usage_this_month', stale_usage)
    monkeypatch.setattr(db, 'ai_get_pending_action', lambda uid: None)
    monkeypatch.setenv('OPENAI_API_KEY', 'test-only')

    dispatches = []
    def execute(uid, args):
        db.add_launch_and_update_balance(uid, 'despesa', 25, 'Mercado', 'teste de cota', categoria='alimentação')
        if failure_after_write:
            raise RuntimeError('falha depois da gravação')
        return 'Despesa registrada.'
    tool = SimpleNamespace(is_write=True, requires_confirmation=confirmation,
                           validate=None, summary=lambda args: 'registrar despesa', execute=execute)
    monkeypatch.setattr(runner, 'get_tool', lambda name: tool)
    dispatch = runner._dispatch_tool
    def observed_dispatch(*args):
        dispatches.append(args)
        return dispatch(*args)
    monkeypatch.setattr(runner, '_dispatch_tool', observed_dispatch)

    class Call:
        def model_dump(self):
            return {'id': 'quota_call', 'type': 'function', 'function': {'name': 'quota_test_write', 'arguments': '{}'}}
    class Completions:
        def __init__(self):
            self.round = 0
        def create(self, **kwargs):
            self.round += 1
            if self.round > 1 and failure_after_write:
                raise RuntimeError('falha depois da pendência')
            message = SimpleNamespace(content=None, tool_calls=[Call()]) if self.round == 1 else SimpleNamespace(content='Confirma o registro?', tool_calls=[])
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])
    monkeypatch.setattr(openai, 'OpenAI', lambda **kw: SimpleNamespace(chat=SimpleNamespace(completions=Completions())))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: runner.chat(user_id, 'Registre uma despesa de 25 reais.', monthly_limit=1), range(2)))

    assert results.count(runner.LIMIT_MSG_TEMPLATE.format(limit=1)) == 1
    assert len(dispatches) == 1, 'a requisição sem cota não pode despachar uma ferramenta de escrita'
    assert len(db.list_launches(user_id, limit=10)) == (0 if confirmation else 1)
    history = db.ai_get_recent_messages(user_id, limit=20)
    assert sum(m['role'] == 'user' for m in history) == 1
    assert history[-1]['role'] == 'assistant'
    assert history[-1]['content'] == (runner.ERROR_MSG if failure_after_write else ('Confirma o registro?' if confirmation else 'Despesa registrada.'))
    from db.ai_chat import get_usage_this_month
    assert get_usage_this_month(user_id) == 1



def test_erro_sem_escrita_devolve_reserva(monkeypatch, user_id):
    import openai
    from db.ai_chat import get_usage_this_month
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute('insert into auth_accounts (user_id, email) values (%s, %s)', (user_id, f'quota-{user_id}@test.invalid'))
        conn.commit()
    monkeypatch.setenv('OPENAI_API_KEY', 'test-only')
    def fail(**kwargs):
        raise RuntimeError('modelo indisponível')
    monkeypatch.setattr(openai, 'OpenAI', lambda **kw: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fail))))
    assert runner.chat(user_id, 'Qual meu saldo?', monthly_limit=1) == runner.ERROR_MSG
    assert get_usage_this_month(user_id) == 0
    assert db.ai_get_recent_messages(user_id)[-1]['content'] == runner.ERROR_MSG


def test_restituicao_antiga_nao_desconta_mes_novo(user_id):
    from datetime import date
    from db.ai_chat import refund_usage
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute('insert into auth_accounts (user_id, email, ai_messages_this_month, ai_month_reset_at) values (%s, %s, 1, %s)',
                    (user_id, f'quota-{user_id}@test.invalid', date(2026, 10, 1)))
        conn.commit()
    refund_usage(user_id, date(2026, 9, 1))
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute('select ai_messages_this_month from auth_accounts where user_id=%s', (user_id,))
        assert cur.fetchone()['ai_messages_this_month'] == 1
