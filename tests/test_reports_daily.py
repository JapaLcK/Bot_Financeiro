from unittest.mock import patch

from core.reports.reports_daily import build_daily_report_text


def test_build_daily_report_text_capitaliza_investimentos_antes_de_exibir():
    with patch("core.reports.reports_daily.get_balance", return_value=1000), \
         patch("core.reports.reports_daily.list_pockets", return_value=[]), \
         patch("core.reports.reports_daily.accrue_all_investments", return_value=[
             {"name": "CDB Banco", "balance": 1250.50}
         ]) as accrue, \
         patch("core.reports.reports_daily.get_launches_by_period", return_value=[]), \
         patch("core.reports.reports_daily.get_summary_by_period", return_value={"despesa": 0.0, "receita": 0.0}):
        msg = build_daily_report_text(123)

    accrue.assert_called_once_with(123)
    assert "📈 Total investido: R$ 1.250,50" in msg
    assert "💰 *Patrimônio total:* R$ 2.250,50" in msg
