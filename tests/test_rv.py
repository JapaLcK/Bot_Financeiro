"""Testes de renda variável (ações/FIIs via Open Finance) — db/rv.py.

Cobre: classificador pluggy_rv_kind, P&L de list_rv_positions e o resumo, além dos
casos de borda validados no sandbox do Pluggy (value null, quantity 0, CDB/ETF fora).
Não bate na rede (list_rv_positions só lê o banco).
"""
import pytest

import db
from db import get_conn
from db.rv import _custo, pluggy_rv_kind
from core.services.pluggy_sync import normalize_pluggy_investment


def _seed_connection(user_id: int) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """insert into open_finance_connections
                   (user_id, provider, provider_item_id, status, institution_id, institution_name)
                   values (%s,'pluggy','test-rv-item-' || %s::text,'ACTIVE','2','Pluggy Bank')
                   on conflict (user_id, provider, provider_item_id) do update set status='ACTIVE'
                   returning id""",
                (user_id, user_id),
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
         "amountOriginal": 10000.0, "amount": 11840.0, "balance": 11840.0,
         "currencyCode": "BRL", "lastMonthRate": 1.2},
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


ACAO_SEM_CUSTO = {"id": "of-sem", "name": "PETR4", "code": "PETR4", "type": "EQUITY",
                  "subtype": "STOCK", "quantity": 10, "value": 100.0, "amount": 1000.0,
                  "balance": 1000.0, "currencyCode": "BRL"}  # amount == balance, sem amountOriginal


def test_equity_sem_amount_original_tem_custo_desconhecido(user_id):
    # Produção: `amount` é o saldo, não o custo. Sem amountOriginal => None, nunca 0.
    _seed(user_id, [ACAO_SEM_CUSTO])
    p = db.list_rv_positions(user_id)[0]
    assert p["cost_known"] is False
    assert (p["invested"], p["pnl"], p["pnl_pct"], p["avg_price"]) == (None, None, None, None)
    s = db.rv_portfolio_summary(user_id)
    assert (s["invested"], s["pnl"], s["pnl_pct"], s["cost_known"]) == (None, None, None, False)


def test_rv_misto_resumo_nulo_e_posicao_com_custo_mantida(user_id):
    _seed(user_id, [ACAO_SEM_CUSTO,
        {"id": "of-fii", "name": "GGRC11", "code": "GGRC11", "type": "EQUITY", "subtype": "REAL_ESTATE_FUND",
         "quantity": 100, "value": 118.40, "amountOriginal": 10000.0, "amount": 11840.0, "balance": 11840.0}])
    pos = {p["ticker"]: p for p in db.list_rv_positions(user_id)}
    assert pos["GGRC11"]["pnl"] == pytest.approx(1840.0) and pos["GGRC11"]["avg_price"] == pytest.approx(100.0)
    assert pos["PETR4"]["pnl"] is None
    s = db.rv_portfolio_summary(user_id)
    assert (s["invested"], s["pnl"], s["cost_known"]) == (None, None, False)
    assert s["market_value"] == pytest.approx(12840.0)


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


# ── Renda fixa do banco (CDB/Tesouro) agregada — não vira caixinha ────────────
def test_of_fixed_income_aggregated_and_clean(user_id):
    NU = "CDB - NU FINANCEIRA S.A. - SOCIEDADE DE CREDITO, FINANCIAMENTO E INVESTIMENTO"
    _seed(user_id, [
        {"id": "cdb1", "name": NU, "type": "FIXED_INCOME", "subtype": "CDB", "balance": 1000.0, "amount": 1000.0, "amountOriginal": 950.0},
        {"id": "cdb2", "name": NU, "type": "FIXED_INCOME", "subtype": "CDB", "balance": 2000.0, "amount": 1950.0, "amountOriginal": 1900.0},
        {"id": "tes1", "name": "Tesouro IPCA+ 2032", "type": "FIXED_INCOME", "subtype": "TREASURY", "balance": 500.0, "amount": 500.0, "amountOriginal": 480.0},
        {"id": "cdb0", "name": NU, "type": "FIXED_INCOME", "subtype": "CDB", "balance": 0.0, "amount": 0.0},  # zero → fora
        {"id": "eq1", "name": "PETR4", "code": "PETR4", "type": "EQUITY", "subtype": "STOCK", "balance": 900.0, "amount": 1000.0},  # RV, não RF
    ])
    items = db.list_of_fixed_income(user_id)
    by = {i["name"]: i for i in items}
    assert "CDB · Nu Financeira" in by            # nome jurídico → limpo
    assert by["CDB · Nu Financeira"]["count"] == 2  # os 2 com saldo (o zero fora)
    assert by["CDB · Nu Financeira"]["balance"] == pytest.approx(3000.0)
    assert by["CDB · Nu Financeira"]["pnl"] == pytest.approx(150.0)  # 3000 - 2850
    assert "Tesouro IPCA+ 2032" in by
    assert "PETR4" not in by                       # equity não é renda fixa
    summ = db.of_fixed_income_summary(user_id)
    assert summ["balance"] == pytest.approx(3500.0)
    assert summ["pnl"] == pytest.approx(170.0)   # 3500 - (950+1900+480)
    assert summ["cost_known"] is True


