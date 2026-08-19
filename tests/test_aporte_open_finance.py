"""Aporte e caixinha quando o dinheiro está num banco conectado (Open Finance).

O bug relatado: o app mostrava R$ 1.387,76 de saldo e o bot respondia "Saldo
insuficiente na conta" a um aporte de R$ 800. Os dois estavam certos e falando de
coisas diferentes — o saldo da tela é Carteira + bancos conectados, e o aporte
debitava só a Carteira, que fica zerada de propósito quando há banco conectado
(senão o mesmo dinheiro conta duas vezes).

Regra adotada: com banco conectado o Pig NÃO debita a Carteira. E antes de
cadastrar um investimento novo ele avisa que o Open Finance já traz as posições do
banco — mesma doutrina que a caixinha vinda do OF já seguia (OF_POCKET_READONLY).
"""
from decimal import Decimal
from unittest.mock import patch

import pytest

import db
from core.handlers import investments as h_investments


def _connect_fake_bank(user_id: int, balance: str = "1387.76") -> int:
    connection = db.save_pluggy_open_finance_item(
        user_id,
        {"id": f"item-of-{user_id}", "connector": {"id": 612, "name": "Nubank"}, "status": "UPDATED"},
    )
    db.save_open_finance_sync(
        connection["id"],
        [{
            "provider_account_id": f"acc-of-{user_id}",
            "name": "Nubank Conta",
            "type": "BANK",
            "subtype": "CHECKING_ACCOUNT",
            "currency": "BRL",
            "balance": Decimal(balance),
            "raw": {},
            "transactions": [],
        }],
    )
    return connection["id"]


# ─── a detecção ─────────────────────────────────────────────────────────────

def test_saldos_sem_banco_conectado(user_id):
    db.add_launch_and_update_balance(user_id, "receita", 500, None, "seed")
    s = h_investments._saldos(user_id)
    assert s["tem_banco"] is False
    assert s["carteira"] == 500.0 and s["banco"] == 0.0


def test_saldos_com_banco_conectado(user_id):
    _connect_fake_bank(user_id)
    s = h_investments._saldos(user_id)
    assert s["tem_banco"] is True
    assert s["carteira"] == 0.0
    assert s["banco"] == 1387.76
    assert s["total"] == 1387.76


def test_saldos_nao_derruba_o_fluxo_se_a_consulta_falhar(user_id):
    """A detecção é acessória — se ela quebrar, o aporte não pode quebrar junto."""
    with patch("core.handlers.investments.db.get_consolidated_balance",
               side_effect=RuntimeError("boom")):
        s = h_investments._saldos(user_id)
    assert s["tem_banco"] is False   # fail-safe: volta ao comportamento antigo


# ─── o débito ───────────────────────────────────────────────────────────────

def test_aporte_com_banco_conectado_nao_debita_a_carteira(user_id):
    """O caso do print: Carteira R$ 0,00, banco com R$ 1.387,76, aporte de R$ 800."""
    _connect_fake_bank(user_id)
    db.create_investment(user_id, "Renda Fixa", 0.14, "yearly")

    msg = h_investments.deposit(
        user_id, "Investi 800 na renda fixa",
        {"investment_name": "Renda Fixa", "amount": 800},
    )

    assert "✅" in msg
    assert "insuficiente" not in msg.lower()
    invs = {i["name"]: float(i["balance"]) for i in db.list_investments(user_id)}
    assert invs["Renda Fixa"] == 800.0
    assert float(db.get_balance(user_id)) == 0.0   # a Carteira não foi tocada


def test_aporte_sem_banco_conectado_continua_debitando(user_id):
    db.create_investment(user_id, "Renda Fixa", 0.14, "yearly")
    db.add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")

    h_investments.deposit(
        user_id, "Investi 800 na renda fixa",
        {"investment_name": "Renda Fixa", "amount": 800},
    )

    assert float(db.get_balance(user_id)) == 200.0


def test_aporte_sem_debito_grava_delta_conta_zero(user_id):
    """`delta_conta` é o que o desfazer usa para estornar. Se ficasse -800 sem o
    débito ter acontecido, desfazer CRIARIA R$ 800 do nada na Carteira."""
    _connect_fake_bank(user_id)
    db.create_investment(user_id, "Renda Fixa", 0.14, "yearly")

    launch_id, _acc, _inv, _canon = db.investment_deposit_from_account(
        user_id, "Renda Fixa", 800, "t", debit_account=False,
    )

    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select efeitos from launches where id=%s", (launch_id,))
        assert cur.fetchone()["efeitos"]["delta_conta"] == 0

    db.delete_launch_and_rollback(user_id, launch_id)
    assert float(db.get_balance(user_id)) == 0.0   # desfazer não inventou dinheiro


