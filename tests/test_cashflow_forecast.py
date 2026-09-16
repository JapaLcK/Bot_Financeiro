"""Previsão de saldo 30/60/90 dias (feature Pro).

`forecast_horizons` não tem lógica de projeção própria — reusa a projeção de
`core/services/cashflow.py` em hoje+30/60/90, sobre uma leitura só das fontes.
Aqui garantimos o contrato (chaves + datas-alvo) sem tocar no DB, e a paridade
entre `project` e a trajetória.
"""
import json
from datetime import date, timedelta

import pytest

from _cashflow_helpers import _mock_sources, fontes_que_mudam
from core.services.cashflow_forecast import forecast_horizons, forecast_with_trajectory


def test_forecast_horizons_reusa_project_nos_tres_horizontes(monkeypatch):
    cf = _mock_sources(monkeypatch)

    alvos: list[date] = []

    def fake_project(today, sb, events, target, extra=0.0):
        alvos.append(target)
        return {"target": target.isoformat(), "projetado": 100.0, "tranquilo": True,
                "balance_source": "consolidated", "of_bank_count": 2,
                "banks_excluded": False}

    monkeypatch.setattr(cf, "_projection", fake_project)

    out = forecast_horizons(42)

    assert set(out["horizons"].keys()) == {"30", "60", "90"}
    today = date.today()
    assert alvos == [today + timedelta(days=30),
                     today + timedelta(days=60),
                     today + timedelta(days=90)]
    assert out["today"] == today.isoformat()
    assert out["horizons"]["30"]["projetado"] == 100.0
    # origem do saldo sobe pro topo pro dashboard renderizar o aviso
    assert out["balance_source"] == "consolidated"
    assert out["of_bank_count"] == 2
    assert out["banks_excluded"] is False


def test_forecast_horizons_default_balance_source_quando_project_omite(monkeypatch):
    cf = _mock_sources(monkeypatch)
    monkeypatch.setattr(cf, "_projection", lambda today, sb, events, target, extra=0.0: {"projetado": 0.0})
    out = forecast_horizons(1, horizons=(30,))
    assert out["balance_source"] == "manual"
    assert out["banks_excluded"] is False


def test_forecast_horizons_aceita_horizontes_customizados(monkeypatch):
    cf = _mock_sources(monkeypatch)
    monkeypatch.setattr(cf, "_projection", lambda today, sb, events, target, extra=0.0: {"t": target.isoformat()})
    out = forecast_horizons(1, horizons=(7, 15))
    assert set(out["horizons"].keys()) == {"7", "15"}


def test_forecast_horizons_le_as_fontes_uma_vez(monkeypatch):
    """Uma leitura por resposta: se as fontes mudam entre leituras (boleto pago,
    saldo novo), os três horizontes continuam saindo do mesmo estado."""
    leituras = fontes_que_mudam(monkeypatch)
    out = forecast_horizons(1)

    assert [out["horizons"][n]["projetado"] for n in ("30", "60", "90")] == [300.0] * 3
    assert leituras == dict.fromkeys(("get_consolidated_balance", "list_recurring_expenses", "list_recurring_incomes", "list_bills", "_open_card_bills_detail"), 1)


# 6) Paridade com project() nos 3 marcos (30/60/90): os dois blocos do mesmo
# payload saem da mesma fonte de eventos. Há itens ENTRE os marcos (só um marco
# posterior os enxerga) e itens que os filtros da fonte têm de excluir — sem a
# checagem de nomes, um filtro removido passaria aqui, porque somaria nos dois.

