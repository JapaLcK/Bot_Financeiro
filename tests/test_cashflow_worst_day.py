"""Pior dia da trajetória (feature Pro) e as causas da queda até ele."""
from datetime import date, timedelta

from _cashflow_helpers import _mock_sources
from core.services.cashflow_forecast import forecast_with_trajectory


# Causas do pior dia: as SAÍDAS desde o último pico (maior saldo antes do pior
# dia; o mais recente em empate; num patamar, o dia em que o saldo chegou lá).

def test_worst_day_causas_inclui_todos_os_degraus_da_queda(monkeypatch):
    """Queda em 3 degraus de tipos diferentes: as 3 são causa, não só a do dia. A
    receita no meio da queda (dia 10) não é causa."""
    today = date.today()
    d = lambda n: today + timedelta(days=n)
    cf = _mock_sources(
        monkeypatch, saldo=2000.0,
        incomes=[{"is_active": True, "pay_day": d(10).day, "frequency": "monthly",
                  "amount": 100.0, "name": "Freela"}],
        expenses=[{"is_active": True, "payment_mode": "autopay", "frequency": "monthly",
                   "due_day": d(5).day, "amount": 900.0, "name": "Aluguel"}],
        card_bills=[{"due_date": d(8), "remaining": 700.0, "card_name": "Nubank"}],
        bills=[{"status": "pending", "due_date": d(12), "amount": 600.0, "name": "Luz"}],
    )
    wd = forecast_with_trajectory(1, days=15)["worst_day"]

    assert wd["date"] == d(12).isoformat() and wd["saldo_projetado"] == -100.0
    assert wd["desde"] == today.isoformat()
    assert wd["causas"] == [
        {"date": d(5).isoformat(), "tipo": "gasto_fixo", "nome": "Aluguel", "valor": 900.0},
        {"date": d(8).isoformat(), "tipo": "fatura_cartao", "nome": "Nubank", "valor": 700.0},
        {"date": d(12).isoformat(), "tipo": "boleto", "nome": "Luz", "valor": 600.0},
    ]
    assert wd["compromissos"] == [{"tipo": "boleto", "nome": "Luz", "valor": 600.0}]


def _cenario_pico_intermediario(monkeypatch):
    """1000 → gasto 300 (dia 3) → receita 2000 (dia 6) → gasto 2500 (dia 10)."""
    today = date.today()
    d = lambda n: today + timedelta(days=n)
    cf = _mock_sources(
        monkeypatch, saldo=1000.0,
        incomes=[{"is_active": True, "pay_day": d(6).day, "frequency": "monthly",
                  "amount": 2000.0, "name": "Salário"}],
        bills=[{"status": "pending", "due_date": d(3), "amount": 300.0, "name": "Mercado"},
               {"status": "pending", "due_date": d(10), "amount": 2500.0, "name": "IPVA"}],
    )
    return forecast_with_trajectory(1, days=20), d


def test_worst_day_causas_so_depois_do_ultimo_pico(monkeypatch):
    """O gasto do dia 3 veio ANTES do pico do dia 6 — não explica a queda até o dia 10."""
    out, d = _cenario_pico_intermediario(monkeypatch)
    wd = out["worst_day"]

    assert wd["date"] == d(10).isoformat() and wd["saldo_projetado"] == 200.0
    assert wd["desde"] == d(6).isoformat()
    assert wd["causas"] == [{"date": d(10).isoformat(), "tipo": "boleto", "nome": "IPVA", "valor": 2500.0}]


def test_worst_day_pico_empatado_vale_o_mais_recente(monkeypatch):
    """1000 → 500 (dia 3) → volta a 1000 (dia 6) → 200 (dia 10): dois picos de
    1000; vale o do dia 6, e o gasto do dia 3 já foi recuperado."""
    today = date.today()
    d = lambda n: today + timedelta(days=n)
    cf = _mock_sources(
        monkeypatch, saldo=1000.0,
        incomes=[{"is_active": True, "pay_day": d(6).day, "frequency": "monthly",
                  "amount": 500.0, "name": "Reembolso"}],
        bills=[{"status": "pending", "due_date": d(3), "amount": 500.0, "name": "Conserto"},
               {"status": "pending", "due_date": d(10), "amount": 800.0, "name": "IPVA"}],
    )
    wd = forecast_with_trajectory(1, days=20)["worst_day"]

    assert wd["date"] == d(10).isoformat()
    assert wd["desde"] == d(6).isoformat()
    assert [c["nome"] for c in wd["causas"]] == ["IPVA"]


