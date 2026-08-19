from datetime import date
from unittest.mock import patch

import pytest

from core.handlers import investments as h_investments


def test_list_investments_capitaliza_antes_de_responder():
    rows = [
        {"name": "CDB Banco Luso", "balance": 821.91, "rate": 1.16, "period": "cdi", "last_date": date(2026, 4, 16)},
        {"name": "Nu Reserva Planejada", "balance": 11287.35, "rate": 0.14, "period": "yearly", "last_date": date(2026, 4, 15)},
    ]

    with patch("core.handlers.investments.db.accrue_all_investments", return_value=rows) as accrue, \
         patch("core.handlers.investments.build_dashboard_link", return_value="https://app.test/d/abc") as link:
        msg = h_investments.list_investments(123)

    accrue.assert_called_once_with(123)
    link.assert_called_once_with(123, view="investments")
    assert "CDB Banco Luso" in msg
    assert "Nu Reserva Planejada" in msg
    assert "R$ 821,91 (116% CDI)" in msg
    assert "R$ 11.287,35 (14%)" in msg
    assert "https://app.test/d/abc" in msg


def test_create_investment_redireciona_para_dashboard():
    with patch("core.handlers.investments.db.accrue_all_investments", return_value=[]) as accrue, \
         patch("core.handlers.investments.build_dashboard_link", return_value="https://app.test/d/abc") as link, \
         patch("core.handlers.investments.db.create_investment_db") as create_db:
        msg = h_investments.create(123, "CDB Teste 116% CDI", "criar investimento CDB Teste 116% CDI")

    create_db.assert_not_called()
    accrue.assert_called_once_with(123)
    link.assert_called_once_with(123, view="investments")
    assert "criação de investimentos agora é feita pelo dashboard" in msg
    assert "https://app.test/d/abc" in msg


def test_create_investment_nao_cria_ipca_spread_pelo_bot():
    with patch("core.handlers.investments.db.accrue_all_investments", return_value=[]) as accrue, \
         patch("core.handlers.investments.build_dashboard_link", return_value="https://app.test/d/abc") as link, \
         patch("core.handlers.investments.db.create_investment_db") as create_db:
        msg = h_investments.create(
            123,
            "LCI Banco Verde IPCA + 7,43% a.a.",
            "criar investimento LCI Banco Verde IPCA + 7,43% a.a.",
        )

    create_db.assert_not_called()
    accrue.assert_called_once_with(123)
    link.assert_called_once_with(123, view="investments")
    assert "dashboard" in msg
    assert "https://app.test/d/abc" in msg


def test_create_investment_com_aporte_inicial_redireciona_para_dashboard():
    with patch("core.handlers.investments.db.accrue_all_investments", return_value=[]) as accrue, \
         patch("core.handlers.investments.build_dashboard_link", return_value="https://app.test/d/abc") as link, \
         patch("core.handlers.investments.db.create_investment_db") as create_db:
        msg = h_investments.create(
            123,
            "CDB Banco 110% CDI valor 10000",
            "criar investimento CDB Banco 110% CDI valor 10000",
        )

    create_db.assert_not_called()
    accrue.assert_called_once_with(123)
    link.assert_called_once_with(123, view="investments")
    assert "https://app.test/d/abc" in msg


# ─── Aporte: nenhum código cru de erro pode chegar ao usuário ────────────────
#
# Regressão do bug relatado no WhatsApp: "Investi 870 em tesouro direto" com o
# investimento não cadastrado devolvia literalmente "Erro ao aportar:
# INV_NOT_FOUND". A causa era casar substring — `"not found" in err.lower()`
# nunca casa com "inv_not_found" (espaço × underscore).

