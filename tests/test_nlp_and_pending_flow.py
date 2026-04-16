from core.intent_classifier import classify
from core.intent_router import route
from core.reports.reports_daily import build_due_bill_reminders
from core.services.quick_entry import handle_quick_entry
from core.types import IncomingMessage
from core.handlers.pending import resolve_delete
from datetime import date
from parsers import parse_receita_despesa_natural
from db import (
    add_credit_purchase,
    add_credit_purchase_installments,
    add_launch_and_update_balance,
    create_card,
    get_balance,
    get_open_bill_summary,
    get_pending_action,
    list_cards,
    set_default_card,
    set_pending_action,
)


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
    parsed = parse_receita_despesa_natural(user_id, "gastei 50 reais para barbara dia 10/04")

    assert parsed is not None
    assert parsed["valor"] == 50
    assert parsed["criado_em"] is not None
    assert parsed["nota"] == "gastei 50 reais para barbara"
    assert parsed["alvo"] == "barbara"


def test_parse_mandei_com_data_no_meio_remove_data_da_nota(user_id):
    parsed = parse_receita_despesa_natural(user_id, "Mandei 50 reais para barbara dia 10/04")

    assert parsed is not None
    assert parsed["tipo"] == "despesa"
    assert parsed["valor"] == 50
    assert parsed["criado_em"] is not None
    assert parsed["nota"] == "Mandei 50 reais para barbara"
    assert parsed["alvo"] == "barbara"


def test_classify_gastei_ontem_e_lancamento_nao_consulta():
    result = classify("gastei 40,80 com rifa ontem")

    assert result.intent == "launches.add"


def test_classify_ontem_gastei_e_lancamento():
    result = classify("ontem gastei 40,80 com rifa")

    assert result.intent == "launches.add"


def test_classify_gastei_no_cartao_vai_para_credito():
    result = classify("gastei 150 no cartao nubank")

    assert result.intent == "credit.handle"


def test_classify_apagar_ct_vai_para_credito():
    result = classify("apagar CC16")

    assert result.intent == "credit.handle"


def test_classify_apagar_compra_vai_para_credito():
    result = classify("apagar CC16")

    assert result.intent == "credit.handle"


def test_route_gasto_no_cartao_nao_debita_saldo(user_id):
    card_id = create_card(user_id=user_id, name="Nubank", closing_day=1, due_day=8)
    set_default_card(user_id, card_id)
    msg = IncomingMessage(platform="discord", user_id=user_id, text="gastei 150 no cartao nubank")

    response = route(classify(msg.text), msg)

    assert "Compra no crédito registrada" in response
    assert "Código da compra" in response
    assert get_balance(user_id) == 0
    bill, _items = get_open_bill_summary(user_id, card_id, as_of=date.today())
    assert float(bill["total"]) == 150.0


def test_quick_entry_gasto_no_cartao_nao_debita_saldo(user_id):
    card_id = create_card(user_id=user_id, name="Nubank", closing_day=1, due_day=8)
    set_default_card(user_id, card_id)

    response = handle_quick_entry(user_id, "gastei 90 no cartao nubank")

    assert response is not None
    assert "Compra no crédito registrada" in response.text
    assert "Código da compra" in response.text
    assert get_balance(user_id) == 0
    bill, _items = get_open_bill_summary(user_id, card_id, as_of=date.today())
    assert float(bill["total"]) == 90.0


def test_route_apagar_ct_remove_compra_da_fatura(user_id):
    card_id = create_card(user_id=user_id, name="Nubank", closing_day=1, due_day=8)
    tx_id, _due, _bill_id = add_credit_purchase(
        user_id=user_id,
        card_id=card_id,
        valor=150.0,
        categoria="outros",
        nota="teste",
        purchased_at=date.today(),
    )
    msg = IncomingMessage(platform="discord", user_id=user_id, text=f"apagar CC{tx_id}")

    response = route(classify(msg.text), msg)

    assert f"Compra no crédito CC{tx_id} apagada" in response
    bill, _items = get_open_bill_summary(user_id, card_id, as_of=date.today())
    assert float(bill["total"]) == 0.0


