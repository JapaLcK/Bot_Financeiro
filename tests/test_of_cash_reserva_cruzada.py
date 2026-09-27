"""Q41: o "recebi 200" anotado casa com UM sistema só — o saque em espécie
(db/open_finance_cash.py) ou o conciliador comum (um Pix recebido de 200). Os
dois escolhendo o mesmo manual, e os dois confirmados, sumiam com a receita do
banco dos relatórios e descontavam o manual da Carteira duas vezes."""
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

import db
from conftest import usuario_pagante
from db.open_finance_cash_answers import answer_link
from db.reconciliation import confirm_reconciliation, list_reconciliations
from tests._of_cash_helpers import caixa, carteira, conecta, links, q, sync, tx  # noqa: F401
from tests.test_pending_rollback import _diga

PIX = dict(op="PIX", desc="Pix recebido Joao", category="Pix recebido")


def _recebi(uid):
    # "em dinheiro": com banco conectado o manual só grava em dinheiro vivo (Q40).
    assert "Receita registrada" in _diga(uid, "recebi 200 do meu pai em dinheiro")
    return q("select id from launches where user_id=%s and tipo='receita' "
             "and coalesce(source, 'manual')='manual' order by id limit 1",
             (uid,), True)[0]["id"]


def _pix(uid):
    return q("""select t.* from open_finance_transactions t join open_finance_accounts a on a.id=t.account_id
                  join open_finance_connections c on c.id=a.connection_id
                 where c.user_id=%s and t.provider_transaction_id='p1'""", (uid,), True)[0]


def _interno(lid):
    return q("select is_internal_movement as i from launches where id=%s", (lid,), True)[0]["i"]


def _mesma_rodada(uid):
    """Saque e Pix recebido de 200 no mesmo sync, com o "recebi 200" anotado."""
    x = _recebi(uid)
    c = conecta(uid, f"item-{uid}", desde=datetime.now() - timedelta(days=10))
    sync(c, uid, [tx("t1", -200, date.today()), tx("p1", 200, date.today(), **PIX)])
    return x, c


def test_mesma_rodada_o_manual_vai_so_para_o_saque(caixa):
    uid = usuario_pagante()
    x, _ = _mesma_rodada(uid)
    assert [(k["status"], k["manual_launch_id"]) for k in links(uid)] == [("perguntar_manual", x)]
    assert _pix(uid)["match_launch_id"] is None, "o conciliador comum ofereceu o manual que o saque reservou"


def test_pix_pendente_primeiro_o_saque_nao_pega_o_manual(caixa):
    """Ordem inversa: o Pix já pendente no "recebi 200" reserva o manual; o
    saque que chega depois credita direto. Positivo: confirmar o Pix funciona."""
    uid = usuario_pagante()
    c = conecta(uid, f"item-{uid}", desde=datetime.now() - timedelta(days=10))
    sync(c, uid, [tx("p1", 200, date.today(), **PIX)])
    x = _recebi(uid)  # propose_manual_reconciliation → pendente no Pix
    assert _pix(uid)["match_launch_id"] == x
    sync(c, uid, [tx("p1", 200, date.today(), **PIX), tx("t1", -200, date.today())])

    assert [(k["status"], k["manual_launch_id"]) for k in links(uid)] == [("ativo", None)]
    assert confirm_reconciliation(uid, _pix(uid)["id"])["changed"]
    assert carteira(uid) == Decimal("400")  # 200 anotado + 200 do saque; a fusão desconta na exibição
    assert not _interno(x)


def _corrida(uid):
    """O estado que só a corrida (ou dado antigo) produz: as duas perguntas
    abertas no mesmo manual."""
    x, c = _mesma_rodada(uid)
    q("update open_finance_transactions set match_launch_id=%s, reconciliation_status='pending' where id=%s",
      (x, _pix(uid)["id"]))
    return x, c


def test_e_o_mesmo_primeiro_o_pix_nao_confirma_no_mesmo_manual(caixa):
    uid = usuario_pagante()
    x, _ = _corrida(uid)
    assert answer_link(uid, links(uid)[0]["id"], "same")["changed"]
    assert _pix(uid)["id"] not in [r["of_tx_id"] for r in list_reconciliations(uid)]
    with pytest.raises(ValueError, match="ALREADY_LINKED"):
        confirm_reconciliation(uid, _pix(uid)["id"])
    assert _interno(x) and _pix(uid)["imported_launch_id"] != x


