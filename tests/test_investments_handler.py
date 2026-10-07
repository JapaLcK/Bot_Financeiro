from datetime import date
from unittest.mock import patch

import pytest
from psycopg.types.json import Jsonb

from conftest import usuario_pagante
from core.handlers import investido as h_investido
from core.handlers import investments as h_investments
from tests._patrimonio_helpers import conexao, investimento_manual, posicao, q


def _carteira_com(saldo: float):
    """Carteira cobrindo o valor, para o teste CHEGAR na chamada que quer exercitar.

    O aporte agora resolve a origem antes de tocar no banco (core/services/funding.py).
    Sem saldo em lugar nenhum ele responde "nenhum saldo cobre" e volta — e o mock de
    `investment_deposit_from_account` nunca é usado, o que faria o teste medir nada.
    """
    return patch("db.get_consolidated_balance", return_value={
        "manual": saldo, "open_finance_bank": 0, "of_bank_count": 0,
        "consolidated": saldo,
        "reconciliation": {"pending_count": 0, "delta_se_confirmar": 0, "receita_back": 0},
    })


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
    assert "no total" not in msg  # o total é o do banco (`carteira`); o seletor não soma


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


# ─── Aporte/resgate: nenhum código cru de erro pode chegar ao usuário ────────
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
    LookupError("INV_NOT_FOUND"),
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


def _assert_sem_codigo_cru(msg, exc):
    code = str(exc)
    assert code not in msg, f"código cru {code!r} vazou para o usuário: {msg!r}"
    # o prefixo antigo ("Erro ao aportar: <CODE>") não pode voltar
    assert "Erro ao aportar:" not in msg
    assert "Erro ao resgatar:" not in msg
    assert msg.strip(), "resposta vazia"


@pytest.mark.parametrize("exc", _DEPOSIT_ERRORS, ids=lambda e: str(e))
def test_deposit_nunca_devolve_codigo_cru(exc):
    with patch("core.handlers.investments.db.investment_deposit_from_account", side_effect=exc), \
         patch("core.handlers.investments.db.accrue_all_investments", return_value=[]), \
         patch("core.handlers.investments.build_dashboard_link", return_value="https://app.test/d/abc"):
        msg = h_investments.deposit(
            123, "investi 870 em tesouro direto",
            {"investment_name": "tesouro direto", "amount": 870},
        )
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


def test_deposit_investimento_inexistente_manda_pro_dashboard():
    """Carteira vazia: a resposta precisa dizer COMO cadastrar, não só que não achou.

    O cadastro é exclusivo do dashboard (ver `create`), então sem o link o
    usuário fica sem saída — foi exatamente o que o INV_NOT_FOUND cru fazia.
    """
    with _carteira_com(1000), \
         patch("core.handlers.investments.db.investment_deposit_from_account",
               side_effect=LookupError("INV_NOT_FOUND")), \
         patch("core.handlers.investments.db.accrue_all_investments", return_value=[]), \
         patch("core.handlers.investments.build_dashboard_link", return_value="https://app.test/d/abc"):
        msg = h_investments.deposit(
            123, "Investi 870 em tesouro direto",
            {"investment_name": "tesouro direto", "amount": 870},
        )

    assert "INV_NOT_FOUND" not in msg
    assert "Tesouro Direto" in msg          # capitalizado como no resto do bot
    assert "dashboard" in msg.lower()
    assert "https://app.test/d/abc" in msg


def test_deposit_investimento_inexistente_com_carteira_lista_os_existentes():
    rows = [{"name": "CDB Banco Luso", "balance": 821.91, "rate": 1.16,
             "period": "cdi", "last_date": date(2026, 4, 16)}]
    with _carteira_com(1000), \
         patch("core.handlers.investments.db.investment_deposit_from_account",
               side_effect=LookupError("INV_NOT_FOUND")), \
         patch("core.handlers.investments.db.accrue_all_investments", return_value=rows) as accrue, \
         patch("core.handlers.investments.build_dashboard_link", return_value="https://app.test/d/abc"):
        msg = h_investments.deposit(
            123, "investi 870 em tesouro direto",
            {"investment_name": "tesouro direto", "amount": 870},
        )

    assert "Não encontrei" in msg
    assert "CDB Banco Luso" in msg
    # a lista é reaproveitada, não buscada duas vezes
    assert accrue.call_count == 1


