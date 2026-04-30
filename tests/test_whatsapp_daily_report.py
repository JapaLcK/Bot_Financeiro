import os
from datetime import date
from unittest.mock import patch

from adapters.whatsapp.wa_app import _daily_report_tick, _dedupe_whatsapp_targets


def test_daily_report_tick_nao_envia_quando_claim_falha():
    with patch.dict(os.environ, {"WA_PROACTIVE_TEMPLATE_NAME": "daily_opener"}, clear=False), \
         patch("adapters.whatsapp.wa_app.now_tz") as now_mock, \
         patch("adapters.whatsapp.wa_app.list_users_with_daily_report_enabled", return_value=[123]), \
         patch("adapters.whatsapp.wa_app.get_daily_report_prefs", return_value={"enabled": True, "hour": 9, "minute": 0}), \
         patch("adapters.whatsapp.wa_app.claim_daily_report_send", return_value=False) as claim_mock, \
         patch("adapters.whatsapp.wa_app.build_daily_report_text") as build_mock, \
         patch("adapters.whatsapp.wa_app.build_due_bill_reminders", return_value=[]), \
         patch("adapters.whatsapp.wa_app.list_identities_by_user", return_value=[{"provider": "whatsapp", "external_id": "5511999999999"}]), \
         patch("adapters.whatsapp.wa_app.send_template") as template_mock:
        now_mock.return_value = type("FakeNow", (), {"hour": 9, "minute": 0, "date": lambda self: date(2026, 4, 23)})()
        _daily_report_tick()

    claim_mock.assert_called_once_with(123, date(2026, 4, 23))
    build_mock.assert_not_called()
    template_mock.assert_not_called()


def test_daily_report_tick_nao_envia_whatsapp_sem_template_configurado():
    with patch.dict(os.environ, {"WA_PROACTIVE_TEMPLATE_NAME": ""}, clear=False), \
         patch("adapters.whatsapp.wa_app.now_tz") as now_mock, \
         patch("adapters.whatsapp.wa_app.list_users_with_daily_report_enabled", return_value=[123]), \
         patch("adapters.whatsapp.wa_app.get_daily_report_prefs", return_value={"enabled": True, "hour": 9, "minute": 0}), \
         patch("adapters.whatsapp.wa_app.claim_daily_report_send") as claim_mock, \
         patch("adapters.whatsapp.wa_app.build_daily_report_text", return_value="resumo"), \
         patch("adapters.whatsapp.wa_app.build_due_bill_reminders", return_value=[]), \
         patch("adapters.whatsapp.wa_app.list_identities_by_user", return_value=[{"provider": "whatsapp", "external_id": "5511999999999"}]), \
         patch("adapters.whatsapp.wa_app.send_template") as template_mock:
        now_mock.return_value = type("FakeNow", (), {"hour": 9, "minute": 0, "date": lambda self: date(2026, 4, 23)})()
        _daily_report_tick()

    claim_mock.assert_not_called()
    template_mock.assert_not_called()


def test_daily_report_tick_envia_apenas_template_quando_configurado():
    with patch.dict(os.environ, {
        "WA_PROACTIVE_TEMPLATE_NAME": "daily_opener",
        "WA_PROACTIVE_TEMPLATE_LANGUAGE": "pt_BR",
        "WA_PROACTIVE_TEMPLATE_INCLUDE_REPORT": "0",
        "WA_PROACTIVE_TEMPLATE_STOP_BUTTON": "0",
    }, clear=False), \
         patch("adapters.whatsapp.wa_app.now_tz") as now_mock, \
         patch("adapters.whatsapp.wa_app.list_users_with_daily_report_enabled", return_value=[123]), \
         patch("adapters.whatsapp.wa_app.get_daily_report_prefs", return_value={"enabled": True, "hour": 9, "minute": 0}), \
         patch("adapters.whatsapp.wa_app.claim_daily_report_send", return_value=True), \
         patch("adapters.whatsapp.wa_app.build_daily_report_text", return_value="resumo"), \
         patch("adapters.whatsapp.wa_app.build_due_bill_reminders", return_value=[]), \
         patch("adapters.whatsapp.wa_app.list_identities_by_user", return_value=[{"provider": "whatsapp", "external_id": "5511999999999"}]), \
         patch("adapters.whatsapp.wa_app.send_template") as template_mock:
        now_mock.return_value = type("FakeNow", (), {"hour": 9, "minute": 0, "date": lambda self: date(2026, 4, 23)})()
        _daily_report_tick()

    template_mock.assert_called_once_with(
        "5511999999999",
        "daily_opener",
        language_code="pt_BR",
        named_body_params=None,
        quick_reply_buttons=None,
    )