# Todos os códigos que db/investments.py levanta nos caminhos de aporte/resgate
# (inclui os do accrual, que roda dentro das duas operações). Enumerados à mão a
# partir de `grep -nE 'raise (ValueError|LookupError|RuntimeError)\(' db/investments.py`
# em vez de testar só o caso do print — é a classe que precisa ficar fechada.
_DEPOSIT_ERRORS = [
    ValueError("INSUFFICIENT_ACCOUNT"),
    ValueError("AMOUNT_INVALID"),
    ValueError("PURCHASE_DATE_FUTURE"),
    ValueError("INVALID_PERIOD"),
    ValueError("INVALID_RATE"),
    RuntimeError("ACCOUNT_MISSING"),
    RuntimeError("CDI_DAILY_NOT_AVAILABLE"),
    RuntimeError("INVESTMENT_LOOKUP_FAILED"),
]

_WITHDRAW_ERRORS = [
    LookupError("INV_NOT_FOUND"),
    ValueError("INSUFFICIENT_INVEST"),
    ValueError("AMOUNT_INVALID"),
    RuntimeError("CDI_DAILY_NOT_AVAILABLE"),
]

# Carteira com o investimento do texto já cadastrado. Sem isso o `deposit` nem
# chega a chamar o banco — ele abre o fluxo de criação (ver seção seguinte) e o
# teste de vazamento de código passaria sem exercitar nada.
_CARTEIRA_TESOURO = [
    {"name": "Tesouro Direto", "balance": 1000.0, "rate": 0.12,
     "period": "yearly", "last_date": date(2026, 4, 16)},
]


def _assert_sem_codigo_cru(msg, exc):
    code = str(exc)
    assert code not in msg, f"código cru {code!r} vazou para o usuário: {msg!r}"
    # o prefixo antigo ("Erro ao aportar: <CODE>") não pode voltar
    assert "Erro ao aportar:" not in msg
    assert "Erro ao resgatar:" not in msg
    assert msg.strip(), "resposta vazia"


@pytest.mark.parametrize("exc", _DEPOSIT_ERRORS, ids=lambda e: str(e))
def test_deposit_nunca_devolve_codigo_cru(exc):
    with patch("core.handlers.investments.db.investment_deposit_from_account",
               side_effect=exc) as dep, \
         patch("core.handlers.investments.db.accrue_all_investments",
               return_value=_CARTEIRA_TESOURO), \
         patch("core.handlers.investments.build_dashboard_link", return_value="https://app.test/d/abc"):
        msg = h_investments.deposit(
            123, "investi 870 em tesouro direto",
            {"investment_name": "tesouro direto", "amount": 870},
        )
    # guarda contra o teste virar vácuo: o erro precisa ter sido provocado
    dep.assert_called_once()
    _assert_sem_codigo_cru(msg, exc)


@pytest.mark.parametrize("exc", _WITHDRAW_ERRORS, ids=lambda e: str(e))
def test_withdraw_nunca_devolve_codigo_cru(exc):
    with patch("core.handlers.investments.db.investment_withdraw_to_account", side_effect=exc), \
         patch("core.handlers.investments.db.accrue_all_investments", return_value=[]), \
         patch("core.handlers.investments.build_dashboard_link", return_value="https://app.test/d/abc"):
        msg = h_investments.withdraw(
            123, "resgatei 100 do tesouro direto",
            {"investment_name": "tesouro direto", "amount": 100},
        )
    _assert_sem_codigo_cru(msg, exc)


def test_deposit_inv_not_found_em_corrida_oferece_criar():
    """O investimento existia na listagem e sumiu antes do aporte (corrida).

    Nunca pode virar código cru — cai no mesmo convite de criação.
    """
    with patch("core.handlers.investments.db.investment_deposit_from_account",
               side_effect=LookupError("INV_NOT_FOUND")) as dep, \
         patch("core.handlers.investments.db.accrue_all_investments",
               return_value=_CARTEIRA_TESOURO), \
         patch("core.handlers.investments.db.set_pending_action") as pend, \
         patch("core.handlers.investments.build_dashboard_link", return_value="https://app.test/d/abc"):
        msg = h_investments.deposit(
            123, "investi 870 em tesouro direto",
            {"investment_name": "tesouro direto", "amount": 870},
        )

    dep.assert_called_once()
    assert "INV_NOT_FOUND" not in msg
    assert "Tesouro Direto" in msg
    assert pend.call_args[0][1] == "investment_create"


