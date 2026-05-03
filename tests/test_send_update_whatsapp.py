from scripts.send_update_whatsapp import (
    WA_UPDATES_DISABLE_ID,
    _dedupe_targets,
    build_quick_reply_buttons,
    get_test_targets,
)


def test_dedupe_targets_prefere_identidade_whatsapp_e_remove_alias_do_mesmo_numero():
    rows = [
        {
            "user_id": 1,
            "email": "um@example.com",
            "auth_phone": "556599929199",
            "identity_phone": "5565999929199",
        },
        {
            "user_id": 2,
            "email": "dois@example.com",
            "auth_phone": "556599929199",
            "identity_phone": "",
        },
    ]

    targets = _dedupe_targets(rows)

    assert len(targets) == 1
    assert targets[0].user_id == 1
    assert targets[0].to == "5565999929199"
    assert targets[0].source == "whatsapp"


def test_get_test_targets_normaliza_numero_informado():
    targets = get_test_targets("(65) 99992-9199")

    assert len(targets) == 1
    assert targets[0].user_id == 0
    assert targets[0].to == "5565999929199"
    assert targets[0].source == "test"


def test_build_quick_reply_buttons_usa_payload_de_opt_out_das_atualizacoes():
    assert build_quick_reply_buttons(False) is None
    assert build_quick_reply_buttons(True) == [{"index": 0, "payload": WA_UPDATES_DISABLE_ID}]