def test_na_corrida_o_saque_ganha_e_o_pix_nao_confirma(caixa):
    """Mesmo sem o "é o mesmo": a pergunta do saque aberta já reserva o manual."""
    uid = usuario_pagante()
    x, _ = _corrida(uid)
    assert _pix(uid)["id"] not in [r["of_tx_id"] for r in list_reconciliations(uid)]
    with pytest.raises(ValueError, match="ALREADY_LINKED"):
        confirm_reconciliation(uid, _pix(uid)["id"])
    assert not _interno(x) and links(uid)[0]["status"] == "perguntar_manual"


def test_manual_fundido_por_outro_escritor_o_e_o_mesmo_recusa(caixa):
    """Com a pergunta aberta, o manual é fundido numa transação por outra porta
    (dado antigo, ou o `_bind` das declarações bancárias): o "é o mesmo" recusa
    e o sync seguinte refaz a decisão sem ele."""
    uid = usuario_pagante()
    x, c = _mesma_rodada(uid)
    q("update open_finance_transactions set imported_launch_id=%s, match_launch_id=%s, "
      "reconciliation_status='confirmed' where id=%s", (x, x, _pix(uid)["id"]))
    r = answer_link(uid, links(uid)[0]["id"], "same")
    assert (r["changed"], r.get("reason")) == (False, "MANUAL_NOT_AVAILABLE")
    assert not _interno(x)
    # O sync seguinte descasa a pergunta presa num manual que já é do Pix.
    sync(c, uid, [tx("t1", -200, date.today()), tx("p1", 200, date.today(), **PIX)])
    assert [(k["status"], k["manual_launch_id"]) for k in links(uid)] == [("ativo", None)]
    assert carteira(uid) == Decimal("400")


def test_sem_disputa_o_e_o_mesmo_segue_valendo(caixa):
    """Positivo: sem o Pix, o "é o mesmo" continua casando."""
    uid = usuario_pagante()
    x = _recebi(uid)
    c = conecta(uid, f"item-{uid}", desde=datetime.now() - timedelta(days=10))
    sync(c, uid, [tx("t1", -200, date.today())])
    assert answer_link(uid, links(uid)[0]["id"], "same")["changed"]
    assert _interno(x) and carteira(uid) == Decimal("200")


def test_ordem_inversa_nao_propoe_o_manual_do_saque(caixa):
    """O sync cai entre o commit do "recebi 200" e a proposta da ordem inversa
    (`propose_manual_reconciliation` roda depois, em outra transação): a
    pergunta do saque já reservou o manual e o Pix não o pega."""
    uid = usuario_pagante()
    c = conecta(uid, f"item-{uid}", desde=datetime.now() - timedelta(days=10))
    sync(c, uid, [tx("p1", 200, date.today(), **PIX)])
    x = db.add_launch_and_update_balance(uid, "receita", 200, "meu pai", None)[0]
    sync(c, uid, [tx("p1", 200, date.today(), **PIX), tx("t1", -200, date.today())])
    assert [(k["status"], k["manual_launch_id"]) for k in links(uid)] == [("perguntar_manual", x)]

    assert db.propose_manual_reconciliation(uid, x)["of_tx_id"] is None
    assert _pix(uid)["match_launch_id"] is None


def _deposito_com_despesa(uid, extra=None):
    x = db.add_launch_and_update_balance(uid, "despesa", 300, "Academia", None, extra_efeitos=extra)[0]
    c = conecta(uid, f"item-{uid}", desde=datetime.now() - timedelta(days=10))
    sync(c, uid, [tx("d1", 300, date.today(), op="DEPOSITO", desc="Transfers")])
    return x


@pytest.mark.parametrize("recorrente", [True, False], ids=["of_recurring", "despesa_comum"])
def test_debito_recorrente_nao_e_candidato_do_deposito(caixa, recorrente):
    """Linha legada do `recurring_charger` (`of_recurring`, delta real) é o
    débito do banco, não o dinheiro depositado: "é o mesmo" a tornaria interna e
    o gasto sumiria. Positivo: a despesa comum de mesmo valor segue casando."""
    uid = usuario_pagante()
    x = _deposito_com_despesa(uid, {"of_recurring": True} if recorrente else None)
    esperado = ("perguntar_fraco", None) if recorrente else ("perguntar_manual", x)
    assert [(k["status"], k["manual_launch_id"]) for k in links(uid)] == [esperado]


def test_e_o_mesmo_recusa_debito_recorrente_ja_perguntado(caixa):
    """Pergunta aberta num débito recorrente (escolhido antes do filtro): a
    resposta recusa e o lançamento segue gasto."""
    uid = usuario_pagante()
    x = _deposito_com_despesa(uid)
    q("""update launches set efeitos = efeitos || '{"of_recurring": true}' where id=%s""", (x,))
    r = answer_link(uid, links(uid)[0]["id"], "same")
    assert (r["changed"], r.get("reason")) == (False, "MANUAL_NOT_AVAILABLE")
    assert not _interno(x)
