from core.intent_classifier import classify
from core.handlers.pending import resolve_delete
from parsers import parse_receita_despesa_natural
from db import add_launch_and_update_balance, get_balance, set_pending_action


def test_parse_paguei_conta_de_luz(user_id):
    parsed = parse_receita_despesa_natural(user_id, "paguei 120 conta de luz")

    assert parsed is not None
    assert parsed["tipo"] == "despesa"
    assert parsed["valor"] == 120
    assert parsed["categoria"] == "moradia"
    assert parsed["alvo"] == "conta de luz"


def test_classify_apagar_id_com_hash():
    result = classify("apagar id #712")

    assert result.intent == "launches.delete"
    assert result.entities["launch_id"] == 712


def test_classify_mostra_meus_lancamentos():
    result = classify("mostra meus lancamentos")

    assert result.intent == "launches.list"


def test_resolve_delete_uses_correct_argument_order(user_id):
    add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    launch_id, _ = add_launch_and_update_balance(user_id, "despesa", 200, "conta de luz", "paguei 200 conta de luz")
    set_pending_action(user_id, "delete_launch", {"launch_id": int(launch_id)})

    response = resolve_delete(user_id, confirmed=True)

    assert "apagado" in response.lower()
    assert get_balance(user_id) == 1000
