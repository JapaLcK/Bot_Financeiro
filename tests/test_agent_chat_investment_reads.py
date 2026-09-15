"""Consultas dos especialistas não carregam lotes que não serão utilizados."""
from decimal import Decimal

import pytest

import db
import db.investments as investments
from core.services import agent_chat as chat


@pytest.mark.parametrize("kind", ["faria_limer", "barao"])
def test_snapshot_nao_busca_lotes_dos_investimentos(user_id, monkeypatch, kind):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into investments (user_id, name, balance, rate, period, last_date) "
            "values (%s, 'CDB com muitos aportes', 2500.25, 0.9, 'monthly', '2026-09-01') returning id",
            (user_id,))
        investment_id = cur.fetchone()["id"]
        cur.execute(
            "insert into investment_lots "
            "(user_id, investment_id, principal_initial, principal_remaining, balance, opened_at, last_date) "
            "select %s, %s, 10.001, 10.001, 10.001, '2026-09-01'::date, '2026-09-01'::date "
            "from generate_series(1, 250)", (user_id, investment_id))
        conn.commit()

    def forbidden_lot_fetch(*args):
        pytest.fail("O snapshot consultou e materializou lotes que não usa")

    monkeypatch.setattr(investments, "_fetch_lots_for_investments", forbidden_lot_fetch)
    result = chat.execute_read(user_id, kind, "consultar_dados_do_agente", {})

    field = "renda_fixa_manual" if kind == "faria_limer" else "investimentos_manuais"
    assert len(result[field]) == 1
    assert result[field][0]["balance"] == Decimal("2500.25")
    assert "lots" not in result[field][0]
    assert result["cobertura"][field] == {"total": 1, "incluidos": 1, "truncado": False}
    if kind == "faria_limer":
        assert result["resumo_renda_fixa_manual_brl"] == {"balance": Decimal("2500.25"), "count": 1}
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) as count, sum(balance) as balance from investment_lots where user_id=%s and investment_id=%s",
                    (user_id, investment_id))
        assert dict(cur.fetchone()) == {"count": 250, "balance": Decimal("2500.25")}
