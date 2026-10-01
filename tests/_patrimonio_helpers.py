"""Molde dos testes da foto do patrimônio (tests/test_patrimonio_foto*.py).

Espelho do Open Finance escrito direto no banco: cada teste controla status,
datas de sync, moeda e `raw` sem passar pelo sync da Pluggy.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from psycopg.types.json import Jsonb

import db
from db.connection import get_conn
from db.patrimonio import calcular

AGORA = datetime.now(timezone.utc)


def q(sql, a=(), fetch=False):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, a)
        r = cur.fetchall() if fetch else (cur.fetchone() if cur.description else None)
        conn.commit()
    return r


def conexao(uid, item, status="UPDATED", sync=AGORA, tentativa=None) -> int:
    return q("""insert into open_finance_connections (user_id, provider, provider_item_id, status,
                    institution_id, institution_name, last_sync_at, last_attempt_at)
                values (%s, 'pluggy', %s, %s, '1', 'Banco', %s, %s) returning id""",
             (uid, item, status, sync, tentativa))["id"]


def conta(cid, pid, saldo, moeda="BRL", code="BRL") -> int:
    return q("""insert into open_finance_accounts (connection_id, provider_account_id, name, type,
                    currency, balance, raw) values (%s, %s, 'Conta', 'BANK', %s, %s, %s) returning id""",
             (cid, pid, moeda, saldo, Jsonb({"currencyCode": code} if code else {})))["id"]


def posicao(cid, pid, saldo, moeda="BRL", status=None, code="BRL") -> int:
    raw = {k: v for k, v in (("currencyCode", code), ("status", status)) if v}
    raw["balance"] = None if saldo is None else float(saldo)  # a Pluggy manda número
    return q("""insert into open_finance_investments (connection_id, provider_investment_id, name,
                    type, currency, balance, raw) values (%s, %s, 'CDB', 'FIXED_INCOME', %s, %s, %s)
                returning id""", (cid, pid, moeda, saldo, Jsonb(raw)))["id"]


def caixinha(uid, nome, saldo, of_investment_id=None):
    q("insert into pockets (user_id, name, balance, of_investment_id) values (%s, %s, %s, %s)",
      (uid, nome, saldo, of_investment_id))


def investimento_manual(uid, nome, saldo):
    q("""insert into investments (user_id, name, balance, rate, period, last_date)
         values (%s, %s, %s, 1, 'cdi', current_date)""", (uid, nome, saldo))


def tx_banco(account_id, ident, valor, **ligacao) -> int:
    cols = "".join(f", {k}" for k in ligacao)
    vals = "".join(", %s" for _ in ligacao)
    return q(f"""insert into open_finance_transactions (account_id, provider_transaction_id,
                     description, amount, transaction_date{cols})
                 values (%s, %s, 'Mercado', %s, current_date{vals}) returning id""",
             (account_id, ident, valor, *ligacao.values()))["id"]


def lancamento(uid, valor=30) -> int:
    return db.add_launch_and_update_balance(uid, "despesa", valor, "mercado", None)[0]


def foto(uid):
    with get_conn() as conn, conn.cursor() as cur:
        f = calcular(cur, uid)
        conn.rollback()
    return f


def horas_atras(h):
    return AGORA - timedelta(hours=h)