def test_deposit_saldo_insuficiente_diz_quanto_falta():
    """"Saldo insuficiente na conta" era vago e foi o que enganou o usuário que
    via R$ 1.387,76 na tela. A resposta agora nomeia o saldo e mostra o número
    (a explicação Carteira x Bancos tem teste próprio em test_aporte_open_finance)."""
    with patch("core.handlers.investments.db.investment_deposit_from_account",
               side_effect=ValueError("INSUFFICIENT_ACCOUNT")), \
         patch("core.handlers.investments.db.accrue_all_investments",
               return_value=_CARTEIRA_TESOURO), \
         patch("core.handlers.investments.db.get_consolidated_balance",
               return_value={"manual": 50, "open_finance_bank": 0, "of_bank_count": 0}), \
         patch("core.handlers.investments.build_dashboard_link", return_value="https://app.test/d/abc"):
        msg = h_investments.deposit(
            123, "investi 999999 no tesouro direto",
            {"investment_name": "Tesouro Direto", "amount": 999999},
        )
    assert "R$ 50,00" in msg
    assert "R$ 999.999,00" in msg


def test_deposit_sucesso_responde_com_id():
    with patch("core.handlers.investments.db.investment_deposit_from_account",
               return_value=(55, 100.0, 870.0, "Tesouro Direto")), \
         patch("core.handlers.investments.db.accrue_all_investments",
               return_value=_CARTEIRA_TESOURO), \
         patch("core.handlers.investments.db.display_id_for", return_value=7):
        msg = h_investments.deposit(
            123, "investi 870 no tesouro direto",
            {"investment_name": "Tesouro Direto", "amount": 870},
        )
    assert "✅" in msg
    assert "Tesouro Direto" in msg
    assert "#7" in msg


def test_deposit_deixa_plan_limit_subir():
    """PlanLimitExceeded tem mensagem amigável e é tratado no handle_incoming —
    o catch genérico não pode engoli-la."""
    from core.services.plan_limits import PlanLimitExceeded

    with patch("core.handlers.investments.db.investment_deposit_from_account",
               side_effect=PlanLimitExceeded("investments", "Faça upgrade!")), \
         patch("core.handlers.investments.db.accrue_all_investments",
               return_value=_CARTEIRA_TESOURO):
        with pytest.raises(PlanLimitExceeded):
            h_investments.deposit(
                123, "investi 870 no tesouro direto",
                {"investment_name": "Tesouro Direto", "amount": 870},
            )


# ─── Fluxo conversacional de aporte ──────────────────────────────────────────
#
# Antes, "Investi 800 na renda fixa" sem o investimento cadastrado terminava
# num beco: a resposta mandava para o dashboard e o usuário tinha que sair do
# WhatsApp. Agora o bot pergunta — qual investimento, se é para criar, com que
# nome e com que taxa — e cria já com o aporte lançado.
#
# A taxa é perguntada porque `create_investment_db` recusa `rate <= 0`
# (db/investments.py:865): não existe criar "só com o nome".

_CARTEIRA_DUPLA = [
    {"name": "Renda Fixa BB", "balance": 500.0, "rate": 0.1,
     "period": "yearly", "last_date": date(2026, 4, 16)},
    {"name": "Renda Fixa Nubank", "balance": 0.0, "rate": 1.0,
     "period": "cdi", "last_date": date(2026, 4, 16)},
]


def _pending(mock_set_pending):
    """(action_type, payload) do último set_pending_action."""
    args = mock_set_pending.call_args[0]
    return args[1], args[2]


