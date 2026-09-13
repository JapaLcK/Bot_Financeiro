"""Consultas que alteram faturas não podem restituir a cota após uma falha."""
from datetime import date
from types import SimpleNamespace

import pytest

import db
from db.ai_chat import get_usage_this_month
from core.services.ai_chat import runner


@pytest.mark.parametrize('tool_name', [
    'get_open_bill', 'get_total_debt', 'forecast_next_bill',
])
@pytest.mark.parametrize('failure', ['modelo', 'historico'])
def test_falha_apos_consulta_que_altera_fatura_mantem_cota(monkeypatch, user_id, tool_name, failure):
    import openai

    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute('insert into auth_accounts (user_id, email) values (%s, %s)',
                    (user_id, f'card-quota-{user_id}@test.invalid'))
        conn.commit()
    card_id = db.create_card(user_id, 'Nubank', closing_day=10, due_day=17)
    if tool_name != 'get_open_bill':
        bill_id = db.get_or_create_open_bill(user_id, card_id, date.today())
        # Simula a fatura paga que voltou a ter saldo antes da reconciliação.
        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute("update credit_bills set total=50, paid_amount=0, status='paid', paid_at=now() where id=%s",
                        (bill_id,))
            conn.commit()

    class Call:
        def model_dump(self):
            return {
                'id': 'card_query', 'type': 'function',
                'function': {
                    'name': tool_name,
                    'arguments': '{}' if tool_name == 'get_total_debt' else '{"card_name":"Nubank"}',
                },
            }

    class Completions:
        def __init__(self):
            self.round = 0

        def create(self, **kwargs):
            self.round += 1
            if self.round == 1:
                message = SimpleNamespace(content=None, tool_calls=[Call()])
            elif failure == 'modelo':
                raise RuntimeError('falha do modelo após consulta')
            else:
                message = SimpleNamespace(content='Consulta concluída.', tool_calls=[])
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    monkeypatch.setenv('OPENAI_API_KEY', 'test-only')
    monkeypatch.setattr(openai, 'OpenAI', lambda **kw: SimpleNamespace(chat=SimpleNamespace(completions=Completions())))
    if failure == 'historico':
        append = db.ai_append_message

        def persist(uid, role, content, **kwargs):
            if role == 'assistant' and content == 'Consulta concluída.':
                raise RuntimeError('falha no histórico após consulta')
            return append(uid, role, content, **kwargs)

        monkeypatch.setattr(db, 'ai_append_message', persist)
        with pytest.raises(RuntimeError, match='histórico após consulta'):
            runner.chat(user_id, 'Consulte minhas faturas.', monthly_limit=1)
    else:
        assert runner.chat(user_id, 'Consulte minhas faturas.', monthly_limit=1) == runner.ERROR_MSG

    # Lê diretamente para verificar o commit sem provocar outra reconciliação.
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute('select status, paid_at from credit_bills where user_id=%s and card_id=%s',
                    (user_id, card_id))
        bills = cur.fetchall()
    assert len(bills) == 1
    assert bills[0]['status'] == 'open'
    assert bills[0]['paid_at'] is None
    assert get_usage_this_month(user_id) == 1, 'a fatura já foi alterada; não se pode devolver a reserva'