def test_deposit_saldo_insuficiente_continua_com_mensagem_propria():
    with _carteira_com(999999), \
         patch("core.handlers.investments.db.investment_deposit_from_account",
               side_effect=ValueError("INSUFFICIENT_ACCOUNT")), \
         patch("core.handlers.investments.build_dashboard_link", return_value="https://app.test/d/abc"):
        msg = h_investments.deposit(
            123, "investi 999999 no cdb", {"investment_name": "CDB", "amount": 999999},
        )
    # a mensagem agora nomeia o saldo e mostra o número, em vez do genérico
    assert "Saldo insuficiente" in msg
    assert "R$ 999.999,00" in msg


def test_deposit_sucesso_responde_com_id():
    with _carteira_com(1000), \
         patch("core.handlers.investments.db.investment_deposit_from_account",
               return_value=(55, 100.0, 870.0, "Tesouro Selic 2029")), \
         patch("core.handlers.investments.db.display_id_for", return_value=7):
        msg = h_investments.deposit(
            123, "investi 870 no tesouro selic 2029",
            {"investment_name": "Tesouro Selic 2029", "amount": 870},
        )
    assert "✅" in msg
    assert "Tesouro Selic 2029" in msg
    assert "#7" in msg


def test_deposit_deixa_plan_limit_subir():
    """PlanLimitExceeded tem mensagem amigável e é tratado no handle_incoming —
    o catch genérico não pode engoli-la."""
    from core.services.plan_limits import PlanLimitExceeded

    with _carteira_com(1000), \
         patch("core.handlers.investments.db.investment_deposit_from_account",
               side_effect=PlanLimitExceeded("investments", "Faça upgrade!")):
        with pytest.raises(PlanLimitExceeded):
            h_investments.deposit(
                123, "investi 870 no cdb", {"investment_name": "CDB", "amount": 870},
            )


# ─── carteira: o "meus investimentos" lê só os bancos conectados ─────────────

def _carteira(uid):
    with patch("core.handlers.investments.build_dashboard_link", return_value="https://app.test/d/abc"):
        return h_investido.carteira(uid)


def test_carteira_sem_banco_pede_para_conectar_e_esconde_o_manual():
    uid = usuario_pagante()
    investimento_manual(uid, "Tesouro Manual", "321")
    msg = _carteira(uid)
    assert msg.startswith("Ainda não sei quanto você tem investido: conecte seu banco no painel")
    assert "Tesouro Manual" not in msg and "321" not in msg and "cadastrad" not in msg
    assert "https://app.test/d/abc" in msg


# Total null: o porquê sai dos motivos, na ordem sem_banco_conectado > saldo_ausente >
# nenhum_investimento > o resto, no WhatsApp e na tool da IA (o painel:
# dashboard_v2_chat_investido.test.mjs).
_NAO_SEI = "Ainda não sei quanto você tem investido: "
_TOOL_SEM_BANCO = "com sem_banco_conectado, sugira conectar o banco"
_TOOL_SEM_SALDO = "senão, com saldo_ausente, o banco não informou o saldo dos investimentos"
_TOOL_NENHUM = "senão, com nenhum_investimento, não encontrei investimentos nos bancos conectados"
_TOOL_RESTO = "senão, a carteira ainda não foi lida no banco"
_LER = _NAO_SEI + "ainda não consegui ler seus investimentos no banco."
_NENHUM = "Não encontrei investimentos nos seus bancos conectados."