def test_deposit_carteira_vazia_oferece_criar_com_o_nome_dito():
    with patch("core.handlers.investments.db.accrue_all_investments", return_value=[]), \
         patch("core.handlers.investments.db.investment_deposit_from_account") as dep, \
         patch("core.handlers.investments.db.set_pending_action") as pend:
        msg = h_investments.deposit(
            123, "Investi 800 na renda fixa",
            {"investment_name": "renda fixa", "amount": 800},
        )

    dep.assert_not_called()           # não tenta aportar no que não existe
    tipo, payload = _pending(pend)
    assert tipo == "investment_create"
    assert payload["etapa"] == "nome"
    assert payload["nome"] == "Renda Fixa"
    assert payload["amount"] == 800.0
    assert "Renda Fixa" in msg
    assert "R$ 800,00" in msg


def test_deposit_carteira_vazia_sem_nome_pergunta_o_nome():
    with patch("core.handlers.investments.db.accrue_all_investments", return_value=[]), \
         patch("core.handlers.investments.db.set_pending_action") as pend:
        msg = h_investments.deposit(123, "investi 800", {"amount": 800})

    tipo, payload = _pending(pend)
    assert tipo == "investment_create"
    assert payload["nome"] == ""
    assert "nome" in msg.lower()


def test_deposit_nome_ambiguo_lista_so_os_candidatos():
    with patch("core.handlers.investments.db.accrue_all_investments", return_value=_CARTEIRA_DUPLA), \
         patch("core.handlers.investments.db.investment_deposit_from_account") as dep, \
         patch("core.handlers.investments.db.set_pending_action") as pend:
        msg = h_investments.deposit(
            123, "Investi 800 na renda fixa",
            {"investment_name": "renda fixa", "amount": 800},
        )

    dep.assert_not_called()           # aportar no errado só se desfaz com resgate
    tipo, payload = _pending(pend)
    assert tipo == "investment_pick"
    assert payload["amount"] == 800.0
    assert "Renda Fixa BB" in msg
    assert "Renda Fixa Nubank" in msg


def test_deposit_nome_desconhecido_com_carteira_lista_a_carteira_inteira():
    with patch("core.handlers.investments.db.accrue_all_investments", return_value=_CARTEIRA_DUPLA), \
         patch("core.handlers.investments.db.set_pending_action") as pend:
        msg = h_investments.deposit(
            123, "investi 800 no tesouro direto",
            {"investment_name": "tesouro direto", "amount": 800},
        )

    tipo, _payload = _pending(pend)
    assert tipo == "investment_pick"
    assert "Renda Fixa BB" in msg and "Renda Fixa Nubank" in msg
    assert "criar" in msg.lower()      # a saída de cadastrar continua oferecida


def test_deposit_match_unico_parcial_usa_o_nome_canonico():
    with patch("core.handlers.investments.db.accrue_all_investments", return_value=_CARTEIRA_DUPLA), \
         patch("core.handlers.investments.db.investment_deposit_from_account",
               return_value=(9, 0.0, 800.0, "Renda Fixa Nubank")) as dep, \
         patch("core.handlers.investments.db.display_id_for", return_value=3):
        msg = h_investments.deposit(
            123, "investi 800 na nubank",
            {"investment_name": "nubank", "amount": 800},
        )

    assert dep.call_args[0][1] == "Renda Fixa Nubank"
    assert "✅" in msg


def test_deposit_acento_e_caixa_nao_atrapalham():
    rows = [{"name": "Reserva de Emergência", "balance": 10.0, "rate": 1.0,
             "period": "cdi", "last_date": date(2026, 4, 16)}]
    with patch("core.handlers.investments.db.accrue_all_investments", return_value=rows), \
         patch("core.handlers.investments.db.investment_deposit_from_account",
               return_value=(9, 0.0, 810.0, "Reserva de Emergência")) as dep, \
         patch("core.handlers.investments.db.display_id_for", return_value=3):
        h_investments.deposit(
            123, "investi 800 na reserva de emergencia",
            {"investment_name": "RESERVA DE EMERGENCIA", "amount": 800},
        )

    assert dep.call_args[0][1] == "Reserva de Emergência"


