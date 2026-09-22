"""Matriz dos planos nas portas reais: HTTP, WhatsApp e tools da IA."""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import db
from core.services import plan_service as plans
from _cashflow_helpers import _mock_sources


@pytest.fixture
def tier(monkeypatch):
    monkeypatch.setenv("PLANS_V2_ENABLED", "1")
    current = ["essencial"]
    monkeypatch.setattr(plans, "get_plan_tier", lambda uid: current[0])
    return lambda value: current.__setitem__(0, value)


@pytest.fixture
def client(monkeypatch):
    import frontend.finance_bot_websocket_custom as app
    from frontend.routes import shared
    # Isola a matriz de capacidades. Autenticação/dono permanecem nos testes
    # test_routes_analytics e test_planos_acesso_sem_assinatura abaixo.
    monkeypatch.setattr(app, "_authorize_dashboard_access", lambda *a: None)
    monkeypatch.setattr(shared, "authorize_dashboard_access", lambda *a: None)
    monkeypatch.setattr(app.limiter, "enabled", False)
    client = TestClient(app.app, raise_server_exceptions=False)
    client.cookies.set(app.CSRF_COOKIE_NAME, "permissions-csrf")
    client.headers[app.CSRF_HEADER_NAME] = "permissions-csrf"
    return client


@pytest.mark.parametrize("value,forecast,insights,cashflow", [
    ("free", False, False, False), ("essencial", False, False, False),
    ("plus", True, True, False), ("pro", True, True, True),
])
def test_matriz_de_capacidades(tier, value, forecast, insights, cashflow):
    tier(value)
    for feature in ("ai_categorization", "recurring_expenses", "export"):
        assert plans.plan_gate_ok(1, feature) is (value != "free")
    assert plans.plan_gate_ok(1, "forecast") is forecast
    assert plans.plan_gate_ok(1, "cashflow") is cashflow
    for feature in ("insights", "financial_comparison", "weekly_report"):
        assert plans.plan_gate_ok(1, feature) is insights


@pytest.mark.parametrize("value,expected", [("free", False), ("essencial", True), ("plus", True), ("pro", True)])
def test_categorizacao_ia_no_tier_correto(tier, value, expected, user_id, monkeypatch):
    from core.services.category_service import infer_category
    import ai_router
    tier(value)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-no-network")
    calls = []
    monkeypatch.setattr(ai_router, "classify_category_with_gpt", lambda *a, **k: calls.append(a) or "saude")
    result = infer_category(user_id, "xqzlorp")
    assert bool(calls) is expected
    assert (result.reason == "ai") is expected


def test_essencial_cria_recorrente_pelo_whatsapp_e_quarto_orcamento(tier, pro_user_id, client, monkeypatch):
    from types import SimpleNamespace
    import core.handle_incoming as incoming
    import openai
    from core.types import IncomingMessage
    from db.recurring import list_recurring_expenses
    user_id = pro_user_id
    # Só o serviço externo é simulado: classificador, gate e roteador são reais.
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-no-network")
    calls = []
    def complete(**kwargs):
        calls.append(kwargs)
        result = '{"intent":"recurring.add","confidence":0.99,"entities":{"valor":100,"dia":10,"nome":"internet","categoria":"moradia"}}'
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=result))])
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=complete))))
    monkeypatch.setattr(incoming, "_normalize_user_id", lambda msg: user_id)
    def say(text):
        return incoming.handle_incoming(IncomingMessage(platform="whatsapp", user_id=user_id,
            text=text, message_id="permissions-test", external_id="permissions", raw={}))
    say("gastei 15 no mercado")
    response = say("gasto fixo de 100 reais de internet todo dia 10")
    assert calls, "o fluxo deve passar pela classificação de IA do Essencial"
    assert len(list_recurring_expenses(user_id)) == 1, response
    for category in ("mercado", "lazer", "saude", "moradia"):
        r = client.post(f"/budgets/{user_id}", json={"categoria": category, "budget": 100})
        assert r.status_code == 200, r.text
    assert len(db.list_budgets(user_id)) == 4


def test_recorrente_nao_grava_se_consulta_de_plano_falhar(user_id, monkeypatch):
    from core.handlers import recurring
    from db.recurring import list_recurring_expenses
    def unavailable(*args):
        raise RuntimeError("plano indisponível")
    monkeypatch.setattr(plans, "plan_gate_ok", unavailable)
    result = recurring.add(user_id, "internet todo dia 10", {"valor": 100, "dia": 10, "nome": "internet"})
    assert "Tente novamente" in result
    assert list_recurring_expenses(user_id) == []


@pytest.mark.parametrize("value", ["free", "essencial", "plus", "pro"])
def test_forecast_api_e_tool_nao_vazam_horizontes(tier, value, client, user_id, monkeypatch):
    from core.services.ai_chat.tools.bills import _forecast_balance
    tier(value)
    _mock_sources(monkeypatch, saldo=1000.0)
    response = client.get(f"/forecast/{user_id}?threshold=300")
    tool = _forecast_balance(user_id, {})
    if value in {"free", "essencial"}:
        assert response.status_code == 403
        assert tool["error"] == "pro_required"
        return
    assert response.status_code == 200, response.text
    data = response.json()["forecast"]
    expected = {"30"} if value == "plus" else {"30", "60", "90"}
    assert set(data["horizons"]) == set(tool["horizons"]) == expected
    if value == "plus":
        assert not {"trajectory", "worst_day", "period", "threshold", "vencidos"} & data.keys()
    else:
        assert len(data["trajectory"]) == 90
        assert data["threshold"] == 300