def test_daily_report_tick_envia_resumo_como_parametro_do_template_quando_habilitado():
    with patch.dict(os.environ, {
        "WA_PROACTIVE_TEMPLATE_NAME": "daily_report",
        "WA_PROACTIVE_TEMPLATE_LANGUAGE": "pt_BR",
        "WA_PROACTIVE_TEMPLATE_INCLUDE_REPORT": "1",
        "WA_PROACTIVE_TEMPLATE_STOP_BUTTON": "1",
    }, clear=False), \
         patch("adapters.whatsapp.wa_app.now_tz") as now_mock, \
         patch("adapters.whatsapp.wa_app.list_users_with_daily_report_enabled", return_value=[123]), \
         patch("adapters.whatsapp.wa_app.get_daily_report_prefs", return_value={"enabled": True, "hour": 9, "minute": 0}), \
         patch("adapters.whatsapp.wa_app.claim_daily_report_send", return_value=True), \
         patch("adapters.whatsapp.wa_app.build_daily_report_text", return_value="resumo"), \
         patch("adapters.whatsapp.wa_app.build_daily_report_summary", return_value={
             "saldo": "R$ 1.000,00",
             "gastos": "R$ 42,90",
             "receita": "R$ 10,00",
             "lancamentos": "2",
         }), \
         patch("adapters.whatsapp.wa_app.build_due_bill_reminders", return_value=[{"message": "lembrete", "card_id": 7}]), \
         patch("adapters.whatsapp.wa_app.list_identities_by_user", return_value=[{"provider": "whatsapp", "external_id": "5511999999999"}]), \
         patch("adapters.whatsapp.wa_app.mark_card_reminder_sent"), \
         patch("adapters.whatsapp.wa_app.send_template") as template_mock:
        now_mock.return_value = type("FakeNow", (), {"hour": 9, "minute": 0, "date": lambda self: date(2026, 4, 23)})()
        _daily_report_tick()

    template_mock.assert_called_once_with(
        "5511999999999",
        "daily_report",
        language_code="pt_BR",
        named_body_params={
            "saldo": "R$ 1.000,00",
            "gastos": "R$ 42,90",
            "receita": "R$ 10,00",
            "lancamentos": "2",
        },
        quick_reply_buttons=[{"index": 0, "payload": "daily_report_disable"}],
    )


def test_dedupe_whatsapp_targets_remove_destinos_repetidos_do_mesmo_numero():
    ids = [
        {"provider": "whatsapp", "external_id": "11999999999"},
        {"provider": "whatsapp", "external_id": "5511999999999"},
        {"provider": "discord", "external_id": "123"},
        {"provider": "whatsapp", "external_id": "5511888888888"},
    ]

    targets = _dedupe_whatsapp_targets(ids)

    assert targets == ["5511999999999", "5511888888888"]


def test_dedupe_whatsapp_targets_remove_variacao_sem_nono_digito():
    ids = [
        {"provider": "whatsapp", "external_id": "556599929199"},
        {"provider": "whatsapp", "external_id": "5565999929199"},
    ]

    targets = _dedupe_whatsapp_targets(ids)

    assert targets == ["5565999929199"]


def test_strip_daily_report_disable_hint_remove_instrucao_de_texto():
    from adapters.whatsapp.wa_app import _strip_daily_report_disable_hint

    raw = "Linha 1\n\n⚙️ Para desligar o report diário automatico:\n*desligar report diario*"

    assert _strip_daily_report_disable_hint(raw) == "Linha 1"
