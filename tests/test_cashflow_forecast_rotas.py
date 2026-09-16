"""Rotas /forecast e /recurring-bills/.../projection pelo HTTP de verdade."""
from datetime import date, timedelta

from _cashflow_helpers import _mock_sources, fontes_que_mudam
from core.services.cashflow_forecast import forecast_horizons, forecast_with_trajectory


# Rotas /forecast e /recurring-bills/.../projection pelo HTTP de verdade
# (TestClient), com auth/plano mockados como em tests/test_export_email.py.

def _cliente(monkeypatch, **fontes):
    from fastapi.testclient import TestClient
    import frontend.finance_bot_websocket_custom as app_mod

    monkeypatch.setattr(app_mod, "_authorize_dashboard_access", lambda req, user_id: None)
    monkeypatch.setattr(app_mod, "_require_pro", lambda user_id, feature: None)
    monkeypatch.setattr(app_mod, "_require_boletos_access", lambda user_id: None)
    cf = _mock_sources(monkeypatch, **fontes)
    # sem raise: um 500 tem de aparecer como 500, não como exceção do teste
    return TestClient(app_mod.app, raise_server_exceptions=False), cf


def test_rota_forecast_devolve_trajetoria_causas_vencidos_e_horizons_intactos(monkeypatch):
    today = date.today()
    client, cf = _cliente(monkeypatch, saldo=1000.0, bills=[
        {"status": "pending", "due_date": today - timedelta(days=2), "amount": 100.0, "name": "Multa"},
        {"status": "pending", "due_date": today + timedelta(days=10), "amount": 700.0, "name": "Aluguel"},
    ])
    r = client.get("/forecast/1?threshold=250")
    assert r.status_code == 200, r.text
    fc = r.json()["forecast"]

    assert len(fc["trajectory"]) == 90
    assert fc["threshold"] == 250.0
    assert fc["period"] == {"start": (today + timedelta(days=1)).isoformat(),
                            "end": (today + timedelta(days=90)).isoformat()}
    assert fc["premises"] == forecast_with_trajectory(1)["premises"]
    assert fc["vencidos"] == [{"date": (today - timedelta(days=2)).isoformat(),
                               "tipo": "boleto", "nome": "Multa", "valor": 100.0}]
    wd = fc["worst_day"]
    assert (wd["date"], wd["saldo_projetado"], wd["abaixo_do_limite"]) == (
        (today + timedelta(days=10)).isoformat(), 200.0, True)
    assert wd["desde"] == today.isoformat()
    assert wd["causas"] == [{"date": (today + timedelta(days=10)).isoformat(),
                             "tipo": "boleto", "nome": "Aluguel", "valor": 700.0}]
    # os horizontes da rota batem com os de `forecast_horizons` (a da tool de IA) nas mesmas fontes
    esperado = forecast_horizons(1)
    assert fc["horizons"] == esperado["horizons"]
    for campo in ("today", "balance_source", "of_bank_count", "banks_excluded"):
        assert fc[campo] == esperado[campo]


def test_rota_forecast_recusa_threshold_nao_finito(monkeypatch):
    client, _ = _cliente(monkeypatch, saldo=1000.0)
    assert client.get("/forecast/1?threshold=100").status_code == 200  # finito passa
    for valor in ("nan", "inf", "-inf"):
        r = client.get(f"/forecast/1?threshold={valor}")
        assert r.status_code == 400, (valor, r.status_code, r.text)


def test_rota_projection_recusa_amount_nao_finito(monkeypatch):
    client, _ = _cliente(monkeypatch, saldo=1000.0)
    alvo = (date.today() + timedelta(days=30)).isoformat()
    assert client.get(f"/recurring-bills/1/projection?date={alvo}&amount=100").status_code == 200
    for valor in ("nan", "inf"):
        r = client.get(f"/recurring-bills/1/projection?date={alvo}&amount={valor}")
        assert r.status_code == 400, (valor, r.status_code, r.text)


def test_rota_forecast_horizontes_e_trajetoria_da_mesma_leitura(monkeypatch):
    """Horizontes e trajetória da MESMA resposta saem de uma leitura só: se as
    fontes mudam entre leituras, o dia 30 da trajetória ainda é o horizonte de 30."""
    client, _ = _cliente(monkeypatch)
    leituras = fontes_que_mudam(monkeypatch)
    r = client.get("/forecast/1")
    assert r.status_code == 200, r.text
    fc = r.json()["forecast"]

    for n in (30, 60, 90):
        assert fc["trajectory"][n - 1]["saldo_projetado"] == fc["horizons"][str(n)]["projetado"] == 300.0, n
    assert fc["worst_day"]["saldo_projetado"] == 300.0
    assert list(leituras.values()) == [1] * 5
    assert set(fc) == {"today", "balance_source", "of_bank_count", "banks_excluded", "horizons",
                       "trajectory", "worst_day", "vencidos", "threshold", "period", "premises",
                       "vencem_hoje"}
