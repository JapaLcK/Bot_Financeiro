from unittest.mock import patch

from core.reports.reports_daily import build_daily_report_text


def test_build_daily_report_text_usa_formato_compacto():
    with patch("core.reports.reports_daily.get_balance", return_value=1000), \
         patch("core.reports.reports_daily.get_launches_by_period", return_value=[{"id": 1}, {"id": 2}]), \
         patch("core.reports.reports_daily.get_summary_by_period", return_value={"despesa": 42.90, "receita": 10.0}):
        msg = build_daily_report_text(123)

    assert "🏦 Saldo atual: R$ 1.000,00" in msg
    assert "📉 Gastos de ontem: R$ 42,90" in msg
    assert "📈 Receitas de ontem: R$ 10,00" in msg
    assert "📊 Lançamentos de ontem: 2" in msg
    assert "Investimentos" not in msg
    assert "Caixinhas" not in msg