def test_of_fixed_income_sem_custo_e_amount_original_invalido(user_id):
    _seed(user_id, [
        {"id": "a", "name": "Tesouro Selic 2029", "type": "FIXED_INCOME", "subtype": "TREASURY",
         "balance": 500.0, "amount": 500.0},                                   # sem amountOriginal
        {"id": "b", "name": "Tesouro IPCA+ 2032", "type": "FIXED_INCOME", "subtype": "TREASURY",
         "balance": 900.0, "amountOriginal": "1,2"},                           # lixo: não quebra a query
        {"id": "c", "name": "CDB Banco X", "type": "FIXED_INCOME", "subtype": "CDB",
         "balance": 1100.0, "amountOriginal": 1000.0},
    ])
    by = {i["name"]: i for i in db.list_of_fixed_income(user_id)}
    for nome in ("Tesouro Selic 2029", "Tesouro IPCA+ 2032"):
        assert (by[nome]["cost_known"], by[nome]["invested"], by[nome]["pnl"], by[nome]["pnl_pct"]) == (False, None, None, None)
    assert by["CDB"]["pnl"] == pytest.approx(100.0)   # grupo com custo mantém o seu
    s = db.of_fixed_income_summary(user_id)
    assert (s["invested"], s["pnl"], s["cost_known"]) == (None, None, False)
    assert s["balance"] == pytest.approx(2500.0)


def test_dashboard_payload_rv_sem_custo_nulo_e_rf_com_custo(pro_user_id):
    import asyncio
    from frontend import finance_bot_websocket_custom as dashboard
    _seed(pro_user_id, [ACAO_SEM_CUSTO,
        {"id": "cdb", "name": "CDB Banco X", "type": "FIXED_INCOME", "subtype": "CDB",
         "balance": 1100.0, "amount": 1100.0, "amountOriginal": 1000.0}])
    d = asyncio.run(dashboard.get_financial_data(pro_user_id))
    assert d["rv_summary"]["invested"] is None and d["rv_summary"]["pnl"] is None
    assert d["of_fixed_income_summary"]["pnl"] == pytest.approx(100.0)


@pytest.mark.parametrize("x", [0, -5, "NaN", "Infinity", "1e400", 1e400, "1,2", True, None, "", "1e3", " 1000 ", 10**15, 10**400])
def test_custo_invalido_e_desconhecido(x):
    assert _custo({"amountOriginal": x}) is None


@pytest.mark.parametrize("raw", [None, 5, "x", [], {}])
def test_custo_raw_que_nao_e_dict_com_chave(raw):
    assert _custo(raw) is None


@pytest.mark.parametrize("x", [1000, "1000"])
def test_custo_valido(x):
    assert _custo({"amountOriginal": x}) == 1000.0


def test_isolamento_entre_usuarios_rv_e_renda_fixa(user_id):
    import uuid
    from db.users import ensure_user
    b = int(uuid.uuid4().int % 10_000_000_000)
    ensure_user(b)  # o autouse `_auto_cleanup_orphan_users` apaga b no fim
    def raws(tag, valor):
        return [{"id": f"eq-{tag}", "name": f"AC{tag}", "code": f"AC{tag}", "type": "EQUITY", "subtype": "STOCK",
                 "quantity": 1, "value": valor, "balance": valor, "amountOriginal": valor / 2},
                {"id": f"rf-{tag}", "name": f"Tesouro {tag}", "type": "FIXED_INCOME", "subtype": "TREASURY",
                 "balance": valor, "amountOriginal": valor / 2}]
    _seed(user_id, raws("A", 1000.0))
    _seed(b, raws("B", 2000.0))
    # caixinha do B vinculada à posição de renda fixa do A: não pode escondê-la
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""select i.id from open_finance_investments i
                           join open_finance_connections c on c.id = i.connection_id
                           where c.user_id = %s and i.type = 'FIXED_INCOME'""", (user_id,))
            of_a = cur.fetchone()["id"]
            cur.execute("insert into pockets(user_id,name,balance,source,of_investment_id) "
                        "values(%s,'cx-b',0,'open_finance',%s)", (b, of_a))
        conn.commit()
    assert [p["ticker"] for p in db.list_rv_positions(user_id)] == ["ACA"]
    assert [p["ticker"] for p in db.list_rv_positions(b)] == ["ACB"]
    assert [i["name"] for i in db.list_of_fixed_income(user_id)] == ["Tesouro A"]
    assert [i["name"] for i in db.list_of_fixed_income(b)] == ["Tesouro B"]


def test_of_fixed_income_grupo_do_mesmo_nome_e_nulo_se_um_membro_sem_custo(user_id):
    # dois grupos, o membro sem custo ora último ora primeiro: não depende da ordem das linhas
    NU = "CDB - NU FINANCEIRA S.A. - SOCIEDADE DE CREDITO, FINANCIAMENTO E INVESTIMENTO"
    com = {"type": "FIXED_INCOME", "subtype": "CDB", "balance": 1000.0, "amountOriginal": 900.0}
    sem = {"type": "FIXED_INCOME", "subtype": "CDB", "balance": 2000.0, "amount": 2000.0}
    _seed(user_id, [
        {"id": "g1", "name": NU, **com}, {"id": "g2", "name": NU, **sem},
        {"id": "h1", "name": "Tesouro Selic 2029", **sem}, {"id": "h2", "name": "Tesouro Selic 2029", **com},
    ])
    grupos = db.list_of_fixed_income(user_id)
    assert len(grupos) == 2
    for g in grupos:
        assert g["count"] == 2 and g["balance"] == pytest.approx(3000.0)
        assert (g["cost_known"], g["invested"], g["pnl"], g["pnl_pct"]) == (False, None, None, None)
    s = db.of_fixed_income_summary(user_id)
    assert (s["invested"], s["pnl"], s["cost_known"]) == (None, None, False)