def test_route_apagar_compra_com_codigo_simples_remove_da_fatura(user_id):
    card_id = create_card(user_id=user_id, name="Nubank", closing_day=1, due_day=8)
    tx_id, _due, _bill_id = add_credit_purchase(
        user_id=user_id,
        card_id=card_id,
        valor=80.0,
        categoria="outros",
        nota="teste",
        purchased_at=date.today(),
    )
    msg = IncomingMessage(platform="discord", user_id=user_id, text=f"apagar CC{tx_id}")

    response = route(classify(msg.text), msg)

    assert f"Compra no crédito CC{tx_id} apagada" in response
    bill, _items = get_open_bill_summary(user_id, card_id, as_of=date.today())
    assert float(bill["total"]) == 0.0


def test_route_compra_acima_do_limite_e_bloqueada(user_id):
    card_id = create_card(user_id=user_id, name="teste", closing_day=15, due_day=22)
    from db import set_card_limit

    set_card_limit(user_id, card_id, 100.0)
    set_default_card(user_id, card_id)
    msg = IncomingMessage(platform="discord", user_id=user_id, text="gastei 150 no cartao teste")

    response = route(classify(msg.text), msg)

    assert "Compra não registrada" in response
    assert "Limite total" in response
    bill, _items = get_open_bill_summary(user_id, card_id, as_of=date.today())
    assert float(bill["total"]) == 0.0


def test_route_cartoes_lista_pelo_fluxo_central(user_id):
    card_id = create_card(user_id=user_id, name="Nubank", closing_day=1, due_day=8)
    from db import set_card_limit

    set_card_limit(user_id, card_id, 1000.0)
    msg = IncomingMessage(platform="discord", user_id=user_id, text="Cartões")
    result = classify("Cartões")

    response = route(result, msg)

    assert "Seus cartões cadastrados" in response
    assert "Nubank" in response
    assert "Fechamento" in response
    assert "Vencimento" in response
    assert "Limite" in response


def test_classify_frase_natural_de_cartoes():
    result = classify("quais cartoes tenho registrado?")

    assert result.intent == "credit.handle"


def test_classify_vocabulario_natural_de_faturas():
    result = classify("me mostra minhas faturas")

    assert result.intent == "credit.handle"


def test_classify_vocabulario_cartao_principal():
    result = classify("qual meu cartao principal?")

    assert result.intent == "credit.handle"


def test_route_frase_natural_lista_cartoes(user_id):
    create_card(user_id=user_id, name="Nubank", closing_day=1, due_day=8)
    msg = IncomingMessage(platform="discord", user_id=user_id, text="quais sao meus cartoes?")

    response = route(classify(msg.text), msg)

    assert "Seus cartões" in response
    assert "Nubank" in response


def test_route_pergunta_cartao_principal(user_id):
    card_id = create_card(user_id=user_id, name="Nubank", closing_day=1, due_day=8)
    from db import set_default_card

    set_default_card(user_id, card_id)
    msg = IncomingMessage(platform="discord", user_id=user_id, text="qual meu cartao principal?")

    response = route(classify(msg.text), msg)

    assert "cartão principal" in response.lower()
    assert "Nubank" in response


def test_route_pergunta_fatura_do_cartao(user_id):
    card_id = create_card(user_id=user_id, name="Nubank", closing_day=1, due_day=8)
    from db import set_default_card

    set_default_card(user_id, card_id)
    add_credit_purchase(
        user_id=user_id,
        card_id=card_id,
        valor=100.0,
        categoria="outros",
        nota="teste",
        purchased_at=date(2026, 4, 5),
    )
    msg = IncomingMessage(platform="discord", user_id=user_id, text="quanto tenho na fatura do nubank?")

    response = route(classify(msg.text), msg)

    assert "Fatura atual" in response
    assert "Nubank" in response


def test_route_pergunta_fatura_deste_cartao_usa_principal(user_id):
    card_id = create_card(user_id=user_id, name="Nubank", closing_day=1, due_day=8)
    from db import set_default_card

    set_default_card(user_id, card_id)
    add_credit_purchase(
        user_id=user_id,
        card_id=card_id,
        valor=80.0,
        categoria="outros",
        nota="teste",
        purchased_at=date(2026, 4, 5),
    )
    msg = IncomingMessage(platform="discord", user_id=user_id, text="quanto tenho na fatura deste cartao?")

    response = route(classify(msg.text), msg)

    assert "Fatura atual" in response
    assert "Nubank" in response


