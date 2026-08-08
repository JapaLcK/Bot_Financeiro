"""Saldo consolidado (Carteira + bancos Open Finance) nas superfícies de leitura.

Regressão do bug: conectar um banco não atualizava o saldo mostrado ao usuário —
bot, relatórios e chat IA liam só accounts.balance (Carteira manual), ignorando os
saldos autoritativos das contas BANK sincronizadas via Pluggy.

Lançamento em fases: o consolidado fica atrás do gate consolidated_balance_enabled
(default = só as contas de teste). Os testes de exibição ligam o gate global via
OF_CONSOLIDATED_BALANCE_ENABLED; os de gate-off garantem a saída antiga.
"""
from decimal import Decimal

import pytest

import db
from core.handlers import balance as h_balance


@pytest.fixture()
def consolidated_on(monkeypatch):
    monkeypatch.setenv("OF_CONSOLIDATED_BALANCE_ENABLED", "1")


def _connect_fake_bank(user_id: int, balance: str = "4320.75") -> int:
    """Cria uma conexão Pluggy + 1 conta BANK sincronizada com o saldo dado."""
    connection = db.save_pluggy_open_finance_item(
        user_id,
        {"id": f"item-test-{user_id}", "connector": {"id": 612, "name": "Nubank"}, "status": "UPDATED"},
    )
    db.save_open_finance_sync(
        connection["id"],
        [{
            "provider_account_id": f"acc-test-{user_id}",
            "name": "Nubank Conta",
            "type": "BANK",
            "subtype": "CHECKING_ACCOUNT",
            "currency": "BRL",
            "balance": Decimal(balance),
            "raw": {},
            "transactions": [],
        }],
    )
    return connection["id"]


def test_get_consolidated_balance_sem_banco(user_id):
    db.add_launch_and_update_balance(user_id, "receita", 100, None, "seed")
    cb = db.get_consolidated_balance(user_id)
    assert cb["manual"] == Decimal("100")
    assert cb["open_finance_bank"] == Decimal("0")
    assert cb["of_bank_count"] == 0
    assert cb["consolidated"] == Decimal("100")


def test_get_consolidated_balance_soma_banco_conectado(user_id):
    db.add_launch_and_update_balance(user_id, "receita", 100, None, "seed")
    _connect_fake_bank(user_id, "4320.75")

    cb = db.get_consolidated_balance(user_id)
    assert cb["manual"] == Decimal("100")
    assert cb["open_finance_bank"] == Decimal("4320.75")
    assert cb["of_bank_count"] == 1
    assert cb["consolidated"] == Decimal("4420.75")


def test_reconectar_banco_nao_duplica_saldo(user_id):
    """Reconectar cria outra connection com a MESMA conta — o saldo não pode dobrar."""
    _connect_fake_bank(user_id, "500.00")
    # segunda conexão (novo item) com a mesma conta real (mesmo provider_account_id)
    connection = db.save_pluggy_open_finance_item(
        user_id,
        {"id": f"item-test-2-{user_id}", "connector": {"id": 612, "name": "Nubank"}, "status": "UPDATED"},
    )
    db.save_open_finance_sync(
        connection["id"],
        [{
            "provider_account_id": f"acc-test-{user_id}",
            "name": "Nubank Conta",
            "type": "BANK",
            "subtype": "CHECKING_ACCOUNT",
            "currency": "BRL",
            "balance": Decimal("650.00"),
            "raw": {},
            "transactions": [],
        }],
    )

    cb = db.get_consolidated_balance(user_id)
    assert cb["of_bank_count"] == 1
    assert cb["open_finance_bank"] == Decimal("650.00")  # saldo da conexão mais recente


