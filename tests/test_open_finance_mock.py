from db import (
    create_mock_open_finance_connection,
    disconnect_open_finance_connection,
    get_open_finance_snapshot,
)
from core.services.open_finance import handle_open_finance_whatsapp_command


def test_mock_open_finance_connection_syncs_accounts_and_transactions(user_id):
    result = create_mock_open_finance_connection(user_id, "nubank")

    assert result["connection"]["institution_name"] == "Nubank"
    assert result["accounts_synced"] == 2
    assert result["transactions_synced"] == 7

    snapshot = get_open_finance_snapshot(user_id)
    assert len(snapshot["connections"]) == 1
    assert len(snapshot["accounts"]) == 2
    assert len(snapshot["transactions"]) == 7


def test_whatsapp_open_finance_command_flow(user_id):
    response = handle_open_finance_whatsapp_command(user_id, "conectar openfinance itau")
    assert response is not None
    assert "configurações seguras" in response
    assert "view=open-finance" in response
    assert get_open_finance_snapshot(user_id)["connections"] == []

    status = handle_open_finance_whatsapp_command(user_id, "openfinance")
    assert status is not None
    assert "view=open-finance" in status

    removed = handle_open_finance_whatsapp_command(user_id, "desconectar openfinance")
    assert removed is not None
    assert "view=open-finance" in removed
    assert get_open_finance_snapshot(user_id)["connections"] == []


def test_disconnect_open_finance_connection_is_scoped_to_user(user_id):
    create_mock_open_finance_connection(user_id, "bradesco")
    deleted = disconnect_open_finance_connection(user_id)

    assert deleted == 1
    assert get_open_finance_snapshot(user_id)["connections"] == []
