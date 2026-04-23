from datetime import date
from unittest.mock import patch

from adapters.whatsapp.wa_app import _daily_report_tick, _dedupe_whatsapp_targets


def test_daily_report_tick_nao_envia_quando_claim_falha():
    with patch("adapters.whatsapp.wa_app.now_tz") as now_mock, \
         patch("adapters.whatsapp.wa_app.list_users_with_daily_report_enabled", return_value=[123]), \
         patch("adapters.whatsapp.wa_app.get_daily_report_prefs", return_value={"enabled": True, "hour": 9, "minute": 0}), \
         patch("adapters.whatsapp.wa_app.claim_daily_report_send", return_value=False) as claim_mock, \
         patch("adapters.whatsapp.wa_app.build_daily_report_text") as build_mock, \
         patch("adapters.whatsapp.wa_app.build_due_bill_reminders", return_value=[]), \
         patch("adapters.whatsapp.wa_app.list_identities_by_user", return_value=[{"provider": "whatsapp", "external_id": "5511999999999"}]), \
         patch("adapters.whatsapp.wa_app.send_text") as send_mock:
        now_mock.return_value = type("FakeNow", (), {"hour": 9, "minute": 0, "date": lambda self: date(2026, 4, 23)})()
        _daily_report_tick()

    claim_mock.assert_called_once_with(123, date(2026, 4, 23))
    build_mock.assert_not_called()
    send_mock.assert_not_called()


def test_daily_report_tick_envia_quando_claim_tem_sucesso():
    with patch("adapters.whatsapp.wa_app.now_tz") as now_mock, \
         patch("adapters.whatsapp.wa_app.list_users_with_daily_report_enabled", return_value=[123]), \
         patch("adapters.whatsapp.wa_app.get_daily_report_prefs", return_value={"enabled": True, "hour": 9, "minute": 0}), \
         patch("adapters.whatsapp.wa_app.claim_daily_report_send", return_value=True), \
         patch("adapters.whatsapp.wa_app.build_daily_report_text", return_value="resumo"), \
         patch("adapters.whatsapp.wa_app.build_due_bill_reminders", return_value=[]), \
         patch("adapters.whatsapp.wa_app.list_identities_by_user", return_value=[{"provider": "whatsapp", "external_id": "5511999999999"}]), \
         patch("adapters.whatsapp.wa_app.send_text") as send_mock:
        now_mock.return_value = type("FakeNow", (), {"hour": 9, "minute": 0, "date": lambda self: date(2026, 4, 23)})()
        _daily_report_tick()

    send_mock.assert_called_once_with("5511999999999", "resumo")


def test_dedupe_whatsapp_targets_remove_destinos_repetidos_do_mesmo_numero():
    ids = [
        {"provider": "whatsapp", "external_id": "11999999999"},
        {"provider": "whatsapp", "external_id": "5511999999999"},
        {"provider": "discord", "external_id": "123"},
        {"provider": "whatsapp", "external_id": "5511888888888"},
    ]

    targets = _dedupe_whatsapp_targets(ids)

    assert targets == ["11999999999", "5511888888888"]