def test_conta_em_moeda_estrangeira_fica_fora_da_soma(user_id):
    """Sem conversão de câmbio, USD 100 não pode entrar como R$ 100 no total."""
    db.add_launch_and_update_balance(user_id, "receita", 100, None, "seed")
    connection = db.save_pluggy_open_finance_item(
        user_id,
        {"id": f"item-test-usd-{user_id}", "connector": {"id": 612, "name": "Nubank"}, "status": "UPDATED"},
    )
    db.save_open_finance_sync(
        connection["id"],
        [
            {
                "provider_account_id": f"acc-test-usd-{user_id}",
                "name": "Nubank Global", "type": "BANK", "subtype": "CHECKING_ACCOUNT",
                "currency": "USD", "balance": Decimal("100.00"), "raw": {}, "transactions": [],
            },
            {
                "provider_account_id": f"acc-test-brl-{user_id}",
                "name": "Nubank Conta", "type": "BANK", "subtype": "CHECKING_ACCOUNT",
                "currency": "BRL", "balance": Decimal("300.00"), "raw": {}, "transactions": [],
            },
        ],
    )

    cb = db.get_consolidated_balance(user_id)
    assert cb["of_bank_count"] == 1          # só a conta BRL conta
    assert cb["open_finance_bank"] == Decimal("300.00")
    assert cb["consolidated"] == Decimal("400.00")


def test_handler_saldo_mostra_consolidado_com_banco(user_id, consolidated_on):
    db.add_launch_and_update_balance(user_id, "receita", 100, None, "seed")
    _connect_fake_bank(user_id, "4320.75")

    out = h_balance.check(user_id)
    assert "Saldo total" in out
    assert "R$ 4.420,75" in out
    assert "Carteira" in out and "R$ 100,00" in out
    assert "Bancos conectados" in out and "R$ 4.320,75" in out


def test_handler_saldo_sem_banco_mantem_conta_corrente(user_id):
    db.add_launch_and_update_balance(user_id, "receita", 100, None, "seed")
    out = h_balance.check(user_id)
    assert "Conta Corrente" in out
    assert "R$ 100,00" in out


def test_ai_chat_tool_get_balance_consolidado(user_id, consolidated_on):
    from core.services.ai_chat.tools.balance import _get_balance

    db.add_launch_and_update_balance(user_id, "receita", 100, None, "seed")
    _connect_fake_bank(user_id, "900.00")

    res = _get_balance(user_id, {})
    assert res["balance"] == 1000.0
    assert res["wallet_balance"] == 100.0
    assert res["connected_banks_balance"] == 900.0
    assert res["connected_bank_accounts"] == 1


# ── Gate beta desligado (fora do allowlist de teste): comportamento antigo ─────

def test_gate_off_handler_mantem_carteira_mesmo_com_banco(user_id):
    """Fora do beta, conectar banco NÃO muda o /saldo (segue Carteira manual)."""
    db.add_launch_and_update_balance(user_id, "receita", 100, None, "seed")
    _connect_fake_bank(user_id, "4320.75")

    out = h_balance.check(user_id)
    assert "Conta Corrente" in out
    assert "R$ 100,00" in out
    assert "Saldo total" not in out
    assert "4.320,75" not in out


def test_gate_off_ai_chat_tool_formato_antigo(user_id):
    from core.services.ai_chat.tools.balance import _get_balance

    db.add_launch_and_update_balance(user_id, "receita", 100, None, "seed")
    _connect_fake_bank(user_id, "900.00")

    res = _get_balance(user_id, {})
    assert res == {"balance": 100.0}


def test_gate_liga_por_email_de_teste(user_id, monkeypatch):
    """O allowlist default são os e-mails de teste (hiago/lucas)."""
    from core.services.plan_service import consolidated_balance_enabled

    monkeypatch.delenv("OF_CONSOLIDATED_BALANCE_ENABLED", raising=False)
    assert consolidated_balance_enabled(user_id, email="lucaskuramoti06@gmail.com")
    assert consolidated_balance_enabled(user_id, email="hiagojo2016@gmail.com")
    assert not consolidated_balance_enabled(user_id, email="outro@exemplo.com")
