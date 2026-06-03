"""
Regressão pra tool `delete_all_launches` da IA conversacional.

Contexto do bug (screenshot WhatsApp): user mandou "Apague todos os lançamentos";
a IA NÃO tinha tool pra isso e improvisou um "Confirma com sim ou não?" como
texto livre — sem registrar pending. Aí o "Sim" caía no determinístico e morria
em "Não entendi". Conserto: tool real `delete_all_launches` que pede confirmação
de verdade (seta ai_pending_action), e o "sim" seguinte executa.

Os testes do fluxo de confirmação NÃO tocam OpenAI: o caminho pending+"sim" em
`_chat_inner` executa a tool e retorna ANTES de chamar o LLM, então roda mesmo
com o kill switch de rede do conftest ativo.
"""

import db
from db import add_launch_and_update_balance, get_balance
from core.services.ai_chat import chat
from core.services.ai_chat.tools import get_tool


def _bal(uid: int) -> float:
    return round(float(get_balance(uid)), 2)


def test_delete_all_launches_tool_registrada():
    tool = get_tool("delete_all_launches")
    assert tool is not None, "tool delete_all_launches não registrada"
    assert tool.is_write is True
    assert tool.requires_confirmation is True, "precisa pedir confirmação (destrutivo em massa)"
    assert tool.summary is not None and tool.validate is not None


def test_delete_all_launches_db_reverte_saldo(user_id: int):
    add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    add_launch_and_update_balance(user_id, "despesa", 200, "luz", "paguei 200 luz")
    add_launch_and_update_balance(user_id, "despesa", 50, "mercado", "gastei 50 mercado")
    assert _bal(user_id) == 750.0
    assert db.count_launches(user_id) == 3

    result = db.delete_all_launches_and_rollback(user_id)
    assert result == {"deleted": 3, "failed": 0}
    assert db.count_launches(user_id) == 0
    assert _bal(user_id) == 0.0, "saldo deve voltar ao estado pré-lançamentos"


def test_delete_all_launches_validate_bloqueia_quando_vazio(user_id: int):
    tool = get_tool("delete_all_launches")
    # Sem lançamentos → valida com mensagem amigável (evita 'confirma apagar tudo?' inútil)
    err = tool.validate(user_id, {})
    assert err is not None and "nenhum lançamento" in err.lower()

    # Com lançamento → valida None (segue pro fluxo de confirmação)
    add_launch_and_update_balance(user_id, "despesa", 10, "cafe", "gastei 10 cafe")
    assert tool.validate(user_id, {}) is None


def test_delete_all_launches_confirmacao_sim_executa(user_id: int):
    """End-to-end do caminho de confirmação, SEM OpenAI: seta o pending (como
    a tool faria) e manda 'sim' — o chat() executa a tool e limpa o pending."""
    add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    add_launch_and_update_balance(user_id, "despesa", 300, "aluguel", "paguei 300 aluguel")
    assert _bal(user_id) == 700.0

    # Simula o que `_dispatch_tool` faz quando o LLM chama a tool de write.
    db.ai_set_pending_action(
        user_id,
        "delete_all_launches",
        {},
        "apagar TODOS os seus lançamentos e reverter o saldo",
    )
    assert db.ai_get_pending_action(user_id) is not None

    resp = chat(user_id, "sim", monthly_limit=1000, platform="whatsapp")

    assert "apaguei" in resp.lower(), f"esperava confirmação de exclusão, veio: {resp!r}"
    assert db.count_launches(user_id) == 0
    assert _bal(user_id) == 0.0
    assert db.ai_get_pending_action(user_id) is None, "pending deve ser limpo após executar"


def test_delete_all_launches_confirmacao_nao_cancela(user_id: int):
    """'não' após o pending NÃO apaga nada e limpa o pending."""
    add_launch_and_update_balance(user_id, "despesa", 80, "uber", "gastei 80 uber")
    assert db.count_launches(user_id) == 1

    db.ai_set_pending_action(
        user_id, "delete_all_launches", {}, "apagar TODOS os seus lançamentos"
    )
    resp = chat(user_id, "não", monthly_limit=1000, platform="whatsapp")

    assert db.count_launches(user_id) == 1, "cancelar não pode apagar nada"
    assert db.ai_get_pending_action(user_id) is None
    assert resp  # alguma mensagem de "não fiz nada"


def _pocket_balance(user_id: int, name: str):
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select balance from pockets where user_id=%s and lower(name)=lower(%s)",
                (user_id, name),
            )
            row = cur.fetchone()
            return float(row["balance"]) if row else None


def _investment_balance(user_id: int, name: str):
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select balance from investments where user_id=%s and lower(name)=lower(%s)",
                (user_id, name),
            )
            row = cur.fetchone()
            return float(row["balance"]) if row else None


def test_delete_all_launches_preserva_caixinha_e_investimento(user_id: int):
    """Regressão do bug 'apaga tudo zerava o usuário do começo': caixinhas e
    investimentos NÃO podem ser tocados — só despesas/receitas (e pagamento de
    fatura) somem.

    Armadilha que isto trava: criar caixinha/investimento gera um launch com
    `is_internal_movement=false`; se o filtro fosse por essa flag (em vez de
    `tipo in ('despesa','receita')`), apagá-lo deletaria a caixinha/o
    investimento junto (efeitos.create_pocket → delete from pockets)."""
    from db.pockets import create_pocket, pocket_deposit_from_account
    from db.investments import create_investment_db, investment_deposit_from_account

    add_launch_and_update_balance(user_id, "receita", 1000, None, "salario")
    add_launch_and_update_balance(user_id, "despesa", 200, "luz", "paguei 200 luz")
    create_pocket(user_id, "viagem")
    pocket_deposit_from_account(user_id, "viagem", 300, "guardando")
    create_investment_db(user_id, "CDB Teste", 1.0, "monthly")
    investment_deposit_from_account(user_id, "CDB Teste", 250)

    # só a despesa e a receita entram no conjunto "apagável"; a criação e os
    # aportes de caixinha/investimento ficam de fora.
    assert db.count_launches(user_id) == 2

    pocket_before = _pocket_balance(user_id, "viagem")
    inv_before = _investment_balance(user_id, "CDB Teste")
    assert pocket_before == 300.0
    assert inv_before is not None and inv_before >= 250.0

    result = db.delete_all_launches_and_rollback(user_id)
    assert result == {"deleted": 2, "failed": 0}

    # o ponto do teste: caixinha e investimento INTACTOS (registro + saldo).
    assert _pocket_balance(user_id, "viagem") == pocket_before, "caixinha não pode ser tocada"
    assert _investment_balance(user_id, "CDB Teste") == inv_before, "investimento não pode ser tocado"
    # as despesas/receitas sumiram.
    assert db.count_launches(user_id) == 0