def test_daily_trajectory_bate_com_project_nos_tres_marcos(monkeypatch):
    today = date.today()
    d50 = today + timedelta(days=50)
    incomes = [
        {"is_active": True, "pay_day": 5, "frequency": "monthly", "amount": 1000.0, "name": "Salário"},
        {"is_active": True, "pay_day": d50.day, "frequency": "annual", "pay_month": d50.month,
         "amount": 800.0, "name": "13º"},
        {"is_active": False, "pay_day": 6, "frequency": "monthly", "amount": 999.0, "name": "Receita inativa"},
    ]
    expenses = [
        {"is_active": True, "payment_mode": "autopay", "frequency": "monthly",
         "due_day": 20, "amount": 400.0, "name": "Aluguel"},
        {"is_active": False, "payment_mode": "autopay", "frequency": "monthly",
         "due_day": 7, "amount": 111.0, "name": "Gasto inativo"},
        {"is_active": True, "payment_mode": "manual", "frequency": "monthly",
         "due_day": 8, "amount": 222.0, "name": "Gasto manual"},
        {"is_active": True, "payment_mode": "autopay", "frequency": "weekly",
         "due_day": 9, "amount": 333.0, "name": "Gasto semanal"},
    ]
    bills = [
        {"status": "pending", "due_date": today + timedelta(days=45), "amount": 150.0, "name": "Água"},
        {"status": "pending", "due_date": today + timedelta(days=60), "amount": 35.0, "name": "No marco 60"},
        {"status": "pending", "due_date": today + timedelta(days=90), "amount": 45.0, "name": "No marco 90"},
        {"status": "pending", "due_date": today - timedelta(days=3), "amount": 60.0, "name": "Multa"},
        {"status": "paid", "due_date": today + timedelta(days=15), "amount": 444.0, "name": "Boleto pago"},
    ]
    card_bills = [
        {"due_date": today + timedelta(days=10), "remaining": 300.0, "card_name": "Nubank"},
        {"due_date": today + timedelta(days=75), "remaining": 250.0, "card_name": "Inter"},
        {"due_date": today + timedelta(days=100), "remaining": 555.0, "card_name": "Fora do horizonte"},
    ]
    cf = _mock_sources(monkeypatch, saldo=2000.0, incomes=incomes, expenses=expenses,
                       bills=bills, card_bills=card_bills)

    traj = forecast_with_trajectory(1, days=90)
    fh = forecast_horizons(1)
    for idx, dias in ((29, 30), (59, 60), (89, 90)):
        esperado = cf.project(1, today + timedelta(days=dias))["projetado"]
        assert traj["trajectory"][idx]["saldo_projetado"] == esperado, dias
        assert traj["horizons"][str(dias)]["projetado"] == esperado, dias
        assert fh["horizons"][str(dias)]["projetado"] == esperado, dias

    vistos = {c["nome"] for item in traj["trajectory"] for c in item["compromissos"]}
    vistos |= {v["nome"] for v in traj["vencidos"]}
    assert {"Salário", "13º", "Aluguel", "Água", "Multa", "Nubank", "Inter"} <= vistos
    assert not vistos & {"Receita inativa", "Gasto inativo", "Gasto manual", "Gasto semanal",
                         "Boleto pago", "Fora do horizonte"}


def test_trajetoria_e_project_batem_com_fracao_de_centavo(monkeypatch):
    """As colunas de valor são `numeric` sem escala: fração de centavo chega do
    banco. Somada em ordem ou agrupamento diferentes (trajetória por dia, `project`
    por tipo), ela arredondava diferente; com soma exata, não. Cenários mínimos
    achados por busca, cada um quebra com uma forma de soma inexata: o 1º com a de
    HEAD (subtotais em sequência + expressão agrupada, trajetória acumulando); o 2º
    com só a trajetória acumulando ou só `project` agrupando."""
    today = date.today()
    for saldo, v1, v2 in ((66.3, 19.485, 48.48), (78.2, 5.168, 14.967)):
        cf = _mock_sources(monkeypatch, saldo=saldo, bills=[
            {"status": "pending", "due_date": today + timedelta(days=1), "amount": v1, "name": "B1"},
            {"status": "pending", "due_date": today + timedelta(days=2), "amount": v2, "name": "B2"},
        ])
        traj = forecast_with_trajectory(1, days=3)["trajectory"]
        for n in (1, 2, 3):
            assert traj[n - 1]["saldo_projetado"] == cf.project(1, today + timedelta(days=n))["projetado"], (saldo, n)


