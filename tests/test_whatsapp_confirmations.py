from adapters.whatsapp.wa_runtime import _pending_supports_confirmation_buttons


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