def test_route_trocar_cartao_principal_abre_fluxo(user_id):
    create_card(user_id=user_id, name="Nubank", closing_day=1, due_day=8)
    create_card(user_id=user_id, name="Visa", closing_day=5, due_day=10)
    msg = IncomingMessage(platform="discord", user_id=user_id, text="quero mudar meu cartao principal")

    response = route(classify(msg.text), msg)

    assert "Qual cartão você quer definir como principal" in response


def test_route_pergunta_quando_vence_cartao_existente(user_id):
    create_card(user_id=user_id, name="Nubank", closing_day=1, due_day=8)
    msg = IncomingMessage(platform="discord", user_id=user_id, text="meu nubank vence quando?")

    response = route(classify(msg.text), msg)

    assert "vence no dia" in response
    assert "8" in response


def test_route_pergunta_quando_vence_cartao_inexistente_oferece_cadastro(user_id):
    create_card(user_id=user_id, name="Nubank", closing_day=1, due_day=8)
    msg = IncomingMessage(platform="discord", user_id=user_id, text="meu visa vence quando?")

    response = route(classify(msg.text), msg)

    assert "Não encontrei um cartão chamado" in response
    assert "criar cartao visa" in response


def test_route_qual_cartao_fecha_dia_30(user_id):
    create_card(user_id=user_id, name="Mastercard", closing_day=30, due_day=31)
    msg = IncomingMessage(platform="discord", user_id=user_id, text="qual cartao fecha dia 30?")

    response = route(classify(msg.text), msg)

    assert "fecham dia 30" in response or "fecha dia 30" in response
    assert "Mastercard" in response


def test_contextual_help_para_cartao_quando_nao_entende(user_id):
    msg = IncomingMessage(platform="discord", user_id=user_id, text="cartao banana extraterrestre")
    result = classify(msg.text)

    response = route(result, msg)

    assert "Não entendi exatamente" in response
    assert "Posso te ajudar com cartões" in response
    assert "criar cartao Nubank" in response
    assert "fatura Nubank" in response


def test_contextual_help_para_caixinha_quando_nao_entende(user_id):
    msg = IncomingMessage(platform="discord", user_id=user_id, text="caixinha banana cosmica")

    response = route(classify(msg.text), msg)

    assert "Não entendi exatamente" in response
    assert "caixinhas" in response.lower() or "caixinha" in response.lower()
    assert "criar caixinha viagem" in response


def test_contextual_help_para_caixinha_com_erro_de_digitacao(user_id):
    msg = IncomingMessage(platform="discord", user_id=user_id, text="caxinha banana cosmica")

    response = route(classify(msg.text), msg)

    assert "Não entendi exatamente" in response
    assert "caixinha" in response.lower()
    assert "criar caixinha viagem" in response


def test_contextual_help_para_investimento_quando_nao_entende(user_id):
    msg = IncomingMessage(platform="discord", user_id=user_id, text="investimento maluco intergalactico")

    response = route(classify(msg.text), msg)

    assert "Não entendi exatamente" in response
    assert "investimento" in response.lower()
    assert "criar investimento CDB 110% CDI" in response


def test_contextual_help_para_lancamentos_quando_nao_entende(user_id):
    msg = IncomingMessage(platform="discord", user_id=user_id, text="gastos banana quanticos")

    response = route(classify(msg.text), msg)

    assert "Não entendi exatamente" in response
    assert "lançamentos" in response.lower() or "saldo" in response.lower()
    assert "gastei 50 mercado" in response


def test_gastos_com_texto_solto_nao_lista_lancamentos(user_id):
    add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    add_launch_and_update_balance(user_id, "despesa", 50, "mercado", "gastei 50 mercado")
    msg = IncomingMessage(platform="discord", user_id=user_id, text="gastos bana quanticos")

    response = route(classify(msg.text), msg)

    assert "Últimos" not in response
    assert "Não entendi exatamente" in response


