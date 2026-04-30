from adapters.whatsapp.wa_parse import InboundMessage
from adapters.whatsapp.wa_runtime import _pending_supports_confirmation_buttons, process_message
from core.types import OutgoingMessage


def test_delete_pending_uses_confirmation_buttons():
    pending = {"action_type": "delete_launch", "payload": {"launch_id": 42}}

    assert _pending_supports_confirmation_buttons(pending) is True


def test_credit_set_primary_choose_does_not_use_confirmation_buttons():
    pending = {"action_type": "credit_card_set_primary", "payload": {"step": "choose"}}

    assert _pending_supports_confirmation_buttons(pending) is False


def test_credit_set_primary_confirmation_uses_buttons():
    pending = {"action_type": "credit_card_set_primary", "payload": {"card_id": 7}}

    assert _pending_supports_confirmation_buttons(pending) is True


def test_credit_setup_binary_steps_use_buttons():
    pending = {"action_type": "credit_card_setup", "payload": {"step": "reminder_opt_in"}}

    assert _pending_supports_confirmation_buttons(pending) is True


def test_credit_limit_question_stays_as_text_input():
    pending = {"action_type": "credit_card_setup", "payload": {"step": "credit_limit_ask"}}

    assert _pending_supports_confirmation_buttons(pending) is False


def test_autolink_com_comando_nao_interrompe_para_boas_vindas(monkeypatch):
    replies = []
    handled = []

    monkeypatch.setattr(
        "adapters.whatsapp.wa_runtime.get_or_create_canonical_user",
        lambda provider, external_id: 111,
    )
    monkeypatch.setattr(
        "adapters.whatsapp.wa_runtime.attempt_whatsapp_phone_link",
        lambda wa_id, current_user_id=None: {"status": "linked", "user_id": 222, "wa_phone": wa_id},
    )
    monkeypatch.setattr(
        "adapters.whatsapp.wa_runtime.send_welcome",
        lambda wa_id: (_ for _ in ()).throw(AssertionError("nao deveria enviar boas-vindas")),
    )
    monkeypatch.setattr("adapters.whatsapp.wa_runtime.log_system_event_sync", lambda *a, **k: None)
    monkeypatch.setattr("adapters.whatsapp.wa_runtime.send_typing_indicator", lambda *a, **k: None)
    monkeypatch.setattr("adapters.whatsapp.wa_runtime._seen_recent", lambda message_id: False)
    monkeypatch.setattr(
        "adapters.whatsapp.wa_runtime._send_reply_with_optional_buttons",
        lambda to, body, user_id=None: replies.append((to, body, user_id)),
    )

    def fake_handle_incoming(msg):
        handled.append(msg)
        return [OutgoingMessage(text=f"uid={msg.user_id} text={msg.text}")]

    monkeypatch.setattr("adapters.whatsapp.wa_runtime.handle_incoming", fake_handle_incoming)

    process_message(
        InboundMessage(
            wa_id="5565992741873",
            text="saldo",
            timestamp="123",
            attachments=[],
            raw={"id": "wamid.test", "type": "text"},
        )
    )

    assert len(handled) == 1
    assert handled[0].user_id == 222
    assert handled[0].text == "saldo"
    assert replies == [("5565992741873", "uid=222 text=saldo", 222)]
