from core.intent_classifier import classify, _try_alias, _normalize
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
    create_pocket,
    get_balance,
    get_memorized_category,
    get_open_bill_summary,
    get_pending_action,
    get_summary_by_period,
    list_cards,
    list_launches,
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


def test_parse_gastei_cafe_cai_em_alimentacao(user_id):
    parsed = parse_receita_despesa_natural(user_id, "gastei 10 cafe")

    assert parsed is not None
    assert parsed["tipo"] == "despesa"
    assert parsed["valor"] == 10
    assert parsed["categoria"] == "alimentação"
    assert parsed["alvo"] == "cafe"


def test_parse_valor_com_4_ou_mais_digitos_e_centavos(user_id):
    parsed_investimentos = parse_receita_despesa_natural(user_id, "gastei 9204,40 em investimentos")
    parsed_bitcoin = parse_receita_despesa_natural(user_id, "gastei 1598,97 em bitcoin")

    assert parsed_investimentos is not None
    assert parsed_investimentos["valor"] == 9204.40
    assert parsed_investimentos["categoria"] == "investimentos"
    assert parsed_investimentos["is_internal_movement"] is True

    assert parsed_bitcoin is not None
    assert parsed_bitcoin["valor"] == 1598.97
    assert parsed_bitcoin["categoria"] == "criptomoedas"
    assert parsed_bitcoin["is_internal_movement"] is True


def test_classify_apagar_id_com_hash():
    result = classify("apagar id #712")

    assert result.intent == "launches.delete"
    assert result.entities["launch_id"] == 712


def test_classify_mostra_meus_lancamentos():
    result = classify("mostra meus lancamentos")

    assert result.intent == "launches.list"


def test_resolve_delete_uses_correct_argument_order(user_id):
    add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    launch_id, _user_seq, _bal = add_launch_and_update_balance(user_id, "despesa", 200, "conta de luz", "paguei 200 conta de luz")
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

    assert "Compra no Crédito Registrada" in response
    assert "Código:" in response
    assert get_balance(user_id) == 0
    bill, _items = get_open_bill_summary(user_id, card_id, as_of=date.today())
    assert float(bill["total"]) == 150.0


def test_quick_entry_gasto_no_cartao_nao_debita_saldo(user_id):
    card_id = create_card(user_id=user_id, name="Nubank", closing_day=1, due_day=8)
    set_default_card(user_id, card_id)

    response = handle_quick_entry(user_id, "gastei 90 no cartao nubank")

    assert response is not None
    assert "Compra no Crédito Registrada" in response.text
    assert "Código:" in response.text
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


def test_free_user_segundo_cartao_recebe_mensagem_amigavel(user_id):
    """Free pode criar 1 cartão; ao tentar o 2º, bot devolve CTA pra upgrade."""
    create_card(user_id=user_id, name="Nubank", closing_day=1, due_day=8)
    # Inicia fluxo de criar cartão e avança até o ponto que chama create_card
    msg = IncomingMessage(platform="discord", user_id=user_id, text="criar cartao Visa")
    route(classify(msg.text), msg)
    route(classify("dia 5"), IncomingMessage(platform="discord", user_id=user_id, text="dia 5"))
    # Esta é a chamada que dispara create_card → PlanLimitExceeded
    response = route(classify("dia 10"), IncomingMessage(platform="discord", user_id=user_id, text="dia 10"))
    assert "PigBank+" in response
    assert "/precos" in response or "upgrade" in response.lower()


def test_free_user_segunda_caixinha_recebe_mensagem_amigavel(user_id):
    """Free pode criar 1 caixinha; ao tentar a 2ª, bot devolve CTA pra upgrade."""
    create_pocket(user_id, "viagem")
    msg = IncomingMessage(platform="discord", user_id=user_id, text="criar caixinha presente")
    response = route(classify(msg.text), msg)
    assert "PigBank+" in response
    assert "/precos" in response or "upgrade" in response.lower()


def test_route_trocar_cartao_principal_abre_fluxo(pro_user_id):
    user_id = pro_user_id
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
    assert "investimentos" in response