# --- respostas às perguntas (resolve_pending) --------------------------------

def _pend(tipo, **payload):
    return {"action_type": tipo, "payload": payload}


def test_pick_nome_escolhido_aporta():
    pending = _pend("investment_pick", amount=800.0, text="Investi 800 na renda fixa", alvo="renda fixa")
    with patch("core.handlers.investments.db.accrue_all_investments", return_value=_CARTEIRA_DUPLA), \
         patch("core.handlers.investments.db.clear_pending_action") as clear, \
         patch("core.handlers.investments.db.investment_deposit_from_account",
               return_value=(9, 0.0, 1300.0, "Renda Fixa BB")) as dep, \
         patch("core.handlers.investments.db.display_id_for", return_value=4):
        msg = h_investments.resolve_pending(123, "Renda Fixa BB", pending)

    clear.assert_called_once_with(123)
    assert dep.call_args[0][1] == "Renda Fixa BB"
    assert dep.call_args[0][2] == 800.0
    assert "✅" in msg and "#4" in msg


def test_pick_criar_nome_muda_para_o_fluxo_de_criacao():
    pending = _pend("investment_pick", amount=800.0, text="Investi 800", alvo="")
    with patch("core.handlers.investments.db.accrue_all_investments", return_value=_CARTEIRA_DUPLA), \
         patch("core.handlers.investments.db.set_pending_action") as pend, \
         patch("core.handlers.investments.db.investment_deposit_from_account") as dep:
        msg = h_investments.resolve_pending(123, "criar CDB Inter", pending)

    dep.assert_not_called()
    tipo, payload = _pending(pend)
    assert tipo == "investment_create"
    assert payload["nome"] == "CDB Inter"
    assert payload["amount"] == 800.0
    assert "CDB Inter" in msg


def test_pick_nome_que_nao_existe_oferece_criar():
    pending = _pend("investment_pick", amount=800.0, text="Investi 800", alvo="")
    with patch("core.handlers.investments.db.accrue_all_investments", return_value=_CARTEIRA_DUPLA), \
         patch("core.handlers.investments.db.set_pending_action") as pend:
        msg = h_investments.resolve_pending(123, "CDB Inter", pending)

    tipo, payload = _pending(pend)
    assert tipo == "investment_create"
    assert payload["nome"] == "CDB Inter"
    assert "CDB Inter" in msg


def test_pick_ainda_ambiguo_pergunta_de_novo():
    pending = _pend("investment_pick", amount=800.0, text="Investi 800", alvo="renda fixa")
    with patch("core.handlers.investments.db.accrue_all_investments", return_value=_CARTEIRA_DUPLA), \
         patch("core.handlers.investments.db.set_pending_action") as pend, \
         patch("core.handlers.investments.db.investment_deposit_from_account") as dep:
        msg = h_investments.resolve_pending(123, "renda fixa", pending)

    dep.assert_not_called()
    tipo, _payload = _pending(pend)
    assert tipo == "investment_pick"
    assert "Renda Fixa BB" in msg and "Renda Fixa Nubank" in msg


@pytest.mark.parametrize("negativa", ["não", "nao", "n", "cancelar", "esquece"])
def test_negativa_cancela_e_limpa_o_pendente(negativa):
    pending = _pend("investment_create", amount=800.0, etapa="nome", nome="Renda Fixa", text="x")
    with patch("core.handlers.investments.db.clear_pending_action") as clear, \
         patch("core.handlers.investments.db.set_pending_action") as pend, \
         patch("core.handlers.investments.db.create_investment_db") as create_db:
        msg = h_investments.resolve_pending(123, negativa, pending)

    clear.assert_called_once_with(123)
    pend.assert_not_called()
    create_db.assert_not_called()
    assert "cancelei" in msg.lower()