@pytest.mark.parametrize("value,cap", [("plus", 30), ("pro", 90)])
def test_projecao_por_data_e_tool_respeitam_limite(tier, value, cap, client, user_id, monkeypatch):
    from core.services.ai_chat.tools.bills import _check_cashflow
    tier(value)
    _mock_sources(monkeypatch, saldo=1000.0)
    for days, status in [(cap, 200), (cap + 1, 403)]:
        target = (date.today() + timedelta(days=days)).isoformat()
        r = client.get(f"/recurring-bills/{user_id}/projection", params={"date": target})
        assert r.status_code == status, r.text
        for args in ({"date": target}, {"days": days}):
            result = _check_cashflow(user_id, args)
            assert (result.get("error") == "pro_required") is (status == 403)


def test_essencial_nao_calcula_projecao_alternativa(tier, client, user_id, monkeypatch):
    from core.services.ai_chat.tools.bills import _check_cashflow
    from core.services.ai_chat.tools.launches import _forecast_month_end
    from core.services import cashflow
    monkeypatch.setattr(cashflow, "project", lambda *a: pytest.fail("projeção não autorizada"))
    target = (date.today() + timedelta(days=15)).isoformat()
    assert client.get(f"/recurring-bills/{user_id}/projection", params={"date": target}).status_code == 403
    assert _check_cashflow(user_id, {"days": 15})["error"] == "pro_required"
    assert _forecast_month_end(user_id, {})["error"] == "pro_required"


def test_analytics_preserva_basico_e_bloqueia_comparacao(tier, client, user_id):
    from core.services.ai_chat.tools.launches import _compare_periods, _get_spending_trend
    db.add_launch_and_update_balance(user_id, "receita", 200, None, "salario")
    r = client.get(f"/analytics/{user_id}/kpis")
    assert r.status_code == 200, r.text
    kpis = r.json()["kpis"]
    assert kpis["total_income"] == 200
    assert "prev" not in kpis and "delta_pct" not in kpis
    for path in ("categories", "top-merchants"):
        assert client.get(f"/analytics/{user_id}/{path}").status_code == 200
    for path in ("evolution", "weekday-pattern", "patterns"):
        assert client.get(f"/analytics/{user_id}/{path}").status_code == 403
    assert client.get(f"/insights/{user_id}/current").status_code == 403
    assert _compare_periods(user_id, {})["error"] == "pro_required"
    assert _get_spending_trend(user_id, {})["error"] == "pro_required"
    tier("plus")
    kpis = client.get(f"/analytics/{user_id}/kpis").json()["kpis"]
    assert "prev" in kpis and "delta_pct" in kpis
    assert client.get(f"/analytics/{user_id}/evolution").status_code == 200
    assert "data" in _get_spending_trend(user_id, {})


def test_downgrade_nao_le_cache_de_insights(tier, user_id, monkeypatch):
    from core import ai_patterns
    from core.services.proactive_ai_scheduler import _precompute_user
    cached = [{"title": "Insight existente"}]
    calls = []
    monkeypatch.setattr(ai_patterns, "_get_cached", lambda *a: calls.append(a) or cached)
    tier("plus")
    assert ai_patterns.generate_ai_insights(user_id) == cached
    assert ai_patterns.generate_ai_patterns(user_id) == cached
    calls.clear()
    tier("essencial")
    assert ai_patterns.generate_ai_insights(user_id) == []
    assert ai_patterns.generate_ai_patterns(user_id) == []
    _precompute_user(user_id)
    assert calls == []


def test_weekly_ativacao_e_downgrade_preservam_desligar(tier, client, user_id):
    from core.handlers import report
    assert "Plus" in report.enable_weekly(user_id)
    url = f"/settings/{user_id}/notifications"
    r = client.patch(url, json={"weekly_report_enabled": True})
    assert r.status_code == 403, r.text
    tier("plus")
    assert client.patch(url, json={"weekly_report_enabled": True}).status_code == 200
    tier("essencial")
    r = client.get(url)
    assert r.status_code == 200, r.text
    assert r.json()["weekly_report_available"] is False
    assert r.json()["weekly_report_enabled"] is False
    r = client.patch(url, json={"weekly_report_enabled": False})
    assert r.status_code == 200, r.text
    assert "desligado" in report.disable_weekly(user_id).lower()


def test_legado_mantem_capacidades_anteriores(monkeypatch):
    monkeypatch.setenv("PLANS_V2_ENABLED", "0")
    monkeypatch.setattr(plans, "is_pro", lambda uid: False)
    for feature in ("weekly_report", "insights", "financial_comparison"):
        assert plans.plan_gate_ok(1, feature)
    for feature in ("recurring_expenses", "forecast", "ai_categorization"):
        assert not plans.plan_gate_ok(1, feature)