def test_contextual_help_para_categorias_quando_nao_entende(user_id):
    msg = IncomingMessage(platform="discord", user_id=user_id, text="categoria marciana aleatoria")

    response = route(classify(msg.text), msg)

    assert "Não entendi exatamente" in response
    assert "categor" in response.lower()
    assert "criar categoria mercado" in response


def test_contextual_help_para_dashboard_quando_nao_entende(user_id):
    msg = IncomingMessage(platform="discord", user_id=user_id, text="dashboard estranho demais")

    response = route(classify(msg.text), msg)

    assert "Não entendi exatamente" in response
    assert "dashboard" in response.lower()
    assert "abrir o dashboard" in response.lower() or "`dashboard`" in response


def test_contextual_help_generico_quando_nao_entende_nada(user_id):
    msg = IncomingMessage(platform="discord", user_id=user_id, text="banana radioativo do espaco")

    response = route(classify(msg.text), msg)

    assert "Não entendi exatamente o que você quer fazer" in response
    assert "`ajuda`" in response


def test_route_pergunta_como_registrar_compra_no_credito(user_id):
    msg = IncomingMessage(
        platform="discord",
        user_id=user_id,
        text="como faço para registrar compras no cartao de credito",
    )

    response = route(classify(msg.text), msg)

    assert "Para registrar uma compra no crédito" in response
    assert "credito 150 mercado" in response
    assert "apagar CC17" in response


def test_classify_sinonimos_de_cadastro_de_cartao():
    for texto in ("cadastrar cartao", "registrar cartao", "adicionar cartao", "quero cadastrar um cartao"):
        result = classify(texto)
        assert result.intent == "credit.handle", f"Esperado credit.handle para '{texto}', obteve {result.intent}"


def test_route_cadastrar_cartao_abre_fluxo_guiado(user_id):
    msg = IncomingMessage(platform="discord", user_id=user_id, text="cadastrar cartao")

    response = route(classify(msg.text), msg)
    pending = get_pending_action(user_id)

    assert "Qual cartão deseja registrar" in response
    assert pending["action_type"] == "credit_card_setup"
    assert pending["payload"]["step"] == "name"


def test_route_registrar_cartao_nubank_pergunta_fechamento(user_id):
    msg = IncomingMessage(platform="discord", user_id=user_id, text="registrar cartao nubank")

    response = route(classify(msg.text), msg)
    pending = get_pending_action(user_id)

    assert "Quando fecha a fatura" in response
    assert pending["payload"]["card_name"] == "nubank"
    assert pending["payload"]["step"] == "closing_day"


def test_route_registrar_cartao_com_dados_completos(user_id):
    msg = IncomingMessage(
        platform="discord",
        user_id=user_id,
        text="registrar cartao Nubank fecha 1 vence 8",
    )

    response = route(classify(msg.text), msg)

    assert "registrado com sucesso" in response


def test_route_pergunta_como_criar_caixinha(user_id):
    msg = IncomingMessage(
        platform="discord",
        user_id=user_id,
        text="como faço para criar uma caixinha",
    )

    response = route(classify(msg.text), msg)

    assert "Para criar uma caixinha" in response
    assert "criar caixinha viagem" in response


def test_route_pergunta_como_importar_ofx(user_id):
    msg = IncomingMessage(
        platform="discord",
        user_id=user_id,
        text="como faço para importar um extrato ofx",
    )

    response = route(classify(msg.text), msg)

    assert "Para importar um OFX" in response
    assert "importar ofx" in response


def test_route_pergunta_como_fazer_um_lancamento(user_id):
    msg = IncomingMessage(
        platform="discord",
        user_id=user_id,
        text="como faço para fazer um lançamento",
    )

    response = route(classify(msg.text), msg)

    assert "Para fazer um lançamento" in response
    assert "gastei 50 mercado" in response


def test_route_pergunta_como_faco_um_lancamento(user_id):
    msg = IncomingMessage(
        platform="discord",
        user_id=user_id,
        text="como faco um lancamento?",
    )

    response = route(classify(msg.text), msg)

    assert "Para fazer um lançamento" in response
    assert "listar lançamentos" in response