def test_create_sim_confirma_o_nome_sugerido_e_pergunta_a_taxa():
    pending = _pend("investment_create", amount=800.0, etapa="nome", nome="Renda Fixa", text="x")
    with patch("core.handlers.investments.db.set_pending_action") as pend, \
         patch("core.handlers.investments.db.create_investment_db") as create_db:
        msg = h_investments.resolve_pending(123, "sim", pending)

    create_db.assert_not_called()
    tipo, payload = _pending(pend)
    assert tipo == "investment_create"
    assert payload["etapa"] == "taxa"
    assert payload["nome"] == "Renda Fixa"
    assert "rende" in msg.lower()


def test_create_outro_texto_vira_o_nome():
    pending = _pend("investment_create", amount=800.0, etapa="nome", nome="Renda Fixa", text="x")
    with patch("core.handlers.investments.db.set_pending_action") as pend:
        msg = h_investments.resolve_pending(123, "cdb nubank", pending)

    _tipo, payload = _pending(pend)
    assert payload["nome"] == "CDB Nubank"
    assert payload["etapa"] == "taxa"
    assert "CDB Nubank" in msg


def test_create_sim_sem_nome_sugerido_pergunta_o_nome():
    pending = _pend("investment_create", amount=800.0, etapa="nome", nome="", text="x")
    with patch("core.handlers.investments.db.set_pending_action") as pend:
        msg = h_investments.resolve_pending(123, "sim", pending)

    pend.assert_not_called()          # continua na mesma etapa, sem regravar
    assert "nome" in msg.lower()


def test_create_taxa_cria_com_aporte_e_avisa_o_dashboard():
    pending = _pend("investment_create", amount=800.0, etapa="taxa", nome="Renda Fixa",
                    text="Investi 800 na renda fixa")
    with patch("core.handlers.investments.db.create_investment_db",
               return_value=(31, 7, "Renda Fixa")) as create_db, \
         patch("core.handlers.investments.db.clear_pending_action") as clear, \
         patch("core.handlers.investments.db.display_id_for", return_value=1), \
         patch("core.handlers.investments.build_dashboard_link", return_value="https://app.test/d/abc"):
        msg = h_investments.resolve_pending(123, "100% do CDI", pending)

    kwargs = create_db.call_args.kwargs
    args = create_db.call_args.args
    assert args[1] == "Renda Fixa"
    assert args[2] == 1.0              # 100% do CDI
    assert args[3] == "cdi"
    assert float(kwargs["initial_amount"]) == 800.0   # cria E aporta na mesma transação
    clear.assert_called_once_with(123)
    assert "✅" in msg
    assert "R$ 800,00" in msg
    assert "dashboard" in msg.lower()
    assert "https://app.test/d/abc" in msg


def test_create_taxa_ao_ano_tambem_serve():
    pending = _pend("investment_create", amount=800.0, etapa="taxa", nome="CDB Inter", text="x")
    with patch("core.handlers.investments.db.create_investment_db",
               return_value=(31, 7, "CDB Inter")) as create_db, \
         patch("core.handlers.investments.db.clear_pending_action"), \
         patch("core.handlers.investments.db.display_id_for", return_value=1), \
         patch("core.handlers.investments.build_dashboard_link", return_value="https://app.test/d/abc"):
        h_investments.resolve_pending(123, "12% ao ano", pending)

    args = create_db.call_args.args
    assert args[3] == "yearly"
    assert abs(float(args[2]) - 0.12) < 1e-9


def test_create_taxa_ilegivel_nao_cria_e_repergunta():
    pending = _pend("investment_create", amount=800.0, etapa="taxa", nome="Renda Fixa", text="x")
    with patch("core.handlers.investments.db.create_investment_db") as create_db, \
         patch("core.handlers.investments.db.clear_pending_action") as clear:
        msg = h_investments.resolve_pending(123, "sei lá, uma taxa boa", pending)

    create_db.assert_not_called()
    clear.assert_not_called()          # o pendente segue vivo para a próxima resposta
    assert "CDI" in msg