def test_caixinha_com_banco_conectado_nao_debita_a_carteira(user_id):
    from core.handlers import pockets as h_pockets

    _connect_fake_bank(user_id)
    db.create_pocket(user_id, "Viagem")

    msg = h_pockets.deposit(user_id, "coloquei 200 na caixinha viagem",
                            {"pocket_name": "Viagem", "amount": 200})

    assert "✅" in msg
    assert float(db.get_balance(user_id)) == 0.0
    pockets = {p["name"]: float(p["balance"]) for p in db.list_pockets(user_id)}
    assert pockets["Viagem"] == 200.0


# ─── a mensagem que enganou ─────────────────────────────────────────────────

def test_mensagem_de_saldo_sem_banco_mostra_o_numero(user_id):
    db.add_launch_and_update_balance(user_id, "receita", 50, None, "seed")
    msg = h_investments._msg_saldo_insuficiente(user_id, 800)
    assert "R$ 50,00" in msg and "R$ 800,00" in msg
    assert "Saldo insuficiente na conta para esse aporte." not in msg


def test_mensagem_de_saldo_com_banco_explica_a_diferenca(user_id):
    _connect_fake_bank(user_id)
    msg = h_investments._msg_saldo_insuficiente(user_id, 800)
    assert "Carteira" in msg
    assert "R$ 0,00" in msg          # a Carteira
    assert "R$ 1.387,76" in msg      # o que ele vê na tela
    assert "banco" in msg.lower()


# ─── o aviso antes de cadastrar ─────────────────────────────────────────────

def test_investimento_novo_com_banco_conectado_avisa_antes(user_id):
    _connect_fake_bank(user_id)

    msg = h_investments.deposit(
        user_id, "Investi 800 na renda fixa",
        {"investment_name": "renda fixa", "amount": 800},
    )

    assert "Open Finance" in msg
    assert "registrar mesmo assim" in msg
    pend = db.get_pending_action(user_id)
    assert pend["action_type"] == "investment_create"
    assert pend["payload"]["etapa"] == "of_aviso"
    assert db.list_investments(user_id) == []   # nada gravado ainda


def test_investimento_novo_sem_banco_nao_avisa(user_id):
    msg = h_investments.deposit(
        user_id, "Investi 800 na renda fixa",
        {"investment_name": "renda fixa", "amount": 800},
    )
    assert "Open Finance" not in msg
    assert db.get_pending_action(user_id)["payload"]["etapa"] == "nome"


def test_investimento_que_ja_existe_nao_avisa(user_id):
    """O aviso diz "ele entra sozinho pelo Open Finance". Para um ativo que o
    usuário cadastrou de propósito isso é falso — e avisar em todo aporte só
    treinaria o usuário a ignorar o aviso."""
    _connect_fake_bank(user_id)
    db.create_investment(user_id, "CDB XP", 0.12, "yearly")

    msg = h_investments.deposit(
        user_id, "Investi 800 no cdb xp",
        {"investment_name": "CDB XP", "amount": 800},
    )

    assert "Open Finance" not in msg
    assert "✅" in msg


@pytest.mark.parametrize("resposta", ["registrar mesmo assim", "sim", "registrar"])
def test_aviso_afirmativa_segue_para_a_taxa(user_id, resposta):
    pend = {"action_type": "investment_create",
            "payload": {"amount": 800.0, "etapa": "of_aviso", "nome": "Renda Fixa", "text": "x"}}
    with patch("core.handlers.investments.db.set_pending_action") as sp:
        msg = h_investments.resolve_pending(user_id, resposta, pend)
    assert "rende" in msg.lower()
    assert sp.call_args[0][2]["etapa"] == "taxa"


def test_aviso_texto_solto_nao_vale_como_consentimento(user_id):
    """O gate serve justamente para quem está em dúvida — não pode cair sozinho."""
    pend = {"action_type": "investment_create",
            "payload": {"amount": 800.0, "etapa": "of_aviso", "nome": "Renda Fixa", "text": "x"}}
    with patch("core.handlers.investments.db.set_pending_action") as sp, \
         patch("core.handlers.investments.db.create_investment_db") as create:
        msg = h_investments.resolve_pending(user_id, "como assim?", pend)
    create.assert_not_called()
    sp.assert_not_called()                       # continua na mesma etapa
    assert "registrar mesmo assim" in msg


def test_aviso_negativa_cancela(user_id):
    pend = {"action_type": "investment_create",
            "payload": {"amount": 800.0, "etapa": "of_aviso", "nome": "Renda Fixa", "text": "x"}}
    with patch("core.handlers.investments.db.clear_pending_action") as clear, \
         patch("core.handlers.investments.db.create_investment_db") as create:
        msg = h_investments.resolve_pending(user_id, "não", pend)
    create.assert_not_called()
    clear.assert_called_once_with(user_id)
    assert "cancelei" in msg.lower()


