"""Q40 — review Codex P2 no #633: a resposta "cartão" e o extrato conciliado.

A: "gastei 50 no mercado" → "cartão", com cartão manual (Q2b), vai para a
fatura como a frase direta "gastei 50 no mercado no cartão"; antes passava só
BANCO e dizia que o Open Finance traria uma compra que ele nunca traz.

B: a transação do banco confirmada como o lançamento manual
(`confirm_reconciliation` apaga a sombra) continua "no extrato".
"""
from __future__ import annotations

import pytest

import db
from db.open_finance import buscar_no_extrato
from utils_date import today_tz

from tests._fusao_of_helpers import (  # noqa: F401 (fixtures)
    conecta_banco, ia_fora, manda, of_tx_pendente, sincroniza, tx, uid_pro)
from tests.test_forma_pagamento_conversa import (  # noqa: F401 (fixtures)
    _q, com_of, compras_credito, fala, manuais, pendencia, sem_of)


def _manual(uid):
    db.set_default_card(uid, db.create_card(uid, "Manual", closing_day=10, due_day=17))


def _fatura(uid) -> list[float]:
    return [float(r) for r in _q(
        "select coalesce(array_agg(valor order by valor), '{}') v from credit_transactions "
        "where user_id=%s", (uid,))["v"]]


# ── A: a resposta "cartão" ──────────────────────────────────────────────────

@pytest.mark.parametrize("resposta", ["cartão", "crédito", "cartão de crédito", "foi no cartão"])
def test_resposta_cartao_com_manual_vai_para_a_fatura(com_of, ia_fora, resposta):
    _manual(com_of)
    manda(com_of, "gastei 50 no mercado")
    r = manda(com_of, resposta)
    assert "Compra no Crédito Registrada" in r and "Não registrei" not in r, r
    assert _fatura(com_of) == [50.0] and manuais(com_of) == 0


def test_resposta_cartao_multi_vai_cada_um_para_a_fatura(com_of, ia_fora):
    _manual(com_of)
    manda(com_of, "gastei 300 no ifood e 150 na farmácia")
    manda(com_of, "cartão")
    assert _fatura(com_of) == [150.0, 300.0] and manuais(com_of) == 0


def test_resposta_cartao_duas_vezes_grava_uma(com_of, ia_fora):
    _manual(com_of)
    manda(com_of, "gastei 50 no mercado")
    manda(com_of, "cartão")
    manda(com_of, "cartão")
    assert _fatura(com_of) == [50.0]


def test_resposta_cartao_no_audio_so_o_gasto_vai_para_a_fatura(com_of, ia_fora, monkeypatch):
    _manual(com_of)
    fala(monkeypatch, com_of, "gastei 50 no mercado e gastei 30 no uber")
    manda(com_of, "cartão")
    assert _fatura(com_of) == [30.0, 50.0] and manuais(com_of) == 0


def test_resposta_cartao_no_audio_conta_segue_pelo_banco(com_of, ia_fora, monkeypatch):
    import db.bills as B
    _manual(com_of)
    conta = B.create_boleto(com_of, "Luz", 120.0, today_tz(), category="moradia")
    # Só o gasto ganha o "no cartão": a conta segue como na resposta "pix"
    # (a frase direta "paguei a luz de 120 no cartão" viraria compra de 120).
    fala(monkeypatch, com_of, "gastei 50 no mercado e paguei a luz de 120")
    manda(com_of, "cartão")
    assert _fatura(com_of) == [50.0] and manuais(com_of) == 0
    assert B.get_bill(com_of, conta["id"])["status"] == "paid"


# Positivos: o que não é crédito segue como antes.

@pytest.mark.parametrize("resposta", ["pix", "débito", "cartão de débito"])
def test_resposta_banco_nao_credito_nao_grava(com_of, ia_fora, resposta):
    _manual(com_of)
    manda(com_of, "gastei 50 no mercado")
    r = manda(com_of, resposta)
    assert "Não registrei" in r, r
    assert _fatura(com_of) == [] and manuais(com_of) == 0


def test_resposta_cartao_sem_manual_o_open_finance_traz(com_of, ia_fora):
    manda(com_of, "gastei 50 no mercado")
    r = manda(com_of, "cartão")
    assert "Não registrei" in r and "Open Finance" in r, r
    assert _fatura(com_of) == [] and manuais(com_of) == 0 and pendencia(com_of) is None


def test_resposta_cartao_em_receita_nao_vira_compra(com_of, ia_fora):
    _manual(com_of)
    manda(com_of, "recebi 500")
    r = manda(com_of, "cartão")
    assert "Não registrei" in r, r
    assert _fatura(com_of) == [] and manuais(com_of) == 0


def test_resposta_dinheiro_com_manual_grava_na_carteira(com_of, ia_fora):
    _manual(com_of)
    manda(com_of, "gastei 50 no mercado")
    manda(com_of, "dinheiro")
    assert manuais(com_of) == 1 and _fatura(com_of) == []


# ── B: o extrato conciliado ─────────────────────────────────────────────────

