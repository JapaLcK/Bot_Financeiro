"""Snapshots de carteira limitam detalhes sem transformar uma amostra em total."""
import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

import db
from core.services import agent_chat as chat


def manual_investments(count):
    return [
        {"id": i, "name": f"CDB {i}", "balance": Decimal("10.01"),
         "last_date": date(2026, 9, 1), "rate": Decimal("0.9"), "period": "monthly",
         "indexer": "prefixado", "lots": [{"id": lot} for lot in range(150)]}
        for i in range(count)
    ]


@pytest.fixture
def portfolios(monkeypatch):
    def prepare(count):
        manual = manual_investments(count)
        positions = [
            {"ticker": f"ATIVO{i}", "market_value": 20, "invested": 15,
             "currency": "BRL", "cost_known": True}
            for i in range(count)
        ]
        fixed_income = [{"name": f"Tesouro {i}", "balance": 30, "invested": 25,
                         "count": 1} for i in range(count)]
        monkeypatch.setattr(db, "list_investments", lambda uid: manual)
        monkeypatch.setattr(db, "list_rv_positions", lambda uid: positions)
        monkeypatch.setattr(db, "list_of_fixed_income", lambda uid, *, currency=None: fixed_income)
        monkeypatch.setattr(db, "of_fixed_income_summary", lambda uid, *, currency=None: {
            "balance": count * 30, "invested": count * 25, "count": count})
        monkeypatch.setattr(db, "accrue_all_investments", lambda *a: pytest.fail("aplicou juros"))
        return manual
    return prepare


def test_modelo_recebe_no_maximo_100_investimentos_manuais(portfolios):
    portfolios(137)

    class Call:
        id = "snapshot"
        function = SimpleNamespace(name="consultar_dados_do_agente", arguments="{}")

        def model_dump(self):
            return {"id": self.id, "type": "function", "function": {
                "name": self.function.name, "arguments": self.function.arguments}}

    responses = iter([
        SimpleNamespace(content=None, tool_calls=[Call()]),
        SimpleNamespace(content="A carteira cadastrada inclui renda fixa e variável.", tool_calls=[]),
        SimpleNamespace(content='{"valid": true}'),
    ])
    payloads = []

    def create(**kwargs):
        payloads.extend(json.loads(m["content"]) for m in kwargs["messages"] if m["role"] == "tool")
        return SimpleNamespace(choices=[SimpleNamespace(message=next(responses))])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    chat._answer(client, 42, "faria_limer", "Como está a composição da minha carteira?", [])
    assert len(payloads) == 1
    assert len(payloads[0]["renda_fixa_manual"]) == 100


@pytest.mark.parametrize("count", [0, 100, 101, 250])
def test_cobertura_e_totais_integrais_do_faria_limer(portfolios, count):
    portfolios(count)
    result = chat.execute_read(42, "faria_limer", "consultar_dados_do_agente", {})
    assert len(result["renda_fixa_manual"]) == min(count, 100)
    assert len(result["posicoes"]) == min(count, 100)
    assert result["resumo_brl"]["count"] == count
    assert result["resumo_brl"]["market_value"] == count * 20
    assert result["resumo_renda_fixa_manual_brl"] == {
        "count": count, "balance": Decimal("10.01") * count}
    assert result["cobertura"] == {
        field: {"total": count, "incluidos": min(count, 100), "truncado": count > 100}
        for field in ("posicoes", "renda_fixa_manual")
    }
    assert "Não some as listas parciais" in result["nota"]


def test_barao_limita_detalhes_e_informa_cobertura_sem_serializar_lotes(portfolios):
    manual = portfolios(137)
    result = chat.execute_read(42, "barao", "consultar_dados_do_agente", {})
    assert len(result["renda_fixa"]) == len(result["investimentos_manuais"]) == 100
    assert all("lots" not in row for row in result["investimentos_manuais"])
    assert result["investimentos_manuais"][0] == {key: value for key, value in manual[0].items() if key != "lots"}
    assert result["cobertura"] == {
        field: {"total": 137, "incluidos": 100, "truncado": True}
        for field in ("renda_fixa", "investimentos_manuais")
    }
    assert len(manual[0]["lots"]) == 150  # o snapshot não altera os dados consultados


