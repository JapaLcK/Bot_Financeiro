"""O "pode ser R$ X" das pendências, e as guardas que autorizam pelo menor.

Exibição NÃO muda com pendência (conta os dois lados, como antes). A guarda da
Carteira passa a ignorar receita pendente: ela só autoriza depois de confirmada
ou rejeitada. Despesa pendente já conta em dobro — é o menor, fica como está.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

import db
from core.services import funding
from db.open_finance import MERGED_WALLET_DELTA_SQL, _fused_join_sql
from utils_date import today_tz

from tests._fusao_of_helpers import (  # noqa: F401 (uid_pro/ia_fora são fixtures)
    conecta_banco, consolidado, ia_fora, manda, saldo_bruto, sincroniza, tx, uid_pro,
)
from tests.test_fusao_of_superficies_da_carteira import (  # noqa: F401 (fixture)
    _fatura_de, _Req, sem_autorizacao,
)
from tests.test_reconciliacao_resolver import _q, pendencia

RECEITA = dict(valor="73.38", frase="recebi 73,38 do fulano em dinheiro", saldo="188.26",
               descricao="CREDITO XPTO 9981")


def _resumo(uid):
    r = db.reconciliation_summary(uid)
    return r["pending_count"], r["delta_se_confirmar"], r["receita_back"]


def test_sql_da_fusao_nao_mudou():
    """O extraído gera exatamente o texto de antes do PR."""
    from db.open_finance import BANK_ACCOUNTS_SQL
    antes = f"""
    select coalesce(sum(-d), 0) as d from (
      select distinct l.id, (l.efeitos ->> 'delta_conta')::numeric as d
        from ({BANK_ACCOUNTS_SQL}) a
        join open_finance_accounts ra on ra.id = a.id
        join open_finance_accounts ta on ta.provider_account_id = ra.provider_account_id
        join open_finance_connections tc on tc.id = ta.connection_id and tc.user_id = %s
        join open_finance_transactions t on t.account_id = ta.id
        join launches l on l.id = t.imported_launch_id
       where l.user_id = %s
         and coalesce(l.source, 'manual') <> 'open_finance'
         and jsonb_typeof(l.efeitos -> 'delta_conta') = 'number'
    ) x