def test_route_saque_generico_reconhece_investimento_pelo_nome(user_id):
    from core.intent_classifier import classify
    from core.intent_router import route
    from core.types import IncomingMessage
    import db

    db.create_investment_db(user_id, "Nu Reserva Planejada", 0.14, "yearly", nota="seed")
    db.add_launch_and_update_balance(user_id, "receita", 1000, None, "seed saldo")
    db.investment_deposit_from_account(user_id, "Nu Reserva Planejada", 1000, "seed deposit")

    msg = IncomingMessage(platform="discord", user_id=user_id, text="saquei 100 de Nu Reserva Planejada")
    result = classify(msg.text)
    response = route(result, msg)

    assert "Resgate de" in response
    assert "Nu Reserva Planejada" in response


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
    assert "aprender ifood como alimentacao" in response


def test_listar_regras_usa_fluxo_de_categorias(user_id):
    msg = IncomingMessage(platform="discord", user_id=user_id, text="listar regras")

    response = route(classify(msg.text), msg)

    assert "categoria" in response.lower()


def test_aprender_como_cria_regra(user_id):
    msg = IncomingMessage(platform="discord", user_id=user_id, text="aprender ifood como alimentacao")

    response = route(classify(msg.text), msg)

    assert "aprendido" in response.lower()
    assert get_memorized_category(user_id, "pedido no ifood") == "alimentacao"


def test_regras_de_categorias_plural_lista_regras(user_id):
    msg = IncomingMessage(platform="discord", user_id=user_id, text="regras de categorias")

    response = route(classify(msg.text), msg)

    assert "categoria" in response.lower()


def test_remover_regra_remove_regra_existente(user_id):
    route(
        classify("aprender ifood como alimentacao"),
        IncomingMessage(platform="discord", user_id=user_id, text="aprender ifood como alimentacao"),
    )
    msg = IncomingMessage(platform="discord", user_id=user_id, text="remover regra ifood")

    response = route(classify(msg.text), msg)

    assert "removida" in response.lower()
    assert get_memorized_category(user_id, "pedido no ifood") is None


def test_remove_regra_sem_r_funciona(user_id):
    route(
        classify("aprender money como investimentos"),
        IncomingMessage(platform="discord", user_id=user_id, text="aprender money como investimentos"),
    )
    msg = IncomingMessage(platform="discord", user_id=user_id, text="remove regra money")

    response = route(classify(msg.text), msg)

    assert "removida" in response.lower()
    assert get_memorized_category(user_id, "money invest") is None


def test_remover_regra_por_nome_da_categoria_remove_regras_associadas(user_id):
    route(
        classify("aprender bitcoin como criptomoedas"),
        IncomingMessage(platform="discord", user_id=user_id, text="aprender bitcoin como criptomoedas"),
    )
    msg = IncomingMessage(platform="discord", user_id=user_id, text="remover regra criptomoedas")

    response = route(classify(msg.text), msg)

    assert "removida" in response.lower() or "removidas" in response.lower()
    assert get_memorized_category(user_id, "comprei bitcoin") is None


def test_regra_aprendida_de_investimentos_vira_movimentacao_interna(user_id):
    route(
        classify("aprender bitcoin como criptomoedas"),
        IncomingMessage(platform="discord", user_id=user_id, text="aprender bitcoin como criptomoedas"),
    )

    response = route(
        classify("gastei 1598,97 em bitcoin"),
        IncomingMessage(platform="discord", user_id=user_id, text="gastei 1598,97 em bitcoin"),
    )

    summary = get_summary_by_period(user_id, date.today().replace(day=1), date.today())

    assert "registrada" in response.lower()
    assert "criptomoedas" in response.lower()
    assert summary["despesa"] == 0.0


def test_lancamento_manual_ensina_categoria_automaticamente(user_id):
    msg = IncomingMessage(platform="discord", user_id=user_id, text="gastei 35 na farmacia sao jose")

    response = route(classify(msg.text), msg)

    assert "registrada" in response.lower()
    assert get_memorized_category(user_id, "compra farmacia sao jose") == "saude"


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


def test_fluxo_segundo_cartao_pergunta_se_vira_principal(pro_user_id):
    user_id = pro_user_id
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


# ───────────────────────────────────────────────────────────────────────────
# Valor primeiro sem palavra-chave → despesa por padrão.
# "77,90 mercado" = gastei 77,90 no mercado, sem precisar do verbo.
# Receita SEMPRE exige "recebi"/"receita"/"ganhei".
# ───────────────────────────────────────────────────────────────────────────


def test_parse_valor_primeiro_sem_palavra_chave_vira_despesa(user_id):
    parsed = parse_receita_despesa_natural(user_id, "77,90 mercado")

    assert parsed is not None
    assert parsed["tipo"] == "despesa"
    assert parsed["valor"] == 77.90
    assert parsed["alvo"] == "mercado"


