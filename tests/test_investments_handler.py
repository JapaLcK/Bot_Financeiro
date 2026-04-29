from datetime import date
from unittest.mock import patch

from core.handlers import investments as h_investments


def test_list_investments_capitaliza_antes_de_responder():
    rows = [
        {"name": "CDB Banco Luso", "balance": 821.91, "rate": 1.16, "period": "cdi", "last_date": date(2026, 4, 16)},
        {"name": "Nu Reserva Planejada", "balance": 11287.35, "rate": 0.14, "period": "yearly", "last_date": date(2026, 4, 15)},
    ]

    with patch("core.handlers.investments.db.accrue_all_investments", return_value=rows) as accrue, \
         patch("core.handlers.investments.build_dashboard_link", return_value="https://app.test/d/abc") as link:
        msg = h_investments.list_investments(123)

    accrue.assert_called_once_with(123)
    link.assert_called_once_with(123, view="investments")
    assert "Atualizado até 16/04/2026" in msg
    assert "R$ 821,91 (116% CDI)" in msg
    assert "R$ 11.287,35 (14%)" in msg
    assert "https://app.test/d/abc" in msg


def test_create_investment_redireciona_para_dashboard():
    with patch("core.handlers.investments.db.accrue_all_investments", return_value=[]) as accrue, \
         patch("core.handlers.investments.build_dashboard_link", return_value="https://app.test/d/abc") as link, \
         patch("core.handlers.investments.db.create_investment_db") as create_db:
        msg = h_investments.create(123, "CDB Teste 116% CDI", "criar investimento CDB Teste 116% CDI")

    create_db.assert_not_called()
    accrue.assert_called_once_with(123)
    link.assert_called_once_with(123, view="investments")
    assert "criação de investimentos agora é feita pelo dashboard" in msg
    assert "https://app.test/d/abc" in msg


def test_create_investment_nao_cria_ipca_spread_pelo_bot():
    with patch("core.handlers.investments.db.accrue_all_investments", return_value=[]) as accrue, \
         patch("core.handlers.investments.build_dashboard_link", return_value="https://app.test/d/abc") as link, \
         patch("core.handlers.investments.db.create_investment_db") as create_db:
        msg = h_investments.create(
            123,
            "LCI Banco Verde IPCA + 7,43% a.a.",
            "criar investimento LCI Banco Verde IPCA + 7,43% a.a.",
        )

    create_db.assert_not_called()
    accrue.assert_called_once_with(123)
    link.assert_called_once_with(123, view="investments")
    assert "dashboard" in msg
    assert "https://app.test/d/abc" in msg


def test_create_investment_com_aporte_inicial_redireciona_para_dashboard():
    with patch("core.handlers.investments.db.accrue_all_investments", return_value=[]) as accrue, \
         patch("core.handlers.investments.build_dashboard_link", return_value="https://app.test/d/abc") as link, \
         patch("core.handlers.investments.db.create_investment_db") as create_db:
        msg = h_investments.create(
            123,
            "CDB Banco 110% CDI valor 10000",
            "criar investimento CDB Banco 110% CDI valor 10000",
        )

    create_db.assert_not_called()
    accrue.assert_called_once_with(123)
    link.assert_called_once_with(123, view="investments")
    assert "https://app.test/d/abc" in msg