def test_worst_day_nao_altera_o_item_da_trajetoria(monkeypatch):
    out, d = _cenario_pico_intermediario(monkeypatch)
    item = next(it for it in out["trajectory"] if it["date"] == out["worst_day"]["date"])

    assert "causas" not in item and "desde" not in item
    assert set(item) == {"date", "saldo_projetado", "abaixo_do_limite", "compromissos"}


def test_worst_day_pico_pode_ser_o_saldo_de_partida(monkeypatch):
    """Saldo 1000 hoje, cartão 300 no dia 1, luz 200 no dia 5: o pico é a partida
    (dia 0), então a queda vem desde hoje e as duas saídas são causa."""
    today = date.today()
    d = lambda n: today + timedelta(days=n)
    cf = _mock_sources(monkeypatch, saldo=1000.0, bills=[
        {"status": "pending", "due_date": d(1), "amount": 300.0, "name": "Cartão loja"},
        {"status": "pending", "due_date": d(5), "amount": 200.0, "name": "Luz"}])
    wd = forecast_with_trajectory(1, days=20)["worst_day"]

    assert (wd["date"], wd["saldo_projetado"]) == (d(5).isoformat(), 500.0)
    assert wd["desde"] == today.isoformat()
    assert [c["nome"] for c in wd["causas"]] == ["Cartão loja", "Luz"]


def test_worst_day_queda_abaixo_da_partida_depois_de_receita(monkeypatch):
    """Caso mais comum: saldo 100 hoje, salário 3000 amanhã, aluguel 1200 no dia 5,
    cartão 900 no dia 8, limite 1500. O pior dia (1000) fica ACIMA da partida (100),
    mas houve queda desde o pico do salário — ela tem de vir explicada."""
    today = date.today()
    d = lambda n: today + timedelta(days=n)
    cf = _mock_sources(
        monkeypatch, saldo=100.0,
        incomes=[{"is_active": True, "pay_day": d(1).day, "frequency": "monthly", "amount": 3000.0, "name": "Salário"}],
        bills=[{"status": "pending", "due_date": d(5), "amount": 1200.0, "name": "Aluguel"}],
        card_bills=[{"due_date": d(8), "remaining": 900.0, "card_name": "Nubank"}],
    )
    wd = forecast_with_trajectory(1, days=25, threshold=1500.0)["worst_day"]

    assert (wd["date"], wd["saldo_projetado"], wd["abaixo_do_limite"]) == (d(8).isoformat(), 1000.0, True)
    assert wd["desde"] == d(1).isoformat()
    assert [c["nome"] for c in wd["causas"]] == ["Aluguel", "Nubank"]


def test_worst_day_pico_considera_vencidos_no_saldo_de_partida(monkeypatch):
    """Saldo 3000, mas o aluguel vencido (2500) já o leva a 500 na partida; gasto de
    300 no dia 2, salário 1500 no dia 3, gasto de 1650 no dia 10. O pico é o do
    salário, não os 3000 que o vencido já consumiu."""
    today = date.today()
    d = lambda n: today + timedelta(days=n)
    cf = _mock_sources(
        monkeypatch, saldo=3000.0,
        incomes=[{"is_active": True, "pay_day": d(3).day, "frequency": "monthly", "amount": 1500.0, "name": "Salário"}],
        bills=[{"status": "pending", "due_date": d(-5), "amount": 2500.0, "name": "Aluguel atrasado"},
               {"status": "pending", "due_date": d(2), "amount": 300.0, "name": "Mercado"},
               {"status": "pending", "due_date": d(10), "amount": 1650.0, "name": "Oficina"}],
    )
    wd = forecast_with_trajectory(1, days=20)["worst_day"]

    assert (wd["date"], wd["saldo_projetado"]) == (d(10).isoformat(), 50.0)
    assert wd["desde"] == d(3).isoformat()
    assert [c["nome"] for c in wd["causas"]] == ["Oficina"]


