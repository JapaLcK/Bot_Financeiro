"""Q41: o banco muda de novo uma transação já decidida, e o switch é desligado
depois de haver vínculos. Estado terminal do BANCO (historico, estornado) reabre;
decisão do USUÁRIO (desfeito, nao_dinheiro, "apagar #N") é permanente."""
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

import db
from conftest import usuario_pagante
from db.open_finance_cash_answers import answer_link, undo_link
from tests._of_cash_helpers import (  # noqa: F401  (fixture)
    caixa, carteira, conecta, dia, launches_visiveis, links, q, sync, tx,
)
from tests.test_of_cash_estorno import _webhook_apaga
from tests.test_pending_rollback import _diga

COMPRA = {"op": "CARTAO", "desc": "Compra", "category": "Shopping"}


def _autos(uid):
    return q("select count(*) as n from launches where user_id=%s and source='manual' "
             "and categoria='transferencia_interna'", (uid,), True)[0]["n"]


def test_desligar_o_switch_mantem_o_deposito_fora_da_receita(caixa, monkeypatch):
    uid = usuario_pagante()
    c = conecta(uid, f"item-{uid}")
    extrato = [tx("d1", 300, dia(10), op="DEPOSITO", desc="Transfers")]
    sync(c, uid, extrato)
    answer_link(uid, links(uid)[0]["id"], "cash")
    assert carteira(uid) == Decimal("-300")

    monkeypatch.delenv("OF_CASH_ENABLED")
    sync(c, uid, extrato)

    assert carteira(uid) == Decimal("-300")
    assert launches_visiveis(uid) == [], "com o switch desligado o depósito virou receita em dobro"


def test_desligar_o_switch_segue_o_banco_e_nao_cria_vinculo(caixa, monkeypatch):
    """Desligado: o que já existe acompanha o banco (correção, reclassificação);
    saque novo não vira vínculo."""
    uid = usuario_pagante()
    c = conecta(uid, f"item-{uid}")
    sync(c, uid, [tx("t1", -200, dia(10))])
    monkeypatch.delenv("OF_CASH_ENABLED")

    sync(c, uid, [tx("t1", -250, dia(10)), tx("t2", -80, dia(12))])
    assert carteira(uid) == Decimal("250"), "a correção do banco parou com o switch desligado"
    assert len(links(uid)) == 1, "switch desligado criou vínculo novo"

    sync(c, uid, [tx("t1", -250, dia(10), **COMPRA), tx("t2", -80, dia(12))])
    assert carteira(uid) == 0, "saque reclassificado para compra ficou na Carteira"
    assert [r["status"] for r in links(uid)] == ["estornado"]
    assert Decimal("250") in [Decimal(str(r["valor"])) for r in launches_visiveis(uid)], "a compra sumiu"


@pytest.mark.parametrize("corrigida, esperado", [(dia(10), "ativo"), (dia(25, mes=1), "historico")])
def test_historico_com_data_corrigida(caixa, corrigida, esperado):
    """Antes do corte (01/02) é histórico; o banco corrige a data: depois do corte
    credita, ainda antes continua histórico."""
    uid = usuario_pagante()
    c = conecta(uid, f"item-{uid}")
    sync(c, uid, [tx("h1", -200, dia(20, mes=1))])
    assert [r["status"] for r in links(uid)] == ["historico"]

    sync(c, uid, [tx("h1", -200, corrigida)])
    antes = links(uid)[0]["updated_at"]
    sync(c, uid, [tx("h1", -200, corrigida)])

    assert links(uid)[0]["updated_at"] == antes, "reavaliou o vínculo num sync sem mudança do banco"
    assert [r["status"] for r in links(uid)] == [esperado]
    assert carteira(uid) == (Decimal("200") if esperado == "ativo" else 0)


def test_pix_saque_que_o_banco_confirma_como_saque_credita(caixa):
    uid = usuario_pagante()
    c = conecta(uid, f"item-{uid}")
    sync(c, uid, [tx("p1", -100, dia(10), op="PIX", desc="Pix Saque Loja X", category="Transfer - PIX")])
    assert [r["status"] for r in links(uid)] == ["perguntar_fraco"]

    for _ in range(2):
        sync(c, uid, [tx("p1", -100, dia(10), op="SAQUE", desc="Saque")])

    (link,) = links(uid)
    assert (link["status"], link["kind"]) == ("ativo", "saque")
    assert carteira(uid) == Decimal("100")


def test_reclassificado_e_de_volta_credita_uma_vez(caixa):
    uid = usuario_pagante()
    c = conecta(uid, f"item-{uid}")
    sync(c, uid, [tx("t1", -200, dia(10))])
    sync(c, uid, [tx("t1", -200, dia(10), **COMPRA)])
    assert carteira(uid) == 0

    for _ in range(3):
        sync(c, uid, [tx("t1", -200, dia(10))])

    assert carteira(uid) == Decimal("200")
    assert [r["status"] for r in links(uid)] == ["ativo"]
    assert _autos(uid) == 1


def test_estornado_casado_com_manual_volta_a_perguntar(caixa):
    """O "é o mesmo" estornado devolve o manual a receita comum; de volta a saque,
    pergunta de novo em vez de creditar por cima do manual (dobro)."""
    uid = usuario_pagante()
    assert "Receita registrada" in _diga(uid, "recebi 200 do meu pai")
    c = conecta(uid, f"item-{uid}", desde=datetime.now() - timedelta(days=10))
    sync(c, uid, [tx("t1", -200, date.today())])
    answer_link(uid, links(uid)[0]["id"], "same")
    sync(c, uid, [tx("t1", -200, date.today(), **COMPRA)])
    sync(c, uid, [tx("t1", -200, date.today())])

    assert carteira(uid) == Decimal("200")
    assert [r["status"] for r in links(uid)] == ["perguntar_manual"]