def test_parse_valor_primeiro_inteiro(user_id):
    parsed = parse_receita_despesa_natural(user_id, "50 uber")

    assert parsed is not None
    assert parsed["tipo"] == "despesa"
    assert parsed["valor"] == 50
    assert parsed["alvo"] == "uber"


def test_parse_valor_primeiro_com_rs_limpa_o_alvo(user_id):
    """O prefixo 'R$' não deve vazar pro alvo."""
    parsed = parse_receita_despesa_natural(user_id, "R$ 1.234,56 aluguel")

    assert parsed is not None
    assert parsed["tipo"] == "despesa"
    assert parsed["valor"] == 1234.56
    assert parsed["alvo"] == "aluguel"


def test_parse_receita_sem_palavra_chave_continua_despesa(user_id):
    """Sem 'recebi'/'receita'/'ganhei', valor-primeiro é GASTO mesmo que a
    descrição soe como entrada ('salario'). Receita exige marcação explícita."""
    sem_kw = parse_receita_despesa_natural(user_id, "1000 salario")
    com_kw = parse_receita_despesa_natural(user_id, "recebi 1000 salario")

    assert sem_kw is not None and sem_kw["tipo"] == "despesa"
    assert com_kw is not None and com_kw["tipo"] == "receita"


def test_parse_descricao_primeiro_nao_vira_despesa(user_id):
    """Só valor-PRIMEIRO dispara o default. Descrição-primeiro ('mercado 50')
    fica pro fallback de IA decidir — o parser determinístico não chuta."""
    assert parse_receita_despesa_natural(user_id, "mercado 50") is None


def test_parse_sem_valor_retorna_none(user_id):
    assert parse_receita_despesa_natural(user_id, "mercado") is None


def test_classify_valor_primeiro_vai_para_launches_add():
    assert classify("77,90 mercado").intent == "launches.add"
    assert classify("50 uber").intent == "launches.add"
    assert classify("30 farmacia").intent == "launches.add"


def test_classify_numero_solto_nao_vira_launch():
    """Número solto ('50', resposta a uma pergunta) NÃO pode virar lançamento —
    o pattern exige descrição após o valor. Testa o alias direto pra não cair
    no tier 3 (IA)."""
    assert _try_alias(_normalize("50"), "50") is None


def test_classify_descricao_primeiro_nao_captura_no_aliase():
    """'mercado 50' não bate no alias determinístico (valor-primeiro só)."""
    assert _try_alias(_normalize("mercado 50"), "mercado 50") is None


def test_classify_valor_primeiro_com_cartao_vai_para_credito():
    """Keyword de domínio vence o default: '50 no cartao nubank' é crédito,
    não despesa de conta corrente."""
    assert classify("50 no cartao nubank").intent == "credit.handle"


def test_route_valor_primeiro_debita_saldo(user_id):
    """Ponta-a-ponta: '77,90 mercado' registra DESPESA e debita o saldo."""
    msg = IncomingMessage(platform="discord", user_id=user_id, text="77,90 mercado")

    response = route(classify(msg.text), msg)

    assert "despesa registrada" in response.lower()  # não receita
    assert round(float(get_balance(user_id)), 2) == -77.90


def test_route_receita_exige_palavra_chave(user_id):
    """'recebi 300 salario' credita (+300); depois '50 mercado' debita (-50).
    Confirma que receita só acontece com a palavra-chave."""
    msg_in = IncomingMessage(platform="discord", user_id=user_id, text="recebi 300 salario")
    route(classify(msg_in.text), msg_in)
    assert round(float(get_balance(user_id)), 2) == 300.00

    msg_out = IncomingMessage(platform="discord", user_id=user_id, text="50 mercado")
    route(classify(msg_out.text), msg_out)
    assert round(float(get_balance(user_id)), 2) == 250.00


