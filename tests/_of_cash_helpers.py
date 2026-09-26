"""Molde comum dos testes de saque/depósito em espécie do Open Finance (Q41).

Sem prefixo `test_`: o pytest não coleta. O sync é o de produção
(`save_open_finance_sync` → import → correções, a ordem de
core/services/pluggy_sync.py), com Postgres real.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

import db
from db.connection import get_conn

ATIVACAO = datetime(2026, 1, 1, 12, 0)
CONECTADO = datetime(2026, 2, 1, 12, 0)


@pytest.fixture
def caixa(monkeypatch):
    """Switch ligado e data de ativação fixa (a tabela é de uma linha só)."""
    monkeypatch.setenv("OF_CASH_ENABLED", "1")
    q("insert into of_cash_activation(activated_at) values (%s) "
      "on conflict (id) do update set activated_at = excluded.activated_at", (ATIVACAO,))


def q(sql, params=(), fetch=False):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall() if fetch else None
        conn.commit()
    return rows


def conecta(uid, item, desde=CONECTADO, instituicao=612, nome="Nubank"):
    """Conexão nova com `created_at` no passado (a data da 1ª conexão é o corte)."""
    conn_id = db.save_pluggy_open_finance_item(uid, {
        "id": item, "connector": {"id": instituicao, "name": nome}, "status": "UPDATED",
    })["id"]
    q("update open_finance_connections set created_at=%s where id=%s and user_id=%s", (desde, conn_id, uid))
    return conn_id


def sync(conn_id, uid, txs, saldo="1000", numero="0001-9", conta="acc-1"):
    """Um ciclo do sync de produção sobre um espelho autoritativo."""
    db.save_open_finance_sync(conn_id, [{
        "provider_account_id": f"{conta}-{conn_id}", "name": "Conta", "type": "BANK",
        "subtype": "CHECKING_ACCOUNT", "currency": "BRL", "balance": Decimal(saldo),
        "raw": {"number": numero}, "transactions": list(txs),
    }])
    db.import_open_finance_launches(uid, conn_id)
    db.sync_imported_open_finance_updates(uid, conn_id)


def tx(ident, valor, dia, op="SAQUE", desc="Same person transfer - CASH", pid=None, category=None):
    """Transação do extrato. `ident` é o id da Pluggy; `pid` o providerId do banco."""
    raw = {"operationType": op}
    if pid is not False:
        raw["providerId"] = pid or f"prov-{ident}"
    return {"provider_transaction_id": ident, "description": desc, "amount": Decimal(str(valor)),
            "transaction_date": dia, "transacted_at": None,
            "category": category or ("Same person transfer - CASH" if op == "SAQUE" else "Transfers"),
            "raw": raw}


def carteira(uid) -> Decimal:
    return Decimal(str(q("select balance from accounts where user_id=%s", (uid,), True)[0]["balance"]))


def links(uid) -> list[dict]:
    return [dict(r) for r in q("select * from of_cash_links where user_id=%s order by id", (uid,), True)]


def launches_visiveis(uid) -> list[dict]:
    """O que entra em gasto/receita: lançamentos NÃO internos."""
    return q("select id, tipo, valor, categoria, source from launches "
             "where user_id=%s and not is_internal_movement", (uid,), True)


def dia(d: int, mes: int = 3) -> date:
    return date(2026, mes, d)