def test_resumo_zero_explicita_renda_fixa_excluida_em_caixinhas(user_id, monkeypatch):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into open_finance_connections "
            "(user_id, provider, provider_item_id, status, institution_id, institution_name) "
            "values (%s, 'pluggy', 'snapshot-carteira', 'ACTIVE', '2', 'Banco de teste') returning id", (user_id,))
        connection_id = cur.fetchone()["id"]
        cur.execute(
            "insert into open_finance_investments "
            "(connection_id, provider_investment_id, name, type, balance, currency) "
            "values (%s, 'acao', 'Ação', 'EQUITY', 1000, 'BRL')", (connection_id,))
        cur.execute(
            "insert into open_finance_investments "
            "(connection_id, provider_investment_id, name, type, balance, currency) "
            "values (%s, 'cdb', 'CDB Reserva', 'FIXED_INCOME', 9000, 'BRL') returning id",
            (connection_id,))
        investment_id = cur.fetchone()["id"]
        cur.execute(
            "insert into pockets (user_id, name, balance, source, of_investment_id, interest_enabled) "
            "values (%s, 'Reserva', 9000, 'open_finance', %s, false)", (user_id, investment_id))
        conn.commit()
    before = db.list_pockets(user_id, accrue=False)
    monkeypatch.setattr(db, "accrue_all_pockets", lambda *a: pytest.fail("aplicou juros"))
    result = chat.execute_read(user_id, "faria_limer", "consultar_dados_do_agente", {})
    assert result["resumo_brl"]["market_value"] == 1000
    assert result["renda_fixa_brl"]["balance"] == 0
    assert result["cobertura_renda_fixa"]["caixinhas_incluidas"] is False
    assert "Zero nesses resumos não prova ausência de renda fixa" in result["nota"]
    assert db.list_pockets(user_id, accrue=False) == before


@pytest.mark.parametrize("kind", ["faria_limer", "barao"])
def test_snapshots_nao_somam_moedas_mesmo_com_nome_igual(user_id, monkeypatch, kind):
    other_user = user_id + 1
    db.ensure_user(other_user)
    with db.get_conn() as conn, conn.cursor() as cur:
        for owner in (user_id, other_user):
            cur.execute(
                "insert into open_finance_connections "
                "(user_id, provider, provider_item_id, status, institution_id, institution_name) "
                "values (%s, 'pluggy', %s, 'ACTIVE', '2', 'Banco de teste') returning id", (owner, f"snapshot-moedas-{owner}"))
            connection_id = cur.fetchone()["id"]
            amounts = [("brl", 100), ("USD", 200), ("EUR", 300), ("BRL", 400)] if owner == user_id else [("BRL", 9999)]
            for index, (currency, balance) in enumerate(amounts):
                cur.execute(
                    "insert into open_finance_investments "
                    "(connection_id, provider_investment_id, name, type, balance, currency) "
                    "values (%s, %s, 'CDB - Banco Exemplo', 'FIXED_INCOME', %s, %s) returning id",
                    (connection_id, str(index), balance, currency))
                investment_id = cur.fetchone()["id"]
                if index == 3:
                    cur.execute(
                        "insert into pockets (user_id, name, balance, source, of_investment_id, interest_enabled) "
                        "values (%s, 'Reserva', 400, 'open_finance', %s, false)", (user_id, investment_id))
        conn.commit()
    # Outros consumidores mantêm a consulta legada; o filtro é explícito no chat.
    assert db.of_fixed_income_summary(user_id)["balance"] == 600
    assert db.list_of_fixed_income(user_id)[0]["balance"] == 600
    before = db.list_pockets(user_id, accrue=False)
    monkeypatch.setattr(db, "accrue_all_pockets", lambda *a: pytest.fail("aplicou juros"))
    monkeypatch.setattr(db, "accrue_all_investments", lambda *a: pytest.fail("aplicou juros"))
    result = chat.execute_read(user_id, kind, "consultar_dados_do_agente", {})
    if kind == "faria_limer":
        assert result["renda_fixa_brl"]["balance"] == 100
        assert result["renda_fixa_brl"]["count"] == 1
    else:
        assert len(result["renda_fixa"]) == 1
        assert result["renda_fixa"][0]["balance"] == 100
        assert result["renda_fixa"][0]["count"] == 1
    assert result["cobertura_renda_fixa"]["moeda"] == "BRL"
    assert result["cobertura_renda_fixa"]["outras_moedas_incluidas"] is False
    assert db.list_pockets(user_id, accrue=False) == before
