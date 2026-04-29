from datetime import date
from unittest.mock import patch

from core.handlers import investments as h_investments


def test_list_investments_capitaliza_antes_de_responder():
    rows = [
        {"name": "CDB Banco Luso", "balance": 821.91, "rate": 1.16, "period": "cdi", "last_date": date(2026, 4, 16)},
        {"name": "Nu Reserva Planejada", "balance": 11287.35, "rate": 0.14, "period": "yearly", "last_date": date(2026, 4, 15)},
    ]

    with patch("core.handlers.investments.db.accrue_all_investments", return_value=rows) as accrue:
        msg = h_investments.list_investments(123)

    accrue.assert_called_once_with(123)
    assert "Atualizado até 16/04/2026" in msg
    assert "R$ 821,91 (116% CDI)" in msg
    assert "R$ 11.287,35 (14%)" in msg


def test_create_investment_usa_fluxo_que_aceita_cdi():
    with patch("core.handlers.investments.db.create_investment_db", return_value=(99, 7, "CDB Teste")) as create_db:
        msg = h_investments.create(123, "CDB Teste 116% CDI", "criar investimento CDB Teste 116% CDI")

    create_db.assert_called_once_with(
        123,
        "CDB Teste",
        1.16,
        "cdi",
        nota="criar investimento CDB Teste 116% CDI",
        asset_type="CDB",
        indexer="pct_cdi",
        tax_profile="regressive_ir_iof",
    )
    assert msg == "✅ Investimento criado: **CDB Teste** — 116% CDI (id 99)"


def test_create_investment_aceita_ipca_spread_e_tipo_isento():
    with patch("core.handlers.investments.db.create_investment_db", return_value=(100, 8, "LCI Banco Verde")) as create_db:
        msg = h_investments.create(
            123,
            "LCI Banco Verde IPCA + 7,43% a.a.",
            "criar investimento LCI Banco Verde IPCA + 7,43% a.a.",
        )

    create_db.assert_called_once_with(
        123,
        "LCI Banco Verde",
        0.0743,
        "ipca_spread",
        nota="criar investimento LCI Banco Verde IPCA + 7,43% a.a.",
        asset_type="LCI",
        indexer="ipca_spread",
        tax_profile="exempt_ir_iof",
    )
    assert "IPCA + 7,43% a.a." in msg


def test_create_investment_com_aporte_inicial():
    with patch("core.handlers.investments.db.create_investment_db", return_value=(101, 9, "CDB Banco")) as create_db:
        msg = h_investments.create(
            123,
            "CDB Banco 110% CDI valor 10000",
            "criar investimento CDB Banco 110% CDI valor 10000",
        )

    create_db.assert_called_once_with(
        123,
        "CDB Banco",
        1.10,
        "cdi",
        nota="criar investimento CDB Banco 110% CDI valor 10000",
        asset_type="CDB",
        indexer="pct_cdi",
        tax_profile="regressive_ir_iof",
        initial_amount=10000.0,
    )
    assert msg == "✅ Investimento criado: **CDB Banco** — 110% CDI (id 101)"
