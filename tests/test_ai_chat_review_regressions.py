"""Regressões da revisão: reset concorrente, consultas puras e persistência."""
from datetime import date

import pytest
import db
import db.ai_quota as quota
from core.services import agent_chat
from core.services.ai_chat import runner


def account(uid, month=None):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute('insert into auth_accounts (user_id, email, ai_month_reset_at, ai_messages_this_month) values (%s,%s,%s,0)',
                    (uid, f'review-{uid}@test.invalid', month))
        conn.commit()


def test_leitura_atrasada_do_mes_nao_apaga_reserva(monkeypatch, user_id):
    account(user_id, date(2026, 8, 1))
    monkeypatch.setattr(quota, '_current_month_start', lambda: date(2026, 9, 1))
    original = quota.get_conn
    opened = 0
    reserved = []
    # Pausa lógica exata: depois de ler a linha antiga, outra conexão reserva.
    class Cursor:
        def __init__(self, cur): self.cur = cur
        def __enter__(self): self.cur.__enter__(); return self
        def __exit__(self, *args): return self.cur.__exit__(*args)
        def execute(self, *args): return self.cur.execute(*args)
        def fetchone(self):
            row = self.cur.fetchone()
            reserved.append(quota.reserve_usage(user_id, 1))
            return row
    class Connection:
        def __init__(self): self.manager = original()
        def __enter__(self): self.conn = self.manager.__enter__(); return self
        def __exit__(self, *args): return self.manager.__exit__(*args)
        def cursor(self): return Cursor(self.conn.cursor())
        def commit(self): return self.conn.commit()
    def connection():
        nonlocal opened
        opened += 1
        return Connection() if opened == 1 else original()
    monkeypatch.setattr(quota, 'get_conn', connection)
    quota.get_usage_this_month(user_id)
    assert len(reserved) == 1
    assert reserved[0].month == date(2026, 9, 1)
    assert quota.reserve_usage(user_id, 1) is None, 'leitura atrasada não pode reabrir a última vaga'


def test_carteiro_consulta_sem_gerar_instancias(monkeypatch):
    import db.bills
    from core.services import recurring_charger
    syncs = []
    monkeypatch.setattr(recurring_charger, 'sync_manual_bills_once', lambda *a: syncs.append(a))
    monkeypatch.setattr(db.bills, 'list_bills', lambda *a, **kw: [])
    result = agent_chat.execute_read(42, 'carteiro', 'get_bills_to_pay', {})
    assert result['count_pending'] == 0
    assert syncs == []


def test_banqueiro_consulta_sem_aplicar_juros(monkeypatch):
    accruals = []
    def pockets(uid, *, accrue=True):
        accruals.append(accrue)
        return []
    monkeypatch.setattr(db, 'list_pockets', pockets)
    assert agent_chat.execute_read(42, 'cofre', 'list_pockets', {})['pockets'] == []
    assert accruals == [False]


@pytest.mark.parametrize('write_attempted', [False, True])
def test_falha_ao_persistir_resposta_restitui_apenas_sem_escrita(monkeypatch, user_id, write_attempted):
    import openai
    account(user_id)
    monkeypatch.setenv('OPENAI_API_KEY', 'test-only')
    monkeypatch.setattr(openai, 'OpenAI', lambda **kw: object())
    def answer(*args):
        if write_attempted:
            runner._TURN_WRITE_ATTEMPTED.set(True)
        return 'Resposta pronta.'
    monkeypatch.setattr(runner, '_run_tool_loop', answer)
    append = db.ai_append_message
    def persist(uid, role, text, **kwargs):
        if role == 'assistant': raise RuntimeError('falha no histórico')
        return append(uid, role, text, **kwargs)
    monkeypatch.setattr(db, 'ai_append_message', persist)
    with pytest.raises(RuntimeError, match='histórico'):
        runner.chat(user_id, 'Como está meu saldo?', monthly_limit=1)
    assert quota.get_usage_this_month(user_id) == (1 if write_attempted else 0)


def test_reserva_de_mes_antigo_nao_regride_contador(monkeypatch, user_id):
    account(user_id, date(2026, 10, 1))
    monkeypatch.setattr(quota, '_current_month_start', lambda: date(2026, 9, 1))
    assert quota.reserve_usage(user_id, 1) is None
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute('select ai_month_reset_at from auth_accounts where user_id=%s', (user_id,))
        assert cur.fetchone()['ai_month_reset_at'] == date(2026, 10, 1)


def test_chat_geral_nao_restitui_apos_sincronizacao_indireta(monkeypatch, user_id):
    import openai
    import db.bills
    from core.services import recurring_charger
    account(user_id)
    syncs = []
    monkeypatch.setattr(recurring_charger, 'sync_manual_bills_once', lambda *args: syncs.append(args))
    monkeypatch.setattr(db.bills, 'list_bills', lambda *args, **kwargs: [])
    monkeypatch.setenv('OPENAI_API_KEY', 'test-only')
    monkeypatch.setattr(openai, 'OpenAI', lambda **kw: object())
    def answer(client, uid, messages):
        runner._dispatch_tool(uid, 'get_bills_to_pay', {})
        return runner.ERROR_MSG
    monkeypatch.setattr(runner, '_run_tool_loop', answer)
    assert runner.chat(user_id, 'Quais contas vencem?', monthly_limit=1) == runner.ERROR_MSG
    assert len(syncs) == 1
    assert quota.get_usage_this_month(user_id) == 1


@pytest.mark.parametrize('kind,name,args', [
    ('carteiro', 'get_bills_to_pay', {'sync': True}),
    ('cofre', 'list_pockets', {'accrue': True}),
])
def test_modelo_nao_pode_habilitar_gravacoes_nas_consultas(monkeypatch, kind, name, args):
    import db.bills
    from core.services import recurring_charger
    calls = []
    monkeypatch.setattr(recurring_charger, 'sync_manual_bills_once', lambda *a: calls.append('sync'))
    monkeypatch.setattr(db.bills, 'list_bills', lambda *a, **kw: [])
    monkeypatch.setattr(db, 'list_pockets', lambda uid, *, accrue=True: calls.append(accrue) or [])
    agent_chat.execute_read(42, kind, name, args)
    assert calls == ([] if kind == 'carteiro' else [False])
