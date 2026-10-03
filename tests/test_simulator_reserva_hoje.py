"""O saldo depois da compra de hoje não pode desaparecer com a receita de amanhã."""
from datetime import date, timedelta

import pytest

from _cashflow_helpers import _mock_sources
from core.services import cashflow_forecast, decision_simulator


@pytest.fixture
def hoje(monkeypatch):
    class Data(date):
        @classmethod
        def today(cls):
            return cls(2026, 9, 22)

    monkeypatch.setattr(decision_simulator, "date", Data)
    monkeypatch.setattr(cashflow_forecast, "date", Data)
    return Data.today()


@pytest.mark.parametrize("cenario,saldo_hoje", [
    ({"nome": "À vista", "preco": 900.0, "custos_unicos": 50.0}, 50.0),
    ({"nome": "Parcelada", "preco": 900.0, "entrada": 700.0,
      "parcelas": 10, "custos_unicos": 50.0}, 250.0),
    ({"nome": "Cabe hoje", "preco": 100.0}, 900.0),
])
def test_receita_amanha_nao_esconde_a_reserva_violada_hoje(monkeypatch, hoje, cenario, saldo_hoje):
    _mock_sources(monkeypatch, saldo=1000.0, incomes=[
        {"name": "Salário", "amount": 1000.0, "pay_day": 23, "is_active": True},
    ])
    out = decision_simulator.simulate(1, decision_simulator.Simulacao.model_validate({
        "reserva_minima": 500.0, "cenarios": [cenario],
    }))
    resumo = out["cenarios"][0]["resumo"]
    assert resumo["pior_dia"] == {"date": hoje.isoformat(), "saldo": saldo_hoje}
    assert resumo["dias_abaixo_da_reserva"] == int(saldo_hoje < 500)
    assert resumo["primeiro_dia_abaixo"] == (hoje.isoformat() if saldo_hoje < 500 else None)
    assert out["atual"]["dias_abaixo_da_reserva"] == 0


def test_compromissos_vencidos_e_de_hoje_entram_uma_vez(monkeypatch, hoje):
    _mock_sources(monkeypatch, saldo=1000.0,
                  incomes=[{"name": "Salário", "amount": 1000.0, "pay_day": 23, "is_active": True}],
                  bills=[
                      {"name": "Ontem", "amount": 100.0, "due_date": hoje - timedelta(days=1), "status": "pending"},
                      {"name": "Hoje", "amount": 150.0, "due_date": hoje, "status": "pending"},
                  ])
    out = decision_simulator.simulate(1, decision_simulator.Simulacao.model_validate({
        "reserva_minima": 500.0, "cenarios": [{"nome": "Compra", "preco": 400.0}],
    }))
    assert out["atual"]["pior_dia"] == {"date": hoje.isoformat(), "saldo": 750.0}
    resumo = out["cenarios"][0]["resumo"]
    assert resumo["pior_dia"] == {"date": hoje.isoformat(), "saldo": 350.0}
    assert resumo["saldo_final_90"] == 3350.0
    assert resumo["dias_abaixo_da_reserva"] == 1


def test_compra_futura_nao_sai_hoje_e_ultimo_dia_continua_90(monkeypatch, hoje):
    ultimo = hoje + timedelta(days=90)
    _mock_sources(monkeypatch, saldo=1000.0)
    out = decision_simulator.simulate(1, decision_simulator.Simulacao.model_validate({
        "reserva_minima": 500.0,
        "cenarios": [{"nome": "Depois", "preco": 900.0, "data_compra": ultimo.isoformat()}],
    }))
    resumo = out["cenarios"][0]["resumo"]
    assert resumo["pior_dia"] == {"date": ultimo.isoformat(), "saldo": 100.0}
    assert resumo["dias_abaixo_da_reserva"] == 1
    assert resumo["saldo_final_90"] == 100.0
    assert resumo["primeiro_dia_abaixo"] == ultimo.isoformat()


def test_periodo_inclui_hoje_e_os_90_dias_seguintes(monkeypatch, hoje):
    _mock_sources(monkeypatch, saldo=1000.0)
    out = decision_simulator.simulate(1, decision_simulator.Simulacao.model_validate({
        "reserva_minima": 500.0, "cenarios": [{"nome": "Compra", "preco": 900.0}],
    }))
    resumo = out["cenarios"][0]["resumo"]
    assert resumo["dias_abaixo_da_reserva"] == 91
    assert resumo["primeiro_dia_abaixo"] == hoje.isoformat()
    # O contrato geral da previsão continua com amanhã..dia 90.
    fc = cashflow_forecast.forecast_with_trajectory(1)
    assert len(fc["trajectory"]) == 90
    assert fc["period"]["start"] == (hoje + timedelta(days=1)).isoformat()
    assert fc["period"]["end"] == (hoje + timedelta(days=90)).isoformat()