def test_create_sem_saldo_nao_diz_que_criou():
    """create_investment_db faz criação + aporte na MESMA transação e só dá
    commit no fim: sem saldo, nada é gravado (medido contra o Postgres).
    A resposta não pode dizer que o cadastro ficou de pé."""
    pending = _pend("investment_create", amount=800.0, etapa="taxa", nome="Renda Fixa", text="x")
    with patch("core.handlers.investments.db.create_investment_db",
               side_effect=ValueError("INSUFFICIENT_ACCOUNT")), \
         patch("core.handlers.investments.db.clear_pending_action") as clear, \
         patch("core.handlers.investments.build_dashboard_link", return_value="https://app.test/d/abc"):
        msg = h_investments.resolve_pending(123, "100% do CDI", pending)

    assert "INSUFFICIENT_ACCOUNT" not in msg
    assert "R$ 800,00" in msg
    assert "Não criei" in msg
    assert "Criei o cadastro" not in msg
    clear.assert_called_once_with(123)


def test_create_plan_limit_sobe():
    from core.services.plan_limits import PlanLimitExceeded

    pending = _pend("investment_create", amount=800.0, etapa="taxa", nome="Renda Fixa", text="x")
    with patch("core.handlers.investments.db.create_investment_db",
               side_effect=PlanLimitExceeded("investments", "Faça upgrade!")), \
         patch("core.handlers.investments.db.clear_pending_action"):
        with pytest.raises(PlanLimitExceeded):
            h_investments.resolve_pending(123, "100% do CDI", pending)


def test_resolve_pending_devolve_none_para_pendente_alheio():
    """Sem isso o fluxo prenderia o usuário: qualquer mensagem viraria resposta."""
    assert h_investments.resolve_pending(123, "quanto gastei hoje?", _pend("delete_launch")) is None
    assert h_investments.resolve_pending(
        123, "oi", {"action_type": "investment_create", "payload": {"etapa": "?"}}
    ) is None


# ─── Achados da revisão própria (o Codex está fora do ar) ────────────────────

def test_create_com_nome_que_ja_existe_nao_anuncia_aporte_falso():
    """`create_investment_db` retorna launch_id=None quando o nome já existe —
    e nesse caminho ele volta ANTES de lançar o aporte inicial (medido: o saldo
    da conta não muda). Anunciar "criado com aporte de R$ 800" aqui seria
    confirmar dinheiro que não saiu do lugar.

    Acontece por corrida: o user cadastra pelo dashboard no meio da conversa.
    """
    pending = _pend("investment_create", amount=800.0, etapa="taxa",
                    nome="Renda Fixa", text="Investi 800 na renda fixa")
    rows = [{"name": "Renda Fixa", "balance": 0.0, "rate": 1.0,
             "period": "cdi", "last_date": date(2026, 4, 16)}]

    with patch("core.handlers.investments.db.create_investment_db",
               return_value=(None, 7, "Renda Fixa")), \
         patch("core.handlers.investments.db.clear_pending_action"), \
         patch("core.handlers.investments.db.accrue_all_investments", return_value=rows), \
         patch("core.handlers.investments.db.investment_deposit_from_account",
               return_value=(44, 0.0, 800.0, "Renda Fixa")) as dep, \
         patch("core.handlers.investments.db.display_id_for", return_value=9):
        msg = h_investments.resolve_pending(123, "100% do CDI", pending)

    # o aporte que o user pediu tem que acontecer de verdade
    dep.assert_called_once()
    assert dep.call_args[0][2] == 800.0
    assert "criado" not in msg          # não existia criação nenhuma
    assert "✅" in msg and "#9" in msg