@pytest.mark.parametrize("decisao", ["undo", "apagar", "not_cash"])
def test_decisao_do_usuario_e_permanente_quando_o_banco_mexe(caixa, monkeypatch, decisao):
    """Positivo: desfazer / apagar #N / "não era dinheiro" não reabrem com
    reclassificação, volta, correção de data nem apagar e recriar pelo banco."""
    uid = usuario_pagante()
    c = conecta(uid, f"item-{uid}")
    op, v = ("DEPOSITO", 300) if decisao == "not_cash" else ("SAQUE", -200)

    def t(d=dia(10), **muda):
        return tx("t1", v, d, **{"op": op, "desc": "Transfers", **muda})
    sync(c, uid, [t()])
    (link,) = links(uid)
    if decisao == "undo":
        undo_link(uid, link["id"])
    elif decisao == "apagar":
        _diga(uid, f"apagar #{db.get_launch_user_seq(uid, link['launch_id'])}")
        _diga(uid, "sim")
    else:
        answer_link(uid, link["id"], "not_cash")
    antes = links(uid)[0]["status"]

    sync(c, uid, [t(**COMPRA)])
    sync(c, uid, [t()])
    sync(c, uid, [t(d=dia(12))])
    _webhook_apaga(monkeypatch, f"item-{uid}", ["t1"])  # o banco apaga e depois devolve
    sync(c, uid, [t(d=dia(12))])

    assert carteira(uid) == 0, "o banco reabriu uma decisão do usuário"
    assert [r["status"] for r in links(uid)] == [antes]
    assert _autos(uid) == 0


def _desfaz(uid, link, decisao):
    if decisao == "undo":
        undo_link(uid, link["id"])
    elif decisao == "apagar":
        _diga(uid, f"apagar #{db.get_launch_user_seq(uid, link['launch_id'])}")
        _diga(uid, "sim")
    else:  # depósito: "foi dinheiro" e depois desfazer
        answer_link(uid, link["id"], "cash")
        undo_link(uid, link["id"])


def _gastos(uid):
    return [(r["tipo"], Decimal(str(r["valor"]))) for r in launches_visiveis(uid)]


PIX_RECEBIDO = {"op": "PIX", "desc": "Pix recebido", "category": "Transfer - PIX"}
CASOS = {"undo": (-200, "SAQUE", "Same person transfer - CASH", COMPRA, "despesa"),
         "apagar": (-200, "SAQUE", "Same person transfer - CASH", COMPRA, "despesa"),
         "deposito": (300, "DEPOSITO", "Transfers", PIX_RECEBIDO, "receita")}


@pytest.mark.parametrize("decisao", list(CASOS))
def test_desfeito_que_o_banco_diz_compra_conta_como_compra(caixa, monkeypatch, decisao):
    """Desfazer esconde o lado do banco enquanto ele é dinheiro. O banco diz
    compra (ou Pix comum): a sombra que já existe muda no mesmo sync, e a
    recriada (apagar e devolver) nasce visível já no import. De volta a
    dinheiro: interna de novo, sem crédito."""
    valor, op, cat, virou, tipo = CASOS[decisao]
    dinheiro, outra = tx("t1", valor, dia(10), op=op, desc=cat), tx("t1", valor, dia(10), **virou)
    uid = usuario_pagante()
    c = conecta(uid, f"item-{uid}")
    sync(c, uid, [dinheiro])
    _desfaz(uid, links(uid)[0], decisao)
    sync(c, uid, [dinheiro])
    antes, visivel = links(uid)[0]["status"], [(tipo, abs(Decimal(valor)))]
    assert _gastos(uid) == [], "positivo: desfazer comum segue fora dos relatórios"

    sync(c, uid, [outra])
    assert _gastos(uid) == visivel, "a sombra que já existia não mudou no sync da reclassificação"
    _webhook_apaga(monkeypatch, f"item-{uid}", ["t1"])
    sync(c, uid, [outra], corrige=False)
    assert _gastos(uid) == visivel, "o import recriou a sombra escondida"

    sync(c, uid, [dinheiro])
    assert _gastos(uid) == [], "de volta a dinheiro, o lado do banco tem de sair dos relatórios"
    assert carteira(uid) == 0 and _autos(uid) == 0, "o desfazer recreditou"
    assert [r["status"] for r in links(uid)] == [antes]


def test_desfazer_fusao_de_desfeito_que_virou_compra(caixa, monkeypatch):
    """O 3º site da sombra (`undo_reconciliation`): a sombra recriada da compra é gasto."""
    uid = usuario_pagante()
    c = conecta(uid, f"item-{uid}", desde=datetime.now() - timedelta(days=10))
    hoje = date.today()
    sync(c, uid, [tx("t1", -200, hoje)])
    undo_link(uid, links(uid)[0]["id"])
    _webhook_apaga(monkeypatch, f"item-{uid}", ["t1"])
    _diga(uid, "gastei 200 no mercado")
    sync(c, uid, [tx("t1", -200, hoje, op="CARTAO", desc="Mercado", category="Groceries")])
    (o,) = q("select id, reconciliation_status as s from open_finance_transactions where provider_transaction_id='t1' "
             "and account_id in (select id from open_finance_accounts where connection_id=%s)", (c,), True)
    if o["s"] == "pending":
        assert db.confirm_reconciliation(uid, o["id"])["changed"]
    assert db.undo_reconciliation(uid, o["id"])["changed"]

    assert sorted(_gastos(uid)) == [("despesa", Decimal("200"))] * 2, "a sombra da compra nasceu interna"