def test_route_clarification_valor_completa_lancamento(user_id):
    """Bot perguntou o valor; a resposta '150' completa o lançamento mantendo
    descrição e data do texto original ('lavagem carro 02/06')."""
    set_pending_action(
        user_id,
        "clarification",
        {
            "intent": "launches.add",
            "entities": {"tipo": "despesa", "alvo": "lavagem carro", "categoria": "transporte"},
            "question": "Qual foi o valor da lavagem do carro?",
            "orig_text": "lavagem carro 02/06",
        },
    )
    msg = IncomingMessage(platform="discord", user_id=user_id, text="150")
    response = route(classify(msg.text), msg)

    assert "despesa registrada" in response.lower()
    assert round(float(get_balance(user_id)), 2) == -150.00
    assert get_pending_action(user_id) is None  # clarification consumida
    # data preservada (02/06) — normaliza pro fuso do app, já que o driver
    # devolve o timestamptz no fuso da sessão (varia por máquina/CI).
    from zoneinfo import ZoneInfo
    sp = list_launches(user_id, limit=1)[0]["criado_em"].astimezone(ZoneInfo("America/Sao_Paulo"))
    assert sp.month == 6 and sp.day == 2


def test_route_clarification_receita_preserva_tipo(user_id):
    """Clarification de receita preserva o tipo: '5000' credita (+5000)."""
    set_pending_action(
        user_id,
        "clarification",
        {
            "intent": "launches.add",
            "entities": {"tipo": "receita", "alvo": "salario", "categoria": "rendimentos"},
            "question": "Qual foi o valor recebido?",
            "orig_text": "recebi salario",
        },
    )
    msg = IncomingMessage(platform="discord", user_id=user_id, text="5000")
    response = route(classify(msg.text), msg)

    assert "receita registrada" in response.lower()
    assert round(float(get_balance(user_id)), 2) == 5000.00


def test_route_clarification_descricao_completa_lancamento(user_id):
    """Bot já tinha o valor e perguntou a descrição ('Em que você gastou?');
    a resposta 'mercado' completa o lançamento sem re-perguntar."""
    set_pending_action(
        user_id,
        "clarification",
        {
            "intent": "launches.add",
            "entities": {"tipo": "despesa", "valor": 50},
            "question": "Em que você gastou R$ 50?",
            "orig_text": "gastei 50",
        },
    )
    msg = IncomingMessage(platform="discord", user_id=user_id, text="mercado")
    response = route(classify(msg.text), msg)

    assert "despesa registrada" in response.lower()
    assert round(float(get_balance(user_id)), 2) == -50.00
    assert get_pending_action(user_id) is None


def test_route_clarification_sem_valor_refaz_pergunta(user_id):
    """Resposta sem valor reconhecível refaz a pergunta e mantém o pending."""
    payload = {
        "intent": "launches.add",
        "entities": {"tipo": "despesa", "alvo": "uber", "categoria": "transporte"},
        "question": "Qual foi o valor do uber?",
        "orig_text": "uber",
    }
    set_pending_action(user_id, "clarification", payload)
    msg = IncomingMessage(platform="discord", user_id=user_id, text="sei la")
    response = route(classify(msg.text), msg)

    assert response == "Qual foi o valor do uber?"
    pend = get_pending_action(user_id)
    assert pend is not None and pend["payload"]["intent"] == "launches.add"
    assert round(float(get_balance(user_id)), 2) == 0.00


def test_handle_incoming_clarification_tem_precedencia_sobre_fallback_ia():
    """Regressão (bug do screenshot 77,90 → cinema → 'valor precisa ser maior
    que zero').

    O bot perguntou 'Em que você gastou R$ 77,90?' e guardou um pending de
    clarification determinístico. A resposta 'cinema' classifica como baixa
    confiança (out_of_scope, pois a OpenAI está bloqueada nos testes) e, SEM o
    guard em handle_incoming, seria sequestrada pelo fallback de IA — que não
    conhece o valor 77,90 e falha. O guard garante que o route() determinístico
    resolva a clarification primeiro, completando o lançamento.

    Usa uid < 2 bi pra não sofrer remap em _normalize_user_id. Promove a Pro
    porque é justamente quando o fallback de IA *seria* acionado — provando a
    precedência do caminho determinístico.
    """
    import uuid as _uuid
    import db
    from db.connection import get_conn
    from core.handle_incoming import handle_incoming

    uid = int(_uuid.uuid4().int % 1_000_000_000) + 1  # < 2 bilhões
    db.ensure_user(uid)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select id from auth_accounts where user_id = %s limit 1", (uid,))
            if cur.fetchone():
                cur.execute(
                    "update auth_accounts set plan='pro', plan_expires_at=null where user_id = %s",
                    (uid,),
                )
            else:
                cur.execute(
                    "insert into auth_accounts(user_id, email, password_hash, plan) "
                    "values (%s, %s, 'x', 'pro')",
                    (uid, f"pro-clarif-{uid}@test.local"),
                )
        conn.commit()

    set_pending_action(
        uid,
        "clarification",
        {
            "intent": "launches.add",
            "entities": {"tipo": "despesa", "valor": 77.90},
            "question": "Em que você gastou R$ 77,90?",
            "orig_text": "77,90",
        },
    )

    msg = IncomingMessage(platform="discord", user_id=uid, text="cinema")
    out = handle_incoming(msg)

    assert out, "handle_incoming não retornou resposta"
    response = out[0].text
    assert "despesa registrada" in response.lower(), f"esperava lançamento, veio: {response!r}"
    assert round(float(get_balance(uid)), 2) == -77.90
    assert get_pending_action(uid) is None  # clarification consumida