def test_route_pergunta_como_apagar_compra_no_credito(user_id):
    msg = IncomingMessage(
        platform="discord",
        user_id=user_id,
        text="como faço para apagar tal compra no crédito",
    )

    response = route(classify(msg.text), msg)

    assert "Para apagar uma compra no crédito" in response
    assert "apagar CC17" in response


def test_route_pergunta_com_apago_um_cartao(user_id):
    msg = IncomingMessage(
        platform="discord",
        user_id=user_id,
        text="com apago um cartao?",
    )

    response = route(classify(msg.text), msg)

    assert "Para apagar um cartão" in response
    assert "excluir cartao Nubank" in response


def test_route_pergunta_como_apagar_uma_parcela(user_id):
    msg = IncomingMessage(
        platform="discord",
        user_id=user_id,
        text="como faço para apagar uma parcela",
    )

    response = route(classify(msg.text), msg)

    assert "Para apagar um parcelamento" in response
    assert "apagar PCAB12CD34" in response
    assert "parcelamentos" in response


def test_route_excluir_cartao_abre_confirmacao(user_id):
    create_card(user_id=user_id, name="Nubank", closing_day=1, due_day=8)
    msg = IncomingMessage(
        platform="discord",
        user_id=user_id,
        text="excluir cartao Nubank",
    )

    response = route(classify(msg.text), msg)
    pending = get_pending_action(user_id)

    assert "Tem certeza que deseja excluir o cartão" in response
    assert pending["action_type"] == "credit_delete_card"


def test_route_criar_cartao_pelo_fluxo_central(user_id):
    msg = IncomingMessage(
        platform="discord",
        user_id=user_id,
        text="Criar cartão Nubank fecha 1 vence 8",
    )
    result = classify(msg.text)

    response = route(result, msg)

    assert "registrado com sucesso" in response


def test_route_criar_cartao_abre_fluxo_guiado(user_id):
    msg = IncomingMessage(platform="discord", user_id=user_id, text="criar cartao")

    response = route(classify(msg.text), msg)
    pending = get_pending_action(user_id)

    assert "Qual cartão deseja registrar" in response
    assert pending["action_type"] == "credit_card_setup"
    assert pending["payload"]["step"] == "name"


def test_route_criar_cartao_nubank_pergunta_fechamento(user_id):
    msg = IncomingMessage(platform="discord", user_id=user_id, text="criar cartao nubank")

    response = route(classify(msg.text), msg)
    pending = get_pending_action(user_id)

    assert "Quando fecha a fatura" in response
    assert pending["payload"]["card_name"] == "nubank"
    assert pending["payload"]["step"] == "closing_day"


def test_fluxo_completo_primeiro_cartao_define_principal_e_lembrete(user_id):
    msg = IncomingMessage(platform="discord", user_id=user_id, text="criar cartao nubank")
    route(classify(msg.text), msg)

    response = route(classify("dia 1"), IncomingMessage(platform="discord", user_id=user_id, text="dia 1"))
    assert "Quando vence a fatura" in response

    response = route(classify("dia 8"), IncomingMessage(platform="discord", user_id=user_id, text="dia 8"))
    assert "registrado com sucesso" in response
    assert "já foi definido como principal" in response
    assert "Gostaria de receber notificações" in response

    response = route(classify("sim"), IncomingMessage(platform="discord", user_id=user_id, text="sim"))
    assert "Quantos dias antes" in response

    # Após informar os dias, o bot pergunta sobre limite de crédito
    response = route(classify("3"), IncomingMessage(platform="discord", user_id=user_id, text="3"))
    assert "limite de crédito" in response.lower()

    # Usuário pula o limite — bot finaliza e mostra resumo do cartão
    response = route(classify("não"), IncomingMessage(platform="discord", user_id=user_id, text="não"))
    assert "Cartão principal: Sim" in response
    assert "Lembrete: 3 dia(s) antes" in response

    cards = list_cards(user_id)
    card = cards[0] if cards else None
    assert card is not None


