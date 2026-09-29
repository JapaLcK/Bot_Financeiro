"""Q41: a transação do banco é de UM sistema só — o saque/depósito em espécie
(db/open_finance_cash.py) ou o conciliador comum. O switch ligado depois de
rodar desligado encontra a sombra do depósito já `pending` no "recebi 300":
responder as duas perguntas amarrava a mesma linha do banco ao manual E
debitava a Carteira pelo depósito (a Carteira da tela ia a -300, não a 0)."""
import asyncio
import os
from datetime import date, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest
from psycopg.rows import dict_row

import db
from conftest import usuario_pagante
from db.connection import get_conn
from db.open_finance import merged_wallet_delta
from db.open_finance_cash_answers import answer_link
from db.reconciliation import (
    confirm_reconciliation, list_reconciliations, reconciliation_summary, wallet_guard_delta,
    wallet_guard_delta_async,
)
from tests._of_cash_helpers import caixa, carteira, conecta, links, q, sync, tx  # noqa: F401
from tests.test_pending_rollback import _diga

DEPOSITO = dict(op="DEPOSITO", desc="Transfers")
PIX = dict(op="PIX", desc="Pix recebido Joao", category="Pix recebido")


def _tela(uid) -> Decimal:
    with get_conn() as conn, conn.cursor() as cur:
        return carteira(uid) + merged_wallet_delta(cur, uid)


def _guardas(uid):
    """A guarda de cobertura, pelo cursor sync e pelo async (frontend/routes/cards.py)."""
    with get_conn() as conn, conn.cursor() as cur:
        sync_ = wallet_guard_delta(cur, uid)

    async def _async():
        async with await psycopg.AsyncConnection.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
            async with conn.cursor() as cur:
                return await wallet_guard_delta_async(cur, uid)
    return sync_, asyncio.run(_async())


def _of_tx(uid):
    return q("""select t.* from open_finance_transactions t join open_finance_accounts a on a.id=t.account_id
                  join open_finance_connections c on c.id=a.connection_id
                 where c.user_id=%s and t.provider_transaction_id='d1'""", (uid,), True)[0]


def _pendente_com_switch_desligado(uid, monkeypatch, extrato=None):
    monkeypatch.delenv("OF_CASH_ENABLED")
    assert "Receita registrada" in _diga(uid, "recebi 300 do meu pai em dinheiro")
    c = conecta(uid, f"item-{uid}", desde=datetime.now() - timedelta(days=10))
    extrato = extrato or [tx("d1", 300, date.today(), **DEPOSITO)]
    sync(c, uid, extrato)
    o = _of_tx(uid)
    assert o["reconciliation_status"] == "pending" and links(uid) == []
    return c, o["id"], extrato


def _acionaveis(uid):
    return [r["of_tx_id"] for r in list_reconciliations(uid) if r["status"] == "pending"]


def test_ligar_o_switch_tira_a_pendencia_comum_do_deposito(caixa, monkeypatch):
    uid = usuario_pagante()
    c, of_tx, extrato = _pendente_com_switch_desligado(uid, monkeypatch)
    monkeypatch.setenv("OF_CASH_ENABLED", "1")
    sync(c, uid, extrato)

    assert [k["status"] for k in links(uid)] == ["perguntar_fraco"]
    assert _acionaveis(uid) == [] and reconciliation_summary(uid)["pending_count"] == 0
    assert _guardas(uid) == (0, 0), "a receita pendente escondida ainda descontava na guarda"
    with pytest.raises(ValueError, match="ALREADY_LINKED"):
        confirm_reconciliation(uid, of_tx)
    assert answer_link(uid, links(uid)[0]["id"], "cash")["changed"]
    assert _tela(uid) == 0, "a Carteira pagou o depósito duas vezes"


@pytest.mark.parametrize("fim", ["estornado", "nao_dinheiro"])
def test_a_pendencia_volta_quando_o_vinculo_deixa_de_valer(caixa, monkeypatch, fim):
    """O banco diz que não era depósito (estornado) ou o usuário diz "não era
    dinheiro": a pendência comum reaparece, e confirmar funciona."""
    uid = usuario_pagante()
    c, of_tx, extrato = _pendente_com_switch_desligado(uid, monkeypatch)
    monkeypatch.setenv("OF_CASH_ENABLED", "1")
    sync(c, uid, extrato)
    if fim == "estornado":
        sync(c, uid, [tx("d1", 300, date.today(), **PIX)])
    else:
        answer_link(uid, links(uid)[0]["id"], "not_cash")

    assert [k["status"] for k in links(uid)] == [fim]
    assert _acionaveis(uid) == [of_tx]
    assert confirm_reconciliation(uid, of_tx)["changed"]
    assert _tela(uid) == 0


def test_confirmada_antes_o_dinheiro_nao_cria_vinculo(caixa, monkeypatch):
    """O outro sentido: a linha do banco já é o "recebi 300" (confirmada com o
    switch desligado). Ligar não pergunta nem debita o depósito de novo."""
    uid = usuario_pagante()
    c, of_tx, extrato = _pendente_com_switch_desligado(uid, monkeypatch)
    assert confirm_reconciliation(uid, of_tx)["changed"]
    monkeypatch.setenv("OF_CASH_ENABLED", "1")
    sync(c, uid, extrato)

    assert links(uid) == [], "o depósito já fundido no manual virou pergunta de dinheiro"
    assert _tela(uid) == 0


def test_switch_nunca_ligado_pendencia_comum_igual_a_hoje(caixa, monkeypatch):
    """Positivo: sem o switch, a pendência do depósito é acionável e confirma."""
    uid = usuario_pagante()
    _, of_tx, _ = _pendente_com_switch_desligado(uid, monkeypatch)
    assert _acionaveis(uid) == [of_tx] and reconciliation_summary(uid)["pending_count"] == 1
    assert _guardas(uid) == (-300, -300)
    assert confirm_reconciliation(uid, of_tx)["changed"]


def test_transacao_sem_vinculo_segue_acionavel(caixa, monkeypatch):
    """Positivo: com o switch ligado, o Pix recebido (não é dinheiro) pendente
    no manual segue na lista e confirma."""
    uid = usuario_pagante()
    c, of_tx, extrato = _pendente_com_switch_desligado(uid, monkeypatch, [tx("d1", 300, date.today(), **PIX)])
    monkeypatch.setenv("OF_CASH_ENABLED", "1")
    sync(c, uid, extrato)
    assert links(uid) == [] and _acionaveis(uid) == [of_tx]
    assert confirm_reconciliation(uid, of_tx)["changed"]


def test_ordem_inversa_nao_propoe_na_transacao_do_dinheiro(caixa, monkeypatch):
    """O manual chega entre o sync que criou o vínculo e a correção que torna a
    sombra interna: a ordem inversa não abre pendência (escondida) nela."""
    uid = usuario_pagante()
    monkeypatch.delenv("OF_CASH_ENABLED")
    c = conecta(uid, f"item-{uid}", desde=datetime.now() - timedelta(days=10))
    extrato = [tx("d1", 300, date.today(), **DEPOSITO)]
    sync(c, uid, extrato)
    monkeypatch.setenv("OF_CASH_ENABLED", "1")
    sync(c, uid, extrato, corrige=False)
    assert [k["status"] for k in links(uid)] == ["perguntar_fraco"]

    x = db.add_launch_and_update_balance(uid, "receita", 300, "meu pai", None)[0]
    assert db.propose_manual_reconciliation(uid, x)["of_tx_id"] is None
    assert _of_tx(uid)["match_launch_id"] is None