# ───────────────────────────────────────────────────────────────────────────
# Guard anti-órfão da confirmação destrutiva determinística.
#
# Footgun da screenshot: "apagar #N" arma uma confirmação no pending_actions
# (TTL 10 min). Se o user manda OUTRO comando claro ("saldo") em vez de
# "sim/não", o comando roda mas NÃO limpava o pending — então um "sim" depois
# disparava a exclusão antiga sem querer. O guard limpa o pending órfão quando
# chega um comando claro (alta confiança, não-confirmação, não-out_of_scope).
# ───────────────────────────────────────────────────────────────────────────


def test_route_comando_claro_cancela_delete_pendente_orfao(user_id):
    """Confirmação de exclusão armada + 'saldo' no meio → pending é abandonado.
    Um 'sim' depois NÃO apaga o lançamento (continua vivo, saldo intacto)."""
    add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    launch_id, user_seq, _bal = add_launch_and_update_balance(
        user_id, "despesa", 200, "conta de luz", "paguei 200 conta de luz"
    )
    set_pending_action(
        user_id, "delete_launch",
        {"launch_id": int(launch_id), "display_id": int(user_seq)},
    )

    # Comando claro no meio (balance.check, confiança alta) → abandona o pending.
    msg_saldo = IncomingMessage(platform="discord", user_id=user_id, text="saldo")
    route(classify(msg_saldo.text), msg_saldo)
    assert get_pending_action(user_id) is None, "comando claro deve limpar o pending órfão"

    # 'sim' depois não acha nada pra confirmar — o lançamento sobrevive.
    msg_sim = IncomingMessage(platform="discord", user_id=user_id, text="sim")
    route(classify(msg_sim.text), msg_sim)

    assert get_pending_action(user_id) is None
    # saldo: 1000 - 200 = 800 (a despesa NÃO foi revertida)
    assert round(float(get_balance(user_id)), 2) == 800.00
    assert launch_id in [r["id"] for r in list_launches(user_id, limit=10)]


def test_route_sim_imediato_apaga_normalmente(user_id):
    """Sem comando no meio, o fluxo normal segue intacto: 'sim' logo após a
    confirmação apaga o lançamento (o guard não dispara em confirm.yes)."""
    add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    launch_id, user_seq, _bal = add_launch_and_update_balance(
        user_id, "despesa", 200, "conta de luz", "paguei 200 conta de luz"
    )
    set_pending_action(
        user_id, "delete_launch",
        {"launch_id": int(launch_id), "display_id": int(user_seq)},
    )

    msg_sim = IncomingMessage(platform="discord", user_id=user_id, text="sim")
    response = route(classify(msg_sim.text), msg_sim)

    assert "apagado" in response.lower()
    assert get_pending_action(user_id) is None
    # despesa revertida → volta pra 1000
    assert round(float(get_balance(user_id)), 2) == 1000.00


def test_route_frase_ambigua_nao_cancela_delete_pendente(user_id):
    """Frase de baixa confiança/out_of_scope NÃO mata a confirmação — pode ser
    continuação da conversa; o pending sobrevive pra um 'sim/não' seguinte."""
    add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    launch_id, user_seq, _bal = add_launch_and_update_balance(
        user_id, "despesa", 200, "conta de luz", "paguei 200 conta de luz"
    )
    set_pending_action(
        user_id, "delete_launch",
        {"launch_id": int(launch_id), "display_id": int(user_seq)},
    )

    msg = IncomingMessage(platform="discord", user_id=user_id, text="na verdade deixa quieto")
    route(classify(msg.text), msg)

    pend = get_pending_action(user_id)
    assert pend is not None and pend["action_type"] == "delete_launch"