"""
    assert MERGED_WALLET_DELTA_SQL == antes
    assert "t.match_launch_id" in _fused_join_sql("match_launch_id", "")


def test_despesa_pendente_exibe_igual_e_pode_ser_mais(uid_pro, ia_fora):
    import frontend.finance_bot_websocket_custom as dashboard
    pendencia(uid_pro, "-50.00", "gastei 50 no mercado em dinheiro", "64.88")

    assert consolidado(uid_pro) == (14.88, -50.0), "a exibição mudou"
    snap = asyncio.run(dashboard.get_financial_data(uid_pro))
    assert snap["balance"] == -50.0
    assert _resumo(uid_pro) == (1, Decimal("50"), Decimal("0"))
    assert db.get_consolidated_balance(uid_pro)["reconciliation"]["delta_se_confirmar"] == 50
    assert snap["reconciliation"]["delta_se_confirmar"] == 50


def test_receita_pendente_pode_ser_menos(uid_pro, ia_fora):
    pendencia(uid_pro, **RECEITA)
    assert consolidado(uid_pro)[1] == 73.38
    assert _resumo(uid_pro) == (1, Decimal("-73.38"), Decimal("-73.38"))


def test_mistas_somam_com_sinal(uid_pro, ia_fora):
    hoje = today_tz()
    conexao = conecta_banco(uid_pro, "114.88")
    manda(uid_pro, "gastei 50 no mercado em dinheiro")
    manda(uid_pro, "recebi 73,38 do fulano em dinheiro")
    sincroniza(conexao, uid_pro, "138.26", [
        tx(uid_pro, "-50.00", hoje, "COMPRA CARTAO 4412 XPTO", ident="1"),
        tx(uid_pro, "73.38", hoje, "CREDITO XPTO 9981", ident="2"),
    ])
    assert db.import_open_finance_launches(uid_pro, conexao)["pending"] == 2
    assert _resumo(uid_pro) == (2, Decimal("-23.38"), Decimal("-73.38"))


def test_conexao_pausada_nao_conta(uid_pro, ia_fora):
    conexao, *_ = pendencia(uid_pro, **RECEITA)
    _q("update open_finance_connections set status='PAUSED' where id=%s returning id", (conexao,))
    assert _resumo(uid_pro)[1:] == (Decimal("0"), Decimal("0"))


# ── guardas ────────────────────────────────────────────────────────────────

def _aporte(uid, valor):
    db.create_pocket(uid, "viagem")
    return db.pocket_deposit_from_account(uid, "viagem", valor)


def _carteira_fonte(uid):
    return [f for f in funding.list_sources(uid) if f["kind"] == funding.CARTEIRA][0]["balance"]


def test_receita_pendente_nao_autoriza(uid_pro, ia_fora, sem_autorizacao):
    from fastapi import HTTPException
    from frontend.routes.cards import PayBillPayload, pay_bill_route
    pendencia(uid_pro, "100.00", "recebi 100 do fulano em dinheiro", "214.88", "CREDITO XPTO 9981")
    assert saldo_bruto(uid_pro) == Decimal("100")

    with pytest.raises(ValueError, match="INSUFFICIENT_ACCOUNT"):
        _aporte(uid_pro, 50)
    bill = _fatura_de(uid_pro, 80.0)
    # NO CONTRATO NOVO o pagamento da fatura com Open Finance ativo NÃO usa a
    # Carteira (o débito ocorre no banco): autoriza sem depender da receita
    # pendente e sem drenar o dinheiro em espécie.
    r = asyncio.run(pay_bill_route(_Req(), uid_pro, bill, PayBillPayload(amount=50.0)))
    assert r.get("ok") is True, r
    assert saldo_bruto(uid_pro) == Decimal("100"), "a Carteira foi drenada"
    assert _carteira_fonte(uid_pro) == 0
    assert consolidado(uid_pro)[1] == 100.0, "a exibição mudou"


def test_rejeitar_receita_libera_o_aporte(uid_pro, ia_fora):
    _, of_tx, _, _ = pendencia(uid_pro, "100.00", "recebi 100 do fulano em dinheiro", "214.88",
                               "CREDITO XPTO 9981")
    db.reject_reconciliation(uid_pro, of_tx)
    _aporte(uid_pro, 50)
    assert saldo_bruto(uid_pro) == Decimal("50")


def test_despesa_pendente_continua_contando_em_dobro(uid_pro, ia_fora):
    db.add_launch_and_update_balance(uid_pro, "receita", 100, None, "seed")
    _, of_tx, _, _ = pendencia(uid_pro, "-50.00", "gastei 50 no mercado em dinheiro", "64.88")
    with pytest.raises(ValueError, match="INSUFFICIENT_ACCOUNT"):
        _aporte(uid_pro, 60)
    db.confirm_reconciliation(uid_pro, of_tx)
    _aporte(uid_pro, 60)
    assert saldo_bruto(uid_pro) == Decimal("-10")


def test_sem_pendencia_autoriza_como_hoje(uid_pro, ia_fora):
    db.add_launch_and_update_balance(uid_pro, "receita", 100, None, "seed")
    conecta_banco(uid_pro, "114.88")
    _aporte(uid_pro, 100)
    assert saldo_bruto(uid_pro) == Decimal("0")


def _receita_100(uid, *gastos):
    pendencia(uid, "100.00", "recebi 100 do fulano em dinheiro", "0.00", "CREDITO XPTO 9981")
    for g in gastos:
        manda(uid, g)


def _receita_e_despesa_pendentes(uid):
    hoje = today_tz()
    conexao = conecta_banco(uid, "0.00")
    manda(uid, "Gastei 1 real com a barbara em dinheiro")
    manda(uid, "recebi 73,38 do fulano em dinheiro")
    sincroniza(conexao, uid, "0.00", [
        tx(uid, "-1.00", hoje, "COMPRA CARTAO 4412 XPTO", ident="1"),
        tx(uid, "73.38", hoje, "CREDITO XPTO 9981", ident="2"),
    ])
    assert db.import_open_finance_launches(uid, conexao)["pending"] == 2


@pytest.mark.parametrize("prepara, tela, disponivel, a_conferir", [
    (lambda u: _receita_100(u), "R$ 100,00", "R$ 0,00", "R$ 100,00"),
    (lambda u: _receita_100(u, "gastei 70 no mercado em dinheiro"), "R$ 30,00", "R$ -70,00", "R$ 100,00"),
    (lambda u: _receita_100(u, "gastei 150 no mercado em dinheiro"), "R$ -50,00", "R$ -150,00", "R$ 100,00"),
    (_receita_e_despesa_pendentes, "R$ 72,38", "R$ -1,00", "R$ 73,38"),
], ids=["nada_gasto", "entrada_maior_que_a_tela", "tela_negativa", "receita_e_despesa"])
def test_caixinha_recusa_citando_a_tela_e_fatura_com_of_nao_drena(
        uid_pro, ia_fora, sem_autorizacao, prepara, tela, disponivel, a_conferir):
    """O número da tela e o disponível, separados; nada diz que um contém o outro
    (guarda da CAIXINHA, que continua valendo). Já o pagamento de fatura com
    Open Finance ativo NÃO recusa por saldo nem drena a Carteira no contrato
    novo — o débito ocorre no banco."""
    from frontend.routes.cards import PayBillPayload, pay_bill_route
    prepara(uid_pro)
    db.create_pocket(uid_pro, "viagem")
    frase = (f"{tela} (disponível para pagar: {disponivel}, porque {a_conferir} "
             "de entrada ainda está a conferir com o banco)")

    assert f"Carteira: {tela}" in manda(uid_pro, "/saldo")
    recusa = manda(uid_pro, "guardei 50 na caixinha viagem")
    assert f"Carteira*: {frase}\n" in recusa, recusa
    antes = saldo_bruto(uid_pro)
    r = asyncio.run(pay_bill_route(_Req(), uid_pro, _fatura_de(uid_pro, 80.0),
                                   PayBillPayload(amount=50.0)))
    assert r.get("ok") is True, r
    assert saldo_bruto(uid_pro) == antes, "a Carteira foi drenada pelo pagamento"
    assert "sendo" not in recusa


def test_recusa_sem_pendencia_nao_muda(uid_pro, ia_fora, sem_autorizacao):
    """Sem pendência o texto é o de antes deste PR, byte a byte (o do bot). Já o
    pagamento de fatura com Open Finance ativo mudou de contrato: autoriza sem
    saldo na Carteira e sem drená-la (o débito ocorre no banco)."""
    from frontend.routes.cards import PayBillPayload, pay_bill_route
    db.add_launch_and_update_balance(uid_pro, "receita", 10, None, "seed")
    assert funding.msg_insuficiente(uid_pro, 50, acao="depósito") == (
        "Saldo insuficiente: você tem R$ 10,00 na conta e o depósito é de R$ 50,00.")

    conecta_banco(uid_pro, "0.00")
    assert funding.msg_insuficiente(uid_pro, 50, acao="depósito") == (
        "Nenhum dos seus saldos cobre R$ 50,00 de depósito:\n\n"
        "• **Carteira**: R$ 10,00\n"
        "• **Nubank · Nubank Conta**: R$ 0,00\n\n"
        "A **Carteira** é o dinheiro fora dos bancos conectados (espécie e contas "
        "que você não ligou) — por isso ela costuma ficar zerada depois que você conecta "
        "um banco.")
    r = asyncio.run(pay_bill_route(_Req(), uid_pro, _fatura_de(uid_pro, 80.0),
                                   PayBillPayload(amount=50.0)))
    assert r.get("ok") is True, r
    assert saldo_bruto(uid_pro) == Decimal("10"), "a Carteira foi drenada pelo pagamento"


# ── investimento: as duas guardas de Carteira com receita pendente ─────────

def test_investimento_com_receita_pendente_nao_autoriza(uid_pro, ia_fora):
    _, of_tx, _, _ = pendencia(uid_pro, "100.00", "recebi 100 do fulano em dinheiro", "214.88",
                               "CREDITO XPTO 9981")
    with pytest.raises(ValueError, match="INSUFFICIENT_ACCOUNT"):
        db.create_investment_db(uid_pro, "CDB Inicial", 0.01, "monthly", initial_amount=50)
    db.create_investment_db(uid_pro, "CDB Aporte", 0.01, "monthly")
    with pytest.raises(ValueError, match="INSUFFICIENT_ACCOUNT"):
        db.investment_deposit_from_account(uid_pro, "CDB Aporte", 50)

    db.reject_reconciliation(uid_pro, of_tx)  # POSITIVO: rejeitada, a receita autoriza
    db.investment_deposit_from_account(uid_pro, "CDB Aporte", 50)
    assert saldo_bruto(uid_pro) == Decimal("50")


# ── a pergunta "De onde sai?" ──────────────────────────────────────────────

def test_pergunta_de_origem_cita_a_carteira_da_tela(uid_pro, ia_fora):
    """Carteira na tela 300, 100 dela é receita a conferir, banco também cobre."""
    db.add_launch_and_update_balance(uid_pro, "receita", 200, None, "seed")
    pendencia(uid_pro, "100.00", "recebi 100 do fulano em dinheiro", "500.00", "CREDITO XPTO 9981")
    db.create_pocket(uid_pro, "viagem")

    assert "Carteira: R$ 300,00" in manda(uid_pro, "/saldo")
    pergunta = manda(uid_pro, "guardei 50 na caixinha viagem")
    assert ("1. *Carteira* — R$ 300,00 (disponível para pagar: R$ 200,00, porque "
            "R$ 100,00 de entrada ainda está a conferir com o banco)\n") in pergunta, pergunta


def test_pergunta_de_origem_sem_pendencia_nao_muda(uid_pro, ia_fora):
    db.add_launch_and_update_balance(uid_pro, "receita", 300, None, "seed")
    conecta_banco(uid_pro, "500.00")
    db.create_pocket(uid_pro, "viagem")
    assert manda(uid_pro, "guardei 50 na caixinha viagem") == (
        "De onde sai R$ 50,00?\n\n1. *Carteira* — R$ 300,00\n"
        "2. *Nubank · Nubank Conta* — R$ 500,00\n\n"
        "Responda com o número (ex: *1*) ou *cancelar*.")