def test_project_fracao_de_centavo_soma_como_o_painel(monkeypatch):
    """20 × R$ 10,005 soma 200,10, como o painel "Em aberto" e a tool de contas
    (soma crua). Arredondar cada valor antes de somar daria 200,20 — R$ 0,10 de
    diferença entre dois números lado a lado na tela. Vale para as quatro fontes
    e para o saldo de partida. Esperados conferidos contra o `project()` de HEAD."""
    today = date.today()
    d = lambda n: today + timedelta(days=n)
    vinte = range(20)  # dias 3..22: cada recorrente cai uma única vez até o dia 30
    cenarios = [
        ("boletos_ate", -200.1, dict(bills=[
            {"status": "pending", "due_date": d(3), "amount": 10.005, "name": f"B{i}"} for i in vinte])),
        ("receitas_previstas", 200.1, dict(incomes=[
            {"is_active": True, "pay_day": d(3 + i).day, "frequency": "monthly", "amount": 10.005, "name": f"R{i}"}
            for i in vinte])),
        ("gastos_fixos_previstos", -200.1, dict(expenses=[
            {"is_active": True, "payment_mode": "autopay", "frequency": "monthly", "due_day": d(3 + i).day,
             "amount": 10.005, "name": f"G{i}"} for i in vinte])),
        ("faturas_cartao", -200.1, dict(card_bills=[
            {"due_date": d(4), "remaining": 10.005, "card_name": "Nubank"} for _ in vinte])),
    ]
    for campo, projetado, fontes in cenarios:
        cf = _mock_sources(monkeypatch, **fontes)
        out = cf.project(1, d(30))
        assert (out[campo], out["projetado"]) == (200.1, projetado), campo
        assert forecast_with_trajectory(1, days=30)["trajectory"][29]["saldo_projetado"] == projetado, campo

    # saldo de partida 0,004 + estorno de 0,004 = 0,008 → 0,01 (arredondando o saldo antes: 0,00)
    cf = _mock_sources(monkeypatch, saldo=0.004, bills=[
        {"status": "pending", "due_date": d(2), "amount": -0.004, "name": "Estorno"}])
    assert cf.project(1, d(30))["projetado"] == 0.01


@pytest.mark.parametrize("tarifa, projetado, tranquilo", [
    pytest.param(0.004, 0.0, True, id="fracao_de_centavo_exibe_zero"),
    pytest.param(0.006, -0.01, False, id="um_centavo_negativo_aperta"),
])
def test_project_tranquilo_olha_o_valor_em_centavos(monkeypatch, tarifa, projetado, tranquilo):
    """Saldo zero e uma tarifa: `tranquilo` decide pelo projetado em centavos, o valor
    exibido. R$ 0,004 mostra R$ 0,00 e é tranquilo; R$ 0,006 mostra −R$ 0,01 e aperta.
    O JSON (tool de IA, dashboard) não leva −0,0 junto de `tranquilo=true`."""
    today = date.today()
    cf = _mock_sources(monkeypatch, saldo=0.0, bills=[
        {"status": "pending", "due_date": today + timedelta(days=2), "amount": tarifa, "name": "Tarifa"}])
    out = cf.project(1, today + timedelta(days=30))

    assert json.dumps(out["projetado"]) == json.dumps(projetado)
    assert out["tranquilo"] is tranquilo


@pytest.mark.parametrize("saldo, valores, tranquilo", [
    pytest.param(0.3, (0.1, 0.2), True, id="centavos_com_ruido_de_float"),
    pytest.param(0.0, (0.004,), True, id="fracao_de_centavo_exibe_zero"),
    pytest.param(0.0, (0.006,), False, id="um_centavo_negativo"),
    pytest.param(0.0, (), True, id="zero_exato"),
])
def test_trajetoria_com_limite_zero_e_a_mesma_pergunta_do_tranquilo(monkeypatch, saldo, valores, tranquilo):
    """R$ 0,30 − 0,10 − 0,20 soma −2,8e-17 em float: o caixa é R$ 0,00, tranquilo nos
    horizontes e fora do aperto na trajetória, no mesmo dia. Com limite 0, os dois
    olham o mesmo valor em centavos."""
    today = date.today()
    _mock_sources(monkeypatch, saldo=saldo, bills=[
        {"status": "pending", "due_date": today + timedelta(days=2 + 3 * i), "amount": v, "name": f"B{i}"}
        for i, v in enumerate(valores)])
    out = forecast_with_trajectory(1, days=90, threshold=0.0)

    for n in (30, 60, 90):
        horizonte, item = out["horizons"][str(n)], out["trajectory"][n - 1]
        assert item["saldo_projetado"] == horizonte["projetado"], n
        assert horizonte["tranquilo"] is tranquilo, n
        assert item["abaixo_do_limite"] is (not tranquilo), n
    assert out["worst_day"]["abaixo_do_limite"] is (not tranquilo)