@pytest.mark.parametrize("caso, motivo, whatsapp, tool", [
    ("sem_banco", "sem_banco_conectado", _NAO_SEI + "conecte seu banco no painel para eu ver.", _TOOL_SEM_BANCO),
    ("todas_sem_saldo", "saldo_ausente", _NAO_SEI + "seu banco não informou o saldo dos seus investimentos.",
     _TOOL_SEM_SALDO),
    ("sem_saldo_e_outra_vazia", "saldo_ausente",
     _NAO_SEI + "seu banco não informou o saldo dos seus investimentos.", _TOOL_SEM_SALDO),
    ("vazia_lida", "nenhum_investimento", _NENHUM, _TOOL_NENHUM),
    ("vazia_needs_user", None, _LER, _TOOL_RESTO),       # dúvida: nunca "Não encontrei"
    ("vazia_item_missing", None, _LER, _TOOL_RESTO),
    ("sem_sync", None, _LER, _TOOL_RESTO),
    ("nunca_lida", None, _LER, _TOOL_RESTO),
    ("vazia_lida_e_outra_falhou", None, _LER, _TOOL_RESTO),
    ("vazia_lida_e_pausada_com_posicao", None, _LER, _TOOL_RESTO),  # a posição existe, só está fora
])
def test_total_desconhecido_diz_o_porque_no_whatsapp_e_na_tool(caso, motivo, whatsapp, tool):
    from core.services.ai_chat.tools import get_tool
    from core.services.ai_chat.tools.investments import _get_investment_summary
    uid = usuario_pagante()
    if caso != "sem_banco":
        c = conexao(uid, f"item-{uid}", **({"sync": None} if caso == "sem_sync" else {}))
        if caso in ("todas_sem_saldo", "sem_saldo_e_outra_vazia"):
            posicao(c, "inv-1", None)
        if caso in ("sem_saldo_e_outra_vazia", "vazia_lida_e_outra_falhou"):
            conexao(uid, f"item-b-{uid}")
        if caso in ("nunca_lida", "vazia_lida_e_outra_falhou"):
            q("update open_finance_connections set status_reason='read_failed' where id=%s", (c,))
        if caso == "vazia_needs_user":  # resolve_connection_state: NEEDS_USER grava ERROR/""
            q("update open_finance_connections set status='ERROR', status_reason='', health=%s where id=%s",
              (Jsonb({"item_status": "LOGIN_ERROR"}), c))
        if caso == "vazia_lida_e_pausada_com_posicao":
            posicao(conexao(uid, f"item-p-{uid}", status="PAUSED"), "inv-p", "999")
        if caso == "vazia_item_missing":
            q("update open_finance_connections set status='ERROR', status_reason='item_missing' where id=%s",
              (c,))
    msg = _carteira(uid)
    assert msg.startswith(whatsapp) and "R$" not in msg, msg
    out = _get_investment_summary(uid, {})
    assert out["total_investido"] is None
    assert next((m for m in ("sem_banco_conectado", "saldo_ausente", "nenhum_investimento")
                 if m in out["motivos"]), None) == motivo
    descricao = get_tool("get_investment_summary").schema["function"]["description"]
    assert tool in descricao
    assert (descricao.index(_TOOL_SEM_BANCO) < descricao.index(_TOOL_SEM_SALDO)
            < descricao.index(_TOOL_NENHUM) < descricao.index(_TOOL_RESTO))


def test_carteira_com_banco_total_por_tipo_por_banco_e_sem_saldo():
    uid = usuario_pagante()
    posicao(conexao(uid, f"item-a-{uid}", banco="Nubank"), "inv-1", "40000")
    xp = conexao(uid, f"item-b-{uid}", banco="XP")
    posicao(xp, "inv-2", "17123.45", tipo="EQUITY")
    posicao(xp, "inv-3", None, tipo="MUTUAL_FUND")
    investimento_manual(uid, "Tesouro Manual", "321")
    msg = _carteira(uid)
    assert msg.startswith("📈 **R$ 57.123,45** investidos nos bancos conectados")
    assert "Por tipo:\n• Renda fixa — R$ 40.000,00\n• Ações — R$ 17.123,45\n" \
           "• Fundos de investimento — saldo não informado pelo banco" in msg
    assert "Por banco:\n• Nubank — R$ 40.000,00\n• XP — R$ 17.123,45" in msg
    assert "desatualizado ou incompleto" in msg  # saldo_ausente
    assert "Tesouro Manual" not in msg and "321" not in msg and "R$ 0,00" not in msg
