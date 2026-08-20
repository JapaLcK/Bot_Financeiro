from datetime import date
from unittest.mock import patch

import pytest

from core.handlers import investments as h_investments


def _carteira_com(saldo: float):
    """Carteira cobrindo o valor, para o teste CHEGAR na chamada que quer exercitar.

    O aporte agora resolve a origem antes de tocar no banco (core/services/funding.py).
    Sem saldo em lugar nenhum ele responde "nenhum saldo cobre" e volta — e o mock de
    `investment_deposit_from_account` nunca é usado, o que faria o teste medir nada.
    """
    return patch("db.get_consolidated_balance", return_value={
        "manual": saldo, "open_finance_bank": 0, "of_bank_count": 0,
        "consolidated": saldo,
    })


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
    assert "CDB Banco Luso" in msg
    assert "Nu Reserva Planejada" in msg
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


# ─── Aporte/resgate: nenhum código cru de erro pode chegar ao usuário ────────
#
# Regressão do bug relatado no WhatsApp: "Investi 870 em tesouro direto" com o
# investimento não cadastrado devolvia literalmente "Erro ao aportar:
# INV_NOT_FOUND". A causa era casar substring — `"not found" in err.lower()`
# nunca casa com "inv_not_found" (espaço × underscore).

# Todos os códigos que db/investments.py levanta nos caminhos de aporte/resgate
# (inclui os do accrual, que roda dentro das duas operações). Enumerados à mão a
# partir de `grep -nE 'raise (ValueError|LookupError|RuntimeError)\(' db/investments.py`
# em vez de testar só o caso do print — é a classe que precisa ficar fechada.
_DEPOSIT_ERRORS = [
    LookupError("INV_NOT_FOUND"),
    ValueError("INSUFFICIENT_ACCOUNT"),
    ValueError("AMOUNT_INVALID"),
    ValueError("PURCHASE_DATE_FUTURE"),
    ValueError("INVALID_PERIOD"),
    ValueError("INVALID_RATE"),
    RuntimeError("ACCOUNT_MISSING"),
    RuntimeError("CDI_DAILY_NOT_AVAILABLE"),
    RuntimeError("INVESTMENT_LOOKUP_FAILED"),
]

_WITHDRAW_ERRORS = [
    LookupError("INV_NOT_FOUND"),
    ValueError("INSUFFICIENT_INVEST"),
    ValueError("AMOUNT_INVALID"),
    RuntimeError("CDI_DAILY_NOT_AVAILABLE"),
]


def _assert_sem_codigo_cru(msg, exc):
    code = str(exc)
    assert code not in msg, f"código cru {code!r} vazou para o usuário: {msg!r}"
    # o prefixo antigo ("Erro ao aportar: <CODE>") não pode voltar
    assert "Erro ao aportar:" not in msg
    assert "Erro ao resgatar:" not in msg
    assert msg.strip(), "resposta vazia"


@pytest.mark.parametrize("exc", _DEPOSIT_ERRORS, ids=lambda e: str(e))
def test_deposit_nunca_devolve_codigo_cru(exc):
    with patch("core.handlers.investments.db.investment_deposit_from_account", side_effect=exc), \
         patch("core.handlers.investments.db.accrue_all_investments", return_value=[]), \
         patch("core.handlers.investments.build_dashboard_link", return_value="https://app.test/d/abc"):
        msg = h_investments.deposit(
            123, "investi 870 em tesouro direto",
            {"investment_name": "tesouro direto", "amount": 870},
        )
    _assert_sem_codigo_cru(msg, exc)


@pytest.mark.parametrize("exc", _WITHDRAW_ERRORS, ids=lambda e: str(e))
def test_withdraw_nunca_devolve_codigo_cru(exc):
    with patch("core.handlers.investments.db.investment_withdraw_to_account", side_effect=exc), \
         patch("core.handlers.investments.db.accrue_all_investments", return_value=[]), \
         patch("core.handlers.investments.build_dashboard_link", return_value="https://app.test/d/abc"):
        msg = h_investments.withdraw(
            123, "resgatei 100 do tesouro direto",
            {"investment_name": "tesouro direto", "amount": 100},
        )
    _assert_sem_codigo_cru(msg, exc)