def test_worst_day_sem_eventos_nao_tem_causas(monkeypatch):
    cf = _mock_sources(monkeypatch, saldo=50.0)
    out = forecast_with_trajectory(1, days=90)

    assert out["worst_day"]["causas"] == []
    assert out["worst_day"]["desde"] is None  # sem queda, nada a explicar
    assert out["vencidos"] == []


def test_worst_day_sem_queda_nao_culpa_saida(monkeypatch):
    """Partida 100; no dia 1, salário 1000 e luz 50 → o "pior dia" é o dia 1 com
    1050. O saldo subiu: dizer que a luz causou uma queda seria falso."""
    today = date.today()
    d1 = today + timedelta(days=1)
    cf = _mock_sources(
        monkeypatch, saldo=100.0,
        incomes=[{"is_active": True, "pay_day": d1.day, "frequency": "monthly", "amount": 1000.0, "name": "Salário"}],
        bills=[{"status": "pending", "due_date": d1, "amount": 50.0, "name": "Luz"}],
    )
    wd = forecast_with_trajectory(1, days=25)["worst_day"]

    assert (wd["date"], wd["saldo_projetado"]) == (d1.isoformat(), 1050.0)
    assert wd["causas"] == [] and wd["desde"] is None
    assert wd["compromissos"] == [{"tipo": "receita", "nome": "Salário", "valor": 1000.0},
                                  {"tipo": "boleto", "nome": "Luz", "valor": 50.0}]


def test_worst_day_saida_compensada_no_meio_do_patamar_nao_e_causa(monkeypatch):
    """1000 → salário 1700 no dia 3 (2700) → no dia 7 cartão 900 e freela 900 (continua
    2700) → IPVA 2500 no dia 12. `desde` é a chegada ao patamar (dia 3), mas o cartão
    foi 100% compensado: a única causa é o IPVA."""
    today = date.today()
    d = lambda n: today + timedelta(days=n)
    cf = _mock_sources(
        monkeypatch, saldo=1000.0,
        incomes=[{"is_active": True, "pay_day": d(3).day, "frequency": "monthly", "amount": 1700.0, "name": "Salário"},
                 {"is_active": True, "pay_day": d(7).day, "frequency": "monthly", "amount": 900.0, "name": "Freela"}],
        bills=[{"status": "pending", "due_date": d(7), "amount": 900.0, "name": "Cartão"},
               {"status": "pending", "due_date": d(12), "amount": 2500.0, "name": "IPVA"}],
    )
    wd = forecast_with_trajectory(1, days=25)["worst_day"]

    assert (wd["date"], wd["saldo_projetado"]) == (d(12).isoformat(), 200.0)
    assert wd["desde"] == d(3).isoformat()
    assert wd["causas"] == [{"date": d(12).isoformat(), "tipo": "boleto", "nome": "IPVA", "valor": 2500.0}]


def test_worst_day_saida_no_dia_do_pico_nao_e_causa(monkeypatch):
    """No dia 5, receita 3000 e saída 200 levam o saldo ao pico (3800); a queda
    começa no dia 6. A saída do próprio dia do pico não explica a queda."""
    today = date.today()
    d = lambda n: today + timedelta(days=n)
    cf = _mock_sources(
        monkeypatch, saldo=1000.0,
        incomes=[{"is_active": True, "pay_day": d(5).day, "frequency": "monthly", "amount": 3000.0, "name": "Bônus"}],
        bills=[{"status": "pending", "due_date": d(5), "amount": 200.0, "name": "Farmácia"},
               {"status": "pending", "due_date": d(6), "amount": 3500.0, "name": "Viagem"}],
    )
    wd = forecast_with_trajectory(1, days=20)["worst_day"]

    assert (wd["date"], wd["saldo_projetado"]) == (d(6).isoformat(), 300.0)
    assert wd["desde"] == d(5).isoformat()
    assert [c["nome"] for c in wd["causas"]] == ["Viagem"]