def test_pick_nome_que_comeca_com_novo_nao_vira_comando_criar():
    """Quem tem "Novo CDB" e responde exatamente isso quer aportar nele —
    o prefixo "novo" não pode comer o nome e abrir criação de um "CDB"."""
    rows = [{"name": "Novo CDB", "balance": 100.0, "rate": 0.12,
             "period": "yearly", "last_date": date(2026, 4, 16)}]
    pending = _pend("investment_pick", amount=800.0, text="investi 800", alvo="")

    with patch("core.handlers.investments.db.accrue_all_investments", return_value=rows), \
         patch("core.handlers.investments.db.clear_pending_action"), \
         patch("core.handlers.investments.db.set_pending_action") as pend, \
         patch("core.handlers.investments.db.investment_deposit_from_account",
               return_value=(1, 0.0, 900.0, "Novo CDB")) as dep, \
         patch("core.handlers.investments.db.display_id_for", return_value=2):
        msg = h_investments.resolve_pending(123, "Novo CDB", pending)

    assert dep.call_args[0][1] == "Novo CDB"
    pend.assert_not_called()            # não abriu fluxo de criação
    assert "✅" in msg


def test_pick_criar_nome_novo_continua_criando():
    """A guarda acima não pode matar o caminho legítimo: "criar X" com X
    inexistente segue para a criação."""
    rows = [{"name": "Novo CDB", "balance": 100.0, "rate": 0.12,
             "period": "yearly", "last_date": date(2026, 4, 16)}]
    pending = _pend("investment_pick", amount=800.0, text="investi 800", alvo="")

    with patch("core.handlers.investments.db.accrue_all_investments", return_value=rows), \
         patch("core.handlers.investments.db.set_pending_action") as pend, \
         patch("core.handlers.investments.db.investment_deposit_from_account") as dep:
        h_investments.resolve_pending(123, "criar Tesouro Selic 2029", pending)

    dep.assert_not_called()
    tipo, payload = _pending(pend)
    assert tipo == "investment_create"
    assert payload["nome"] == "Tesouro SELIC 2029"


def test_resolve_pending_nao_cancela_pendente_de_outro_fluxo():
    """A negativa era checada ANTES do tipo: um "não" respondendo a uma
    confirmação de exclusão limpava o pendente alheio e respondia "cancelei o
    aporte"."""
    with patch("core.handlers.investments.db.clear_pending_action") as clear:
        out = h_investments.resolve_pending(
            123, "não", {"action_type": "delete_launch", "payload": {}}
        )
    assert out is None
    clear.assert_not_called()


def test_withdraw_carteira_vazia_nao_se_contradiz():
    """"Estes são seus investimentos:" seguido de "você ainda não tem
    investimentos cadastrados" é texto contraditório."""
    with patch("core.handlers.investments.db.investment_withdraw_to_account",
               side_effect=LookupError("INV_NOT_FOUND")), \
         patch("core.handlers.investments.db.accrue_all_investments", return_value=[]), \
         patch("core.handlers.investments.build_dashboard_link", return_value="https://app.test/d/abc"):
        msg = h_investments.withdraw(
            123, "resgatei 100 do tesouro", {"investment_name": "tesouro", "amount": 100},
        )

    assert "Estes são seus investimentos" not in msg
    assert "Tesouro" in msg
    assert "https://app.test/d/abc" in msg


def test_withdraw_com_carteira_continua_listando():
    rows = [{"name": "CDB Inter", "balance": 500.0, "rate": 0.12,
             "period": "yearly", "last_date": date(2026, 4, 16)}]
    with patch("core.handlers.investments.db.investment_withdraw_to_account",
               side_effect=LookupError("INV_NOT_FOUND")), \
         patch("core.handlers.investments.db.accrue_all_investments", return_value=rows) as accrue, \
         patch("core.handlers.investments.build_dashboard_link", return_value="https://app.test/d/abc"):
        msg = h_investments.withdraw(
            123, "resgatei 100 do tesouro", {"investment_name": "tesouro", "amount": 100},
        )

    assert "CDB Inter" in msg
    assert accrue.call_count == 1       # a lista é reaproveitada, não buscada 2x
