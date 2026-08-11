"""Testes de renda variável (ações/FIIs via Open Finance) — db/rv.py.

Cobre: classificador pluggy_rv_kind, P&L de list_rv_positions e o resumo, além dos
casos de borda validados no sandbox do Pluggy (value null, quantity 0, CDB/ETF fora).
Não bate na rede (list_rv_positions só lê o banco).
"""
import pytest

import db
from db import get_conn
from db.rv import pluggy_rv_kind
from core.services.pluggy_sync import normalize_pluggy_investment


def _seed_connection(user_id: int) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """insert into open_finance_connections
                   (user_id, provider, provider_item_id, status, institution_id, institution_name)
                   values (%s,'pluggy','test-rv-item','ACTIVE','2','Pluggy Bank')
                   on conflict (user_id, provider, provider_item_id) do update set status='ACTIVE'
                   returning id""",
                (user_id,),
            )
            conn_id = cur.fetchone()["id"]
        conn.commit()
    return conn_id


def _seed(user_id: int, raws: list[dict]) -> int:
    conn_id = _seed_connection(user_id)
    db.save_open_finance_investments(conn_id, [normalize_pluggy_investment(r) for r in raws])
    return conn_id


# ── classificador (puro, sem DB) ─────────────────────────────────────────────
@pytest.mark.parametrize("t,st,expected", [
    ("EQUITY", "REAL_ESTATE_FUND", "fii"),
    ("EQUITY", "STOCK", "stock"),
    ("EQUITY", None, "stock"),          # ação sem subtype → stock
    ("ETF", "ETF", None),               # ETF fora do MVP
    ("MUTUAL_FUND", "INVESTMENT_FUND", None),
    ("SECURITY", "PGBL", None),
    ("FIXED_INCOME", "CDB", None),      # caixinha do Banqueiro, intocada
])
def test_pluggy_rv_kind(t, st, expected):
    assert pluggy_rv_kind(t, st) == expected


# ── P&L e filtro (com DB) ────────────────────────────────────────────────────
def test_fii_pnl_and_exclusions(user_id):
    _seed(user_id, [
        {"id": "of-ggrc", "name": "GGRC11", "code": "GGRC11", "isin": "BRGGRCCTF002",
         "type": "EQUITY", "subtype": "REAL_ESTATE_FUND", "quantity": 100, "value": 118.40,
         "amount": 10000.0, "balance": 11840.0, "currencyCode": "BRL", "lastMonthRate": 1.2},
        {"id": "of-cdb", "name": "Caixinha", "type": "FIXED_INCOME", "subtype": "CDB",
         "amount": 500.0, "balance": 500.0},
        {"id": "of-etf", "name": "BOVA11", "code": "BOVA11", "type": "ETF", "subtype": "ETF",
         "quantity": 3, "value": 100.0, "amount": 300.0, "balance": 330.0},
    ])
    pos = db.list_rv_positions(user_id)
    assert {p["ticker"] for p in pos} == {"GGRC11"}  # CDB e ETF fora
    g = pos[0]
    assert g["kind"] == "fii"
    assert g["market_value"] == pytest.approx(11840.0)
    assert g["invested"] == pytest.approx(10000.0)
    assert g["pnl"] == pytest.approx(1840.0)
    assert g["pnl_pct"] == pytest.approx(0.184)
    assert g["market_price"] == pytest.approx(118.40)
    assert g["quantity"] == pytest.approx(100.0)

    summ = db.rv_portfolio_summary(user_id)
    assert summ["count"] == 1
    assert summ["pnl"] == pytest.approx(1840.0)


def test_value_null_falls_back_to_balance_over_qty(user_id):
    # ISUS11 real do sandbox: value=null, quantity=1 → market_price = balance/quantity
    _seed(user_id, [
        {"id": "of-acao", "name": "ITUB4", "code": "ITUB4", "type": "EQUITY", "subtype": "STOCK",
         "quantity": 10, "value": None, "amount": 300.0, "balance": 320.0, "currencyCode": "BRL"},
    ])
    pos = db.list_rv_positions(user_id)
    assert len(pos) == 1
    assert pos[0]["kind"] == "stock"
    assert pos[0]["market_price"] == pytest.approx(32.0)  # 320/10


def test_zero_market_value_is_hidden(user_id):
    # posição vendida/zerada (balance 0) não aparece
    _seed(user_id, [
        {"id": "of-zero", "name": "MGLU3", "code": "MGLU3", "type": "EQUITY", "subtype": "STOCK",
         "quantity": 0, "value": 5.0, "amount": 100.0, "balance": 0.0},
    ])
    assert db.list_rv_positions(user_id) == []


def test_no_of_data_returns_empty(user_id):
    assert db.list_rv_positions(user_id) == []
    assert db.rv_portfolio_summary(user_id)["count"] == 0
