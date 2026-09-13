"""Reservas só podem ser restituídas nas contas que receberam o incremento."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest

import db
import db.ai_chat as quota


def add_account(user_id, count, suffix):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            'insert into auth_accounts (user_id, email, ai_messages_this_month, ai_month_reset_at) '
            'values (%s, %s, %s, %s) returning id',
            (user_id, f'quota-{user_id}-{suffix}@test.invalid', count, date.today().replace(day=1)),
        )
        account_id = cur.fetchone()['id']
        conn.commit()
    return account_id


def counters(user_id):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute('select ai_messages_this_month from auth_accounts where user_id=%s order by id', (user_id,))
        return [row['ai_messages_this_month'] for row in cur.fetchall()]


@pytest.mark.parametrize('initial', [(0, 1), (1, 0), (0, 0, 1)])
def test_restituicoes_repetidas_nao_liberam_cota_de_conta_nao_reservada(user_id, initial):
    for index, count in enumerate(initial):
        add_account(user_id, count, index)
    for _ in range(3):
        reservation = quota.reserve_usage(user_id, 1)
        assert reservation is not None
        assert counters(user_id) == [1] * len(initial)
        quota.refund_usage(user_id, reservation)
        assert counters(user_id) == list(initial)


def test_restituicao_nao_altera_conta_criada_depois_da_reserva(user_id):
    add_account(user_id, 0, 'original')
    reservation = quota.reserve_usage(user_id, 1)
    assert reservation is not None
    add_account(user_id, 1, 'nova')
    quota.refund_usage(user_id, reservation)
    assert counters(user_id) == [0, 1]


def test_restituicao_preserva_consumo_concorrente_do_especialista(user_id):
    add_account(user_id, 0, 'disponivel')
    add_account(user_id, 2, 'esgotada')
    reservation = quota.reserve_usage(user_id, 2)
    assert reservation is not None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: quota.try_consume_usage(user_id, 2), range(2)))
    assert sorted(result for result in results if result is not None) == [2]
    quota.refund_usage(user_id, reservation)
    assert counters(user_id) == [1, 2]