def test_deposit_investimento_inexistente_manda_pro_dashboard():
    """Carteira vazia: a resposta precisa dizer COMO cadastrar, não só que não achou.

    O cadastro é exclusivo do dashboard (ver `create`), então sem o link o
    usuário fica sem saída — foi exatamente o que o INV_NOT_FOUND cru fazia.
    """
    with _carteira_com(1000), \
         patch("core.handlers.investments.db.investment_deposit_from_account",
               side_effect=LookupError("INV_NOT_FOUND")), \
         patch("core.handlers.investments.db.accrue_all_investments", return_value=[]), \
         patch("core.handlers.investments.build_dashboard_link", return_value="https://app.test/d/abc"):
        msg = h_investments.deposit(
            123, "Investi 870 em tesouro direto",
            {"investment_name": "tesouro direto", "amount": 870},
        )

    assert "INV_NOT_FOUND" not in msg
    assert "Tesouro Direto" in msg          # capitalizado como no resto do bot
    assert "dashboard" in msg.lower()
    assert "https://app.test/d/abc" in msg


def test_deposit_investimento_inexistente_com_carteira_lista_os_existentes():
    rows = [{"name": "CDB Banco Luso", "balance": 821.91, "rate": 1.16,
             "period": "cdi", "last_date": date(2026, 4, 16)}]
    with _carteira_com(1000), \
         patch("core.handlers.investments.db.investment_deposit_from_account",
               side_effect=LookupError("INV_NOT_FOUND")), \
         patch("core.handlers.investments.db.accrue_all_investments", return_value=rows) as accrue, \
         patch("core.handlers.investments.build_dashboard_link", return_value="https://app.test/d/abc"):
        msg = h_investments.deposit(
            123, "investi 870 em tesouro direto",
            {"investment_name": "tesouro direto", "amount": 870},
        )

    assert "Não encontrei" in msg
    assert "CDB Banco Luso" in msg
    # a lista é reaproveitada, não buscada duas vezes
    assert accrue.call_count == 1


def test_deposit_saldo_insuficiente_continua_com_mensagem_propria():
    with _carteira_com(999999), \
         patch("core.handlers.investments.db.investment_deposit_from_account",
               side_effect=ValueError("INSUFFICIENT_ACCOUNT")), \
         patch("core.handlers.investments.build_dashboard_link", return_value="https://app.test/d/abc"):
        msg = h_investments.deposit(
            123, "investi 999999 no cdb", {"investment_name": "CDB", "amount": 999999},
        )
    # a mensagem agora nomeia o saldo e mostra o número, em vez do genérico
    assert "Saldo insuficiente" in msg
    assert "R$ 999.999,00" in msg


def test_deposit_sucesso_responde_com_id():
    with _carteira_com(1000), \
         patch("core.handlers.investments.db.investment_deposit_from_account",
               return_value=(55, 100.0, 870.0, "Tesouro Selic 2029")), \
         patch("core.handlers.investments.db.display_id_for", return_value=7):
        msg = h_investments.deposit(
            123, "investi 870 no tesouro selic 2029",
            {"investment_name": "Tesouro Selic 2029", "amount": 870},
        )
    assert "✅" in msg
    assert "Tesouro Selic 2029" in msg
    assert "#7" in msg


def test_deposit_deixa_plan_limit_subir():
    """PlanLimitExceeded tem mensagem amigável e é tratado no handle_incoming —
    o catch genérico não pode engoli-la."""
    from core.services.plan_limits import PlanLimitExceeded

    with _carteira_com(1000), \
         patch("core.handlers.investments.db.investment_deposit_from_account",
               side_effect=PlanLimitExceeded("investments", "Faça upgrade!")):
        with pytest.raises(PlanLimitExceeded):
            h_investments.deposit(
                123, "investi 870 no cdb", {"investment_name": "CDB", "amount": 870},
            )
