"""Migração do #772: fatura com `user_id` NULL ganha o dono do CARTÃO e a coluna vira NOT NULL.

Roda o MESMO SQL do boot (`CREDIT_BILLS_USER_ID_NOT_NULL_SQL`) numa transação só, desfeita
no fim (o DDL do Postgres é transacional): a coluna volta a aceitar NULL, duas faturas
(de A e de B) perdem o dono e a migração devolve cada uma ao dono do próprio cartão.
"""
from datetime import date

import psycopg
import pytest

import db
from conftest import usuario_pagante
from db.connection import get_conn
from db.schema import CREDIT_BILLS_USER_ID_NOT_NULL_SQL


def _fatura(uid: int, nome: str) -> int:
    cartao = db.create_card(uid, nome, closing_day=10, due_day=17)
    return db.add_credit_purchase(uid, cartao, 10, "mercado", nome, date.today())[2]


def test_backfill_pelo_dono_do_cartao_e_coluna_not_null():
    a, b = usuario_pagante(), usuario_pagante()
    fa, fb, fc = _fatura(a, "Nubank"), _fatura(b, "Nubank"), _fatura(a, "Inter")
    donos_sql = "select id, user_id from credit_bills where id = any(%s)"
    nula_sql = ("select is_nullable from information_schema.columns where table_schema = 'public'"
                " and table_name = 'credit_bills' and column_name = 'user_id'")
    with get_conn() as conn, conn.cursor() as cur:
        try:
            cur.execute("alter table credit_bills alter column user_id drop not null")
            cur.execute("update credit_bills set user_id = null where id = any(%s)", ([fa, fb],))
            cur.execute(CREDIT_BILLS_USER_ID_NOT_NULL_SQL)
            cur.execute(donos_sql, ([fa, fb, fc],))
            assert {r["id"]: r["user_id"] for r in cur.fetchall()} == {fa: a, fb: b, fc: a}
            cur.execute(nula_sql)
            assert cur.fetchone()["is_nullable"] == "NO"

            cur.execute(CREDIT_BILLS_USER_ID_NOT_NULL_SQL)  # 2º boot: não faz nada
            cur.execute(donos_sql, ([fa, fb, fc],))
            assert {r["id"]: r["user_id"] for r in cur.fetchall()} == {fa: a, fb: b, fc: a}

            cur.execute("savepoint sem_dono")
            with pytest.raises(psycopg.errors.NotNullViolation):
                cur.execute("insert into credit_bills (user_id, card_id, period_start, period_end)"
                            " select null, card_id, period_start + 1, period_end + 1"
                            " from credit_bills where id = %s", (fa,))
            cur.execute("rollback to savepoint sem_dono")
        finally:
            conn.rollback()