def _pendente(uid):
    """Manual "gastei 50 no mercado em dinheiro" × MERCADO -50 do banco: o
    import propõe o casamento (sombra viva + transação pendente)."""
    conexao = conecta_banco(uid, "1000.00")
    manda(uid, "gastei 50 no mercado em dinheiro")
    sincroniza(conexao, uid, "950.00", [tx(uid, "-50.00", today_tz(), "MERCADO")])
    assert db.import_open_finance_launches(uid, conexao)["pending"] == 1


def _linhas(uid):
    return [(r["dia"], r["alvo"], float(r["valor"])) for r in buscar_no_extrato(uid, "despesa", 50)]


def test_conciliado_continua_no_extrato(uid_pro, ia_fora):
    _pendente(uid_pro)
    assert _linhas(uid_pro) == [(today_tz(), "MERCADO", 50.0)]  # sombra viva: uma linha
    db.confirm_reconciliation(uid_pro, of_tx_pendente(uid_pro))
    assert _linhas(uid_pro) == [(today_tz(), "MERCADO", 50.0)]
    r = manda(uid_pro, "gastei 50 no mercado no pix")
    assert f"{today_tz():%d/%m} · MERCADO · R$ 50,00" in r and "Ainda não apareceu" not in r, r


def test_conciliado_de_outro_usuario_nao_aparece(uid_pro, ia_fora, sem_of):
    _pendente(uid_pro)
    db.confirm_reconciliation(uid_pro, of_tx_pendente(uid_pro))
    conecta_banco(sem_of, "1000.00")
    assert buscar_no_extrato(sem_of, "despesa", 50) == []
    assert buscar_no_extrato(uid_pro, "receita", 50) == []


def test_duas_tx_no_mesmo_lancamento_listam_uma_linha(uid_pro, ia_fora):
    """`imported_launch_id` não é único: o `_bind` de `db/bank_movements.py`
    liga uma segunda transação ao lançamento já conciliado. Uma linha por
    lançamento, não uma por transação."""
    from db.bank_movements import _bind
    _pendente(uid_pro)
    of1 = of_tx_pendente(uid_pro)
    db.confirm_reconciliation(uid_pro, of1)
    lanc = _q("select imported_launch_id i from open_finance_transactions where id=%s", (of1,))["i"]
    conexao = _q("select id from open_finance_connections where user_id=%s", (uid_pro,))["id"]
    sincroniza(conexao, uid_pro, "900.00", [tx(uid_pro, "-50.00", today_tz(), "MERCADO"),
                                             tx(uid_pro, "-50.00", today_tz(), "MERCADO2", "2")])
    of2 = _q("select id from open_finance_transactions where provider_transaction_id=%s",
             (f"of-tx-{uid_pro}-2",))["id"]
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            _bind(cur, uid_pro, {"launch_id": lanc}, {"id": of2, "imported_launch_id": None}, "manual")
        conn.commit()
    assert _q("select count(*) n from open_finance_transactions where imported_launch_id=%s",
              (lanc,))["n"] == 2  # o cenário existe: dois vínculos no mesmo lançamento
    assert _linhas(uid_pro) == [(today_tz(), "MERCADO", 50.0)]


def _compra_na_conexao(uid, item):
    """Mesma compra (mesma conta e mesmo id no provedor) vista por uma conexão."""
    from decimal import Decimal
    c = db.save_pluggy_open_finance_item(uid, {
        "id": item, "connector": {"id": 612, "name": "Nubank"}, "status": "UPDATED"})
    db.save_open_finance_sync(c["id"], [{
        "provider_account_id": f"acc-{uid}", "name": "Nubank", "type": "CREDIT",
        "currency": "BRL", "balance": Decimal("-50"), "raw": {},
        "transactions": [{"provider_transaction_id": f"cc-{uid}", "description": "MERCADO LIVRE",
                          "amount": Decimal("-50"), "transaction_date": today_tz(),
                          "transacted_at": None, "category": "Shopping", "raw": {}}]}])
    db.import_open_finance_credit(uid, c["id"])


def test_reconexao_lista_a_compra_do_cartao_uma_vez(uid_pro, ia_fora):
    """`imported_credit_tx_id` não é único: na reconexão o importador deduplica
    pelo id do provedor e liga a tx nova à MESMA compra. Uma linha por compra."""
    _compra_na_conexao(uid_pro, f"item-a-{uid_pro}")
    _compra_na_conexao(uid_pro, f"item-b-{uid_pro}")
    assert compras_credito(uid_pro) == 1  # o cenário existe: uma compra,
    assert _q("select count(*) n from open_finance_transactions o "
              "join open_finance_accounts a on a.id = o.account_id "
              "join open_finance_connections c on c.id = a.connection_id "
              "where c.user_id=%s and o.imported_credit_tx_id is not null",
              (uid_pro,))["n"] == 2  # duas tx ligadas a ela
    assert _linhas(uid_pro) == [(today_tz(), "MERCADO LIVRE", 50.0)]