def test_fluxo_segundo_cartao_pergunta_se_vira_principal(user_id):
    create_card(user_id=user_id, name="Nubank", closing_day=1, due_day=8)
    msg = IncomingMessage(platform="discord", user_id=user_id, text="criar cartao Visa")
    route(classify(msg.text), msg)
    route(classify("dia 5"), IncomingMessage(platform="discord", user_id=user_id, text="dia 5"))
    response = route(classify("dia 10"), IncomingMessage(platform="discord", user_id=user_id, text="dia 10"))

    assert "Gostaria de receber notificações" in response

    # Sem notificação → bot pergunta sobre limite antes de oferecer trocar principal
    response = route(classify("nao"), IncomingMessage(platform="discord", user_id=user_id, text="nao"))
    assert "limite de crédito" in response.lower()

    # Sem limite → bot pergunta se quer trocar cartão principal
    response = route(classify("não"), IncomingMessage(platform="discord", user_id=user_id, text="não"))
    assert "Deseja tornar o **Visa** seu cartão principal" in response

    response = route(classify("sim"), IncomingMessage(platform="discord", user_id=user_id, text="sim"))
    assert "agora é o seu principal" in response


def test_classify_saudacoes_viram_greeting():
    for texto in ("oi", "ola", "bom dia", "boa tarde", "boa noite"):
        result = classify(texto)
        assert result.intent == "greeting", f"Esperado greeting para '{texto}', obteve {result.intent}"


def test_classify_variacao_saudacao_viram_greeting():
    # variações com letras repetidas devem ser capturadas pelo Tier 2
    for texto in ("oiiii", "olááá", "aloooo"):
        result = classify(texto)
        assert result.intent == "greeting", f"Esperado greeting para '{texto}', obteve {result.intent}"


def test_route_saudacao_retorna_mensagem_amigavel(user_id):
    for texto in ("oi", "bom dia", "boa noite"):
        msg = IncomingMessage(platform="discord", user_id=user_id, text=texto)
        response = route(classify(msg.text), msg)
        # Qualquer saudação deve retornar algo não-vazio e sem "Não entendi"
        assert response, f"Resposta vazia para '{texto}'"
        assert "não entendi" not in response.lower(), f"Resposta de erro para '{texto}': {response}"


def test_classify_parcelas_vai_para_credit_handle():
    for texto in ("parcelas", "ver parcelas", "listar parcelas", "meus parcelamentos"):
        result = classify(texto)
        assert result.intent == "credit.handle", f"Esperado credit.handle para '{texto}', obteve {result.intent}"


def test_route_parcelas_sem_registros_retorna_mensagem_vazia(user_id):
    msg = IncomingMessage(platform="discord", user_id=user_id, text="parcelas")
    response = route(classify(msg.text), msg)
    assert "parcelamento" in response.lower()


def test_route_apagar_grupo_parcelamento(user_id):
    card_id = create_card(user_id=user_id, name="Nubank", closing_day=1, due_day=8)
    ret = add_credit_purchase_installments(
        user_id=user_id,
        card_id=card_id,
        valor_total=300.0,
        installments=3,
        categoria="outros",
        nota="teste parcelamento",
        purchased_at=date.today(),
    )
    result_dict = ret[0] if isinstance(ret, tuple) else ret
    group_id = result_dict["group_id"]
    # Código exibido ao usuário: PC + primeiros 8 hex do UUID sem traços
    raw = str(group_id).replace("-", "").upper()
    code = f"PC{raw[:8]}"

    msg = IncomingMessage(platform="discord", user_id=user_id, text=f"apagar {code}")
    response = route(classify(msg.text), msg)

    assert "Parcelamento desfeito" in response
    assert code in response


def test_build_due_bill_reminders(user_id):
    card_id = create_card(user_id=user_id, name="Nubank", closing_day=1, due_day=8)
    from db import update_card_reminder_settings

    update_card_reminder_settings(user_id, card_id, enabled=True, days_before=3)
    add_credit_purchase(
        user_id=user_id,
        card_id=card_id,
        valor=120.0,
        categoria="outros",
        nota="teste",
        purchased_at=date(2026, 3, 30),
    )

    reminders = build_due_bill_reminders(user_id, date(2026, 4, 5))

    assert len(reminders) == 1
    assert "vence em 3 dia(s)" in reminders[0]["message"].lower()
