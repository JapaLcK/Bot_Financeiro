"""
Cobre a tool `add_credit_purchase` da IA conversacional. A tool valida args e
delega pra `core.handlers.credit.add_credit_from_entities` — mesma fonte de
verdade do bot tradicional.

Foco:
  - validação de args (valor > 0, parcelas no range)
  - cartão default vs explícito
  - à vista vs parcelado
  - consistência com o handler tradicional
"""
import db
from core.services.ai_chat.tools.cards import _add_credit_purchase_execute


def _create_card(user_id: int, name: str = "Nubank", set_default: bool = True):
    """Helper: cria um cartão pra rodar o teste."""
    card_id = db.create_card(user_id, name, closing_day=10, due_day=17)
    if set_default:
        db.set_default_card(user_id, card_id)
    return card_id


def test_execute_compra_simples_no_cartao_default(user_id):
    _create_card(user_id, "Nubank")

    msg = _add_credit_purchase_execute(user_id, {
        "valor": 44.90,
        "descricao": "Pagamento Claro",
    })

    assert "✅" in msg
    assert "Compra no Crédito Registrada" in msg
    assert "Nubank" in msg
    assert "R$ 44,90" in msg


def test_execute_compra_em_cartao_especifico(pro_user_id):
    """Pro pra liberar gate de 2 cartões."""
    _create_card(pro_user_id, "Nubank")
    db.create_card(pro_user_id, "Inter", closing_day=15, due_day=22)

    msg = _add_credit_purchase_execute(pro_user_id, {
        "valor": 100,
        "card_name": "Inter",
        "descricao": "uber",
    })

    assert "Inter" in msg
    assert "Nubank" not in msg  # não foi pro cartão padrão


def test_execute_compra_parcelada(user_id):
    _create_card(user_id, "Nubank")

    msg = _add_credit_purchase_execute(user_id, {
        "valor": 300,
        "descricao": "celular",
        "parcelas": 3,
    })

    assert "Parcelamento Registrado" in msg
    assert "3x" in msg
    assert "R$ 300,00" in msg


def test_execute_parcelas_1_eh_a_vista(user_id):
    """parcelas=1 deve cair no caminho à vista, não parcelado."""
    _create_card(user_id, "Nubank")

    msg = _add_credit_purchase_execute(user_id, {
        "valor": 50,
        "descricao": "lanche",
        "parcelas": 1,
    })

    assert "Compra no Crédito Registrada" in msg
    assert "Parcelamento" not in msg


def test_execute_valor_zero_nao_escreve(user_id):
    _create_card(user_id, "Nubank")

    msg = _add_credit_purchase_execute(user_id, {"valor": 0})

    assert "maior que zero" in msg


def test_execute_parcelas_fora_do_range(user_id):
    _create_card(user_id, "Nubank")

    msg = _add_credit_purchase_execute(user_id, {"valor": 100, "parcelas": 100})

    assert "Número de parcelas inválido" in msg


def test_execute_cartao_inexistente(user_id):
    _create_card(user_id, "Nubank")

    msg = _add_credit_purchase_execute(user_id, {
        "valor": 50,
        "card_name": "CartaoInexistente",
    })

    assert "Não achei o cartão" in msg
    assert "CartaoInexistente" in msg


def test_execute_sem_cartao_padrao(user_id):
    """Sem nenhum cartão cadastrado e sem card_name → mensagem de erro útil."""
    msg = _add_credit_purchase_execute(user_id, {"valor": 50})

    assert "cartão padrão" in msg or "Não achei o cartão" in msg


def test_execute_consistente_com_handler_tradicional(user_id):
    """Tool da IA delega pra mesma fn `add_credit_from_entities` do bot —
    resposta deve seguir mesmo formato (modulo IDs)."""
    _create_card(user_id, "Nubank")

    msg_ia = _add_credit_purchase_execute(user_id, {
        "valor": 75,
        "descricao": "mercado",
    })

    # Mesma chamada via handler direto, num user secundário
    from core.handlers.credit import add_credit_from_entities
    db.ensure_user(user_id + 1)
    try:
        _create_card(user_id + 1, "Nubank")
        msg_handler = add_credit_from_entities(
            user_id + 1,
            valor=75,
            descricao="mercado",
        )

        # Linha de "Cartão: Nubank" e "Valor: R$ 75,00" devem aparecer iguais
        for marker in ("Cartão", "Nubank", "R$ 75,00", "Compra no Crédito"):
            assert marker in msg_ia
            assert marker in msg_handler
    finally:
        # Cleanup
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("delete from credit_transactions where user_id = %s", (user_id + 1,))
                cur.execute("delete from credit_bills where user_id = %s", (user_id + 1,))
                cur.execute("delete from credit_cards where user_id = %s", (user_id + 1,))
                cur.execute("delete from accounts where user_id = %s", (user_id + 1,))
                cur.execute("delete from users where id = %s", (user_id + 1,))
            conn.commit()


def test_execute_ticker_acao_nao_vira_despesa_no_cartao(user_id):
    """Compra de ação (ticker B3) NÃO vira compra no cartão. O LLM confunde
    'ITUB4' com o cartão Itaú — o guard determinístico barra antes de lançar."""
    _create_card(user_id, "Nubank")

    msg = _add_credit_purchase_execute(user_id, {
        "valor": 77.63,
        "descricao": "ITUB4",
        "categoria": "ações",
    })

    assert "Compra no Crédito Registrada" not in msg
    assert "ITUB4" in msg
    assert "Investimentos" in msg
    # Nada foi parar na fatura.
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select count(*) as n from credit_transactions where user_id=%s", (user_id,))
            assert cur.fetchone()["n"] == 0


def test_execute_ticker_no_meio_da_descricao(user_id):
    """Detecta o ticker mesmo com quantidade junto ('10 PETR4')."""
    _create_card(user_id, "Nubank")

    msg = _add_credit_purchase_execute(user_id, {"valor": 100, "descricao": "10 PETR4"})

    assert "Compra no Crédito Registrada" not in msg
    assert "PETR4" in msg


def test_execute_descricao_comum_nao_dispara_falso_positivo(user_id):
    """Descrição comum (não-ticker) segue registrando no cartão normalmente."""
    _create_card(user_id, "Nubank")

    msg = _add_credit_purchase_execute(user_id, {"valor": 50, "descricao": "uber"})

    assert "Compra no Crédito Registrada" in msg


def test_handler_tradicional_aceita_credito_com_acento(user_id):
    """Bug: `t_low.startswith('credito')` perdia 'Crédito' porque `.lower()`
    preserva o acento. Agora aceita ambos."""
    _create_card(user_id, "Nubank")

    from core.handlers.credit import handle

    msg_sem_acento = handle(user_id, "credito 50 mercado")
    msg_com_acento = handle(user_id, "Crédito 50 mercado")

    assert msg_sem_acento is not None
    assert msg_com_acento is not None
    assert "Compra no Crédito Registrada" in msg_sem_acento
    assert "Compra no Crédito Registrada" in msg_com_acento
