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

RECEITA = dict(valor="73.38", frase="recebi 73,38 do fulano", saldo="188.26",
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
    pendencia(uid_pro, "-50.00", "gastei 50 no mercado", "64.88")

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
    manda(uid_pro, "gastei 50 no mercado")
    manda(uid_pro, "recebi 73,38 do fulano")
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
    pendencia(uid_pro, "100.00", "recebi 100 do fulano", "214.88", "CREDITO XPTO 9981")
    assert saldo_bruto(uid_pro) == Decimal("100")

    with pytest.raises(ValueError, match="INSUFFICIENT_ACCOUNT"):
        _aporte(uid_pro, 50)
    bill = _fatura_de(uid_pro, 80.0)
    with pytest.raises(HTTPException) as e:
        asyncio.run(pay_bill_route(_Req(), uid_pro, bill, PayBillPayload(amount=50.0)))
    assert e.value.status_code == 400
    assert ("Saldo atual: R$ 100,00 (sendo R$ 100,00 de entrada a conferir com o banco"
            in e.value.detail), e.value.detail
    assert _carteira_fonte(uid_pro) == 0
    assert consolidado(uid_pro)[1] == 100.0, "a exibição mudou"


def test_rejeitar_receita_libera_o_aporte(uid_pro, ia_fora):
    _, of_tx, _, _ = pendencia(uid_pro, "100.00", "recebi 100 do fulano", "214.88",
                               "CREDITO XPTO 9981")
    db.reject_reconciliation(uid_pro, of_tx)
    _aporte(uid_pro, 50)
    assert saldo_bruto(uid_pro) == Decimal("50")


def test_despesa_pendente_continua_contando_em_dobro(uid_pro, ia_fora):
    db.add_launch_and_update_balance(uid_pro, "receita", 100, None, "seed")
    _, of_tx, _, _ = pendencia(uid_pro, "-50.00", "gastei 50 no mercado", "64.88")
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


def test_recusa_na_conversa_cita_a_carteira_da_tela(uid_pro, ia_fora):
    """Mesma conversa: /saldo mostra 100 e a recusa não pode dizer R$ 0,00."""
    pendencia(uid_pro, "100.00", "recebi 100 do fulano", "30.00", "CREDITO XPTO 9981")
    db.create_pocket(uid_pro, "viagem")

    assert "Carteira: R$ 100,00" in manda(uid_pro, "/saldo")
    recusa = manda(uid_pro, "guardei 50 na caixinha viagem")
    assert ("Carteira*: R$ 100,00 (sendo R$ 100,00 de entrada a conferir com o banco, "
            "que não conta para pagar)") in recusa, recusa
    assert "✅" in manda(uid_pro, "guardei 20 na caixinha viagem"), "o banco cobre 20"