def test_aviso_sem_nome_sugerido_pergunta_o_nome(user_id):
    pend = {"action_type": "investment_create",
            "payload": {"amount": 800.0, "etapa": "of_aviso", "nome": "", "text": "x"}}
    with patch("core.handlers.investments.db.set_pending_action") as sp:
        msg = h_investments.resolve_pending(user_id, "registrar mesmo assim", pend)
    assert "nome" in msg.lower()
    assert sp.call_args[0][2]["etapa"] == "nome"


def test_fluxo_completo_insistindo_cria_sem_debitar(user_id):
    """Ponta a ponta: aviso → insiste → nome já sugerido → taxa → criado."""
    _connect_fake_bank(user_id)

    h_investments.deposit(user_id, "Investi 800 na renda fixa",
                          {"investment_name": "renda fixa", "amount": 800})
    h_investments.resolve_pending(user_id, "registrar mesmo assim",
                                  db.get_pending_action(user_id))
    msg = h_investments.resolve_pending(user_id, "100% do CDI",
                                        db.get_pending_action(user_id))

    assert "✅" in msg
    invs = {i["name"]: float(i["balance"]) for i in db.list_investments(user_id)}
    assert invs["Renda Fixa"] == 800.0
    assert float(db.get_balance(user_id)) == 0.0   # Carteira intacta
    assert db.get_pending_action(user_id) is None


# ─── o resgate é o espelho: não pode creditar a Carteira ────────────────────
#
# Sem isto a correção ficaria meio torta: o aporte não debita, mas o resgate
# creditaria — inflando o consolidado (Carteira + bancos) com o mesmo dinheiro
# que o sync do banco vai trazer de volta.

def test_resgate_com_banco_conectado_nao_credita_a_carteira(user_id):
    _connect_fake_bank(user_id)
    db.create_investment(user_id, "Renda Fixa", 0.14, "yearly")
    db.investment_deposit_from_account(user_id, "Renda Fixa", 800, "t", debit_account=False)
    assert float(db.get_balance(user_id)) == 0.0

    msg = h_investments.withdraw(user_id, "resgatei 300 da renda fixa",
                                 {"investment_name": "Renda Fixa", "amount": 300})

    assert "📤" in msg or "✅" in msg
    assert float(db.get_balance(user_id)) == 0.0   # não inventou dinheiro na Carteira


def test_resgate_sem_banco_continua_creditando(user_id):
    db.create_investment(user_id, "Renda Fixa", 0.14, "yearly")
    db.add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    h_investments.deposit(user_id, "investi 800 na renda fixa",
                          {"investment_name": "Renda Fixa", "amount": 800})
    assert float(db.get_balance(user_id)) == 200.0

    h_investments.withdraw(user_id, "resgatei 300 da renda fixa",
                           {"investment_name": "Renda Fixa", "amount": 300})

    assert float(db.get_balance(user_id)) > 200.0


def test_caixinha_resgate_com_banco_conectado_nao_credita(user_id):
    from core.handlers import pockets as h_pockets

    _connect_fake_bank(user_id)
    db.create_pocket(user_id, "Viagem")
    db.pocket_deposit_from_account(user_id, "Viagem", 200, "t", debit_account=False)

    h_pockets.withdraw(user_id, "retirei 50 da caixinha viagem",
                       {"pocket_name": "Viagem", "amount": 50})

    assert float(db.get_balance(user_id)) == 0.0


# ─── o caminho da IA segue a mesma regra ────────────────────────────────────

def test_ia_aporta_sem_debitar_com_banco_conectado(user_id):
    from core.services.ai_chat.tools.investments import _investment_deposit_execute

    _connect_fake_bank(user_id)
    db.create_investment(user_id, "Renda Fixa", 0.14, "yearly")

    out = _investment_deposit_execute(user_id, {"name": "Renda Fixa", "amount": 800})

    assert "✅" in out
    assert float(db.get_balance(user_id)) == 0.0
    invs = {i["name"]: float(i["balance"]) for i in db.list_investments(user_id)}
    assert invs["Renda Fixa"] == 800.0


def test_ia_mensagem_de_saldo_tambem_ficou_certeira(user_id):
    from core.services.ai_chat.tools.investments import _investment_deposit_execute

    db.create_investment(user_id, "Renda Fixa", 0.14, "yearly")
    db.add_launch_and_update_balance(user_id, "receita", 50, None, "seed")

    out = _investment_deposit_execute(user_id, {"name": "Renda Fixa", "amount": 800})

    assert "Saldo insuficiente na conta pra esse aporte." not in out
    assert "R$ 50,00" in out and "R$ 800,00" in out
