from core.intent_classifier import classify
from core.intent_router import route
from core.types import IncomingMessage
from core.handlers.pending import resolve_delete
from parsers import parse_receita_despesa_natural
from db import add_launch_and_update_balance, create_card, get_balance, set_pending_action


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


def test_parse_gastei_com_data_no_meio_remove_data_da_nota(user_id):
    parsed = parse_receita_despesa_natural(user_id, "Mandei 50 reais para barbara dia 10/04")

    assert parsed is None

    parsed = parse_receita_despesa_natural(user_id, "gastei 50 reais para barbara dia 10/04")

    assert parsed is not None
    assert parsed["valor"] == 50
    assert parsed["criado_em"] is not None
    assert parsed["nota"] == "gastei 50 reais para barbara"
    assert parsed["alvo"] == "reais para barbara"


def test_classify_gastei_ontem_e_lancamento_nao_consulta():
    result = classify("gastei 40,80 com rifa ontem")

    assert result.intent == "launches.add"


def test_classify_ontem_gastei_e_lancamento():
    result = classify("ontem gastei 40,80 com rifa")

    assert result.intent == "launches.add"


def test_route_cartoes_lista_pelo_fluxo_central(user_id):
    create_card(user_id=user_id, name="Nubank", closing_day=1, due_day=8)
    msg = IncomingMessage(platform="discord", user_id=user_id, text="Cartões")
    result = classify("Cartões")

    response = route(result, msg)

    assert "Seus cartões" in response
    assert "Nubank" in response


def test_route_criar_cartao_pelo_fluxo_central(user_id):
    msg = IncomingMessage(
        platform="discord",
        user_id=user_id,
        text="Criar cartão Nubank fecha 1 vence 8",
    )
    result = classify(msg.text)

    response = route(result, msg)

    assert "criado/atualizado" in response
