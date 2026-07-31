from datetime import date, datetime
from unittest.mock import patch

from zoneinfo import ZoneInfo

from core.reports.reports_daily import (
    build_weekly_report_text,
    build_monthly_report_text,
    build_weekly_report_summary,
    build_monthly_report_summary,
)

_TZ = ZoneInfo("America/Sao_Paulo")


def test_build_weekly_report_text():
    with patch("core.reports.reports_daily.get_balance", return_value=1000), \
         patch("core.reports.reports_daily.get_launches_by_period", return_value=[{"id": 1}, {"id": 2}, {"id": 3}]), \
         patch("core.reports.reports_daily.get_summary_by_period", return_value={"despesa": 150.0, "receita": 20.0}):
        msg = build_weekly_report_text(123)

    assert "📊 *Resumo semanal do Bot Financeiro*" in msg
    assert "🏦 Saldo atual: R$ 1.000,00" in msg
    assert "📉 Gastos da semana: R$ 150,00" in msg
    assert "📈 Receitas da semana: R$ 20,00" in msg
    assert "📊 Lançamentos da semana: 3" in msg


def test_build_monthly_report_text():
    with patch("core.reports.reports_daily.get_balance", return_value=1000), \
         patch("core.reports.reports_daily.get_launches_by_period", return_value=[{"id": 1}]), \
         patch("core.reports.reports_daily.get_summary_by_period", return_value={"despesa": 999.90, "receita": 500.0}):
        msg = build_monthly_report_text(123)

    assert "📊 *Resumo mensal do Bot Financeiro*" in msg
    assert "🏦 Saldo atual: R$ 1.000,00" in msg
    assert "📉 Gastos do mês: R$ 999,90" in msg
    assert "📈 Receitas do mês: R$ 500,00" in msg
    assert "📊 Lançamentos do mês: 1" in msg


def test_weekly_closed_usa_semana_anterior():
    # segunda-feira 2026-08-03 → semana fechada = 27/07 a 02/08
    fake_now = datetime(2026, 8, 3, 9, 0, tzinfo=_TZ)
    captured = {}

    def _fake_summary(user_id, start, end):
        captured["start"], captured["end"] = start, end
        return {"despesa": 0.0, "receita": 0.0}

    with patch("core.reports.reports_daily.now_tz", return_value=fake_now), \
         patch("core.reports.reports_daily.get_balance", return_value=0), \
         patch("core.reports.reports_daily.get_launches_by_period", return_value=[]), \
         patch("core.reports.reports_daily.get_summary_by_period", side_effect=_fake_summary):
        s = build_weekly_report_summary(123, closed=True)

    assert captured["start"] == date(2026, 7, 27)
    assert captured["end"] == date(2026, 8, 2)
    assert s["start"] == "27/07/2026" and s["end"] == "02/08/2026"


def test_monthly_closed_usa_mes_anterior():
    # dia 1 (01/08/2026) → mês fechado = 01/07 a 31/07
    fake_now = datetime(2026, 8, 1, 9, 0, tzinfo=_TZ)
    captured = {}

    def _fake_summary(user_id, start, end):
        captured["start"], captured["end"] = start, end
        return {"despesa": 0.0, "receita": 0.0}

    with patch("core.reports.reports_daily.now_tz", return_value=fake_now), \
         patch("core.reports.reports_daily.get_balance", return_value=0), \
         patch("core.reports.reports_daily.get_launches_by_period", return_value=[]), \
         patch("core.reports.reports_daily.get_summary_by_period", side_effect=_fake_summary):
        s = build_monthly_report_summary(123, closed=True)

    assert captured["start"] == date(2026, 7, 1)
    assert captured["end"] == date(2026, 7, 31)
    assert s["start"] == "01/07/2026" and s["end"] == "31/07/2026"


def test_classifier_routes_weekly_and_monthly():
    from core.intent_classifier import _try_exact, _try_alias, _normalize

    for text in ("resumo semanal", "resumo da semana", "quero o relatorio semanal"):
        norm = _normalize(text)
        res = _try_exact(norm) or _try_alias(norm, text)
        assert res is not None and res.intent == "report.weekly", text

    for text in ("resumo mensal", "resumo do mes", "quero o relatorio mensal"):
        norm = _normalize(text)
        res = _try_exact(norm) or _try_alias(norm, text)
        assert res is not None and res.intent == "report.monthly", text
