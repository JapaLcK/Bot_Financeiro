"""Q41 grupo 6, pela conversa real: o usuário anotou "recebi 200" e "gastei 50
no mercado"; o banco mostra o saque de 200 → pergunta; "é o mesmo" não credita
de novo."""
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

import db
from conftest import usuario_pagante
from db.open_finance_cash_answers import answer_link
from tests._of_cash_helpers import caixa, carteira, conecta, links, q, sync, tx  # noqa: F401
from tests.test_pending_rollback import _diga


def _conversa_e_banco(uid, valor_do_saque):
    assert "Receita registrada" in _diga(uid, "recebi 200 do meu pai")
    assert "mercado" in _diga(uid, "gastei 50 no mercado").lower()
    assert carteira(uid) == Decimal("150")
    c = conecta(uid, f"item-{uid}", desde=datetime.now() - timedelta(days=10))
    sync(c, uid, [tx("t1", -valor_do_saque, date.today())])
    return c


def _recebi_id(uid):
    return q("select id from launches where user_id=%s and tipo='receita' order by id limit 1",
             (uid,), True)[0]["id"]


def test_mesmo_valor_pergunta_e_o_mesmo_nao_credita_de_novo(caixa):
    uid = usuario_pagante()
    _conversa_e_banco(uid, 200)

    (link,) = links(uid)
    assert (link["status"], link["manual_launch_id"]) == ("perguntar_manual", _recebi_id(uid))
    assert carteira(uid) == Decimal("150"), "creditou o saque antes de perguntar"

    assert answer_link(uid, link["id"], "same")["changed"]
    resposta = _diga(uid, "saldo")
    assert "R$ 150,00" in resposta and "R$ 350,00" not in resposta, resposta
    assert carteira(uid) == Decimal("150")
    (link,) = links(uid)
    assert (link["status"], link["origem"], link["launch_id"]) == ("ativo", "manual", _recebi_id(uid))
    assert q("select is_internal_movement as i from launches where id=%s", (_recebi_id(uid),), True)[0]["i"]


def test_sao_diferentes_credita(caixa):
    uid = usuario_pagante()
    _conversa_e_banco(uid, 200)
    answer_link(uid, links(uid)[0]["id"], "different")
    assert carteira(uid) == Decimal("350")


def test_outro_valor_nao_casa(caixa):
    """Positivo: saque de 300 não é o "recebi 200" — credita direto."""
    uid = usuario_pagante()
    _conversa_e_banco(uid, 300)
    assert [r["status"] for r in links(uid)] == ["ativo"]
    assert carteira(uid) == Decimal("450")


def _interno(uid, lid):
    return q("select is_internal_movement as i from launches where id=%s", (lid,), True)[0]["i"]


@pytest.mark.parametrize("valor, dias_antes", [(900, 0), (200, 5)], ids=["valor", "data"])
def test_banco_corrige_e_a_pergunta_deixa_de_casar(caixa, valor, dias_antes):
    """O banco corrige o saque pendente (200→900, ou a data sai dos ±3 dias):
    o "recebi 200" não é mais candidato — o saque segue o caminho normal e "é
    o mesmo" não pode mais sumir com a diferença."""
    uid = usuario_pagante()
    c = _conversa_e_banco(uid, 200)
    (link,) = links(uid)
    sync(c, uid, [tx("t1", -valor, date.today() - timedelta(days=dias_antes))])

    assert answer_link(uid, link["id"], "same")["changed"] is False
    (link,) = links(uid)
    assert (link["status"], link["origem"], link["manual_launch_id"]) == ("ativo", "auto", None)
    assert carteira(uid) == Decimal("150") + valor
    assert not _interno(uid, _recebi_id(uid))


def test_correcao_dentro_da_tolerancia_segue_casando(caixa):
    """Positivo: 1 dia de diferença continua o mesmo saque — "é o mesmo" vale."""
    uid = usuario_pagante()
    c = _conversa_e_banco(uid, 200)
    sync(c, uid, [tx("t1", -200, date.today() - timedelta(days=1))])
    (link,) = links(uid)
    assert link["status"] == "perguntar_manual"
    assert answer_link(uid, link["id"], "same")["changed"]
    assert carteira(uid) == Decimal("150")


def test_banco_corrige_depois_do_e_o_mesmo_nada_some(caixa):
    """Já respondido "é o mesmo" e o banco corrige para 900: o casamento se
    desfaz, o "recebi 200" volta a ser receita e a Carteira recebe os 900 —
    à vista e desfazível, em vez de 700 sumidos."""
    uid = usuario_pagante()
    c = _conversa_e_banco(uid, 200)
    assert answer_link(uid, links(uid)[0]["id"], "same")["changed"]
    sync(c, uid, [tx("t1", -900, date.today())])

    (link,) = links(uid)
    assert (link["status"], link["origem"]) == ("ativo", "auto")
    assert carteira(uid) == Decimal("1050")
    assert not _interno(uid, _recebi_id(uid))


def test_e_o_mesmo_recusa_manual_que_nao_casa_mais(caixa):
    """O usuário muda a data do "recebi 200" para 10 dias antes com a pergunta
    aberta: o botão "é o mesmo" não casa o que não casa."""
    uid = usuario_pagante()
    _conversa_e_banco(uid, 200)
    assert db.update_launch_fields(uid, _recebi_id(uid), criado_em=datetime.now() - timedelta(days=10))

    r = answer_link(uid, links(uid)[0]["id"], "same")
    assert (r["changed"], r.get("reason")) == (False, "MANUAL_NOT_AVAILABLE")
    assert not _interno(uid, _recebi_id(uid))
    assert links(uid)[0]["status"] == "perguntar_manual"


@pytest.mark.parametrize("porta", ["banco_estorna", "desfazer"])
def test_soltar_o_manual_respeita_a_categoria_dele(caixa, porta):
    """Casado com "é o mesmo" e depois recategorizado como transferência interna:
    soltar o casamento (o banco deixa de dizer saque, ou o usuário desfaz)
    devolve o manual ao que a CATEGORIA dele diz — interno — e não a receita."""
    from db.open_finance_cash_answers import undo_link
    uid = usuario_pagante()
    c = _conversa_e_banco(uid, 200)
    assert answer_link(uid, links(uid)[0]["id"], "same")["changed"]
    assert db.update_launch_category(uid, _recebi_id(uid), "transferencia_interna")
    if porta == "banco_estorna":
        sync(c, uid, [tx("t1", -200, date.today(), op="CARTAO", desc="Compra")])
    else:
        assert undo_link(uid, links(uid)[0]["id"])["changed"]

    assert links(uid)[0]["status"] in ("estornado", "desfeito")
    assert _interno(uid, _recebi_id(uid))


def test_sao_diferentes_solta_o_manual_para_o_proximo_saque(caixa):
    """"São diferentes" no 1º saque de 200: o "recebi 200" fica livre, e o 2º
    saque de 200 (esse sim, talvez o anotado) pergunta em vez de creditar."""
    uid = usuario_pagante()
    c = _conversa_e_banco(uid, 200)
    assert answer_link(uid, links(uid)[0]["id"], "different")["changed"]
    sync(c, uid, [tx("t1", -200, date.today()), tx("t2", -200, date.today())])

    assert [(r["status"], r["manual_launch_id"]) for r in links(uid)][1] == ("perguntar_manual", _recebi_id(uid))
    assert carteira(uid) == Decimal("350")


def test_pergunta_aberta_segue_reservando_o_manual(caixa):
    """Positivo: com a pergunta do 1º saque aberta, o "recebi 200" não é
    oferecido também ao 2º saque de 200 — que credita direto."""
    uid = usuario_pagante()
    c = _conversa_e_banco(uid, 200)
    sync(c, uid, [tx("t1", -200, date.today()), tx("t2", -200, date.today())])

    assert [r["status"] for r in links(uid)] == ["perguntar_manual", "ativo"]
    assert carteira(uid) == Decimal("350")


@pytest.mark.parametrize("categoria, esperado", [("transferencia_interna", "perguntar_novo"),
                                                  ("presente", "perguntar_manual"), (None, "ativo")])
def test_manual_recategorizado_com_a_pergunta_aberta(caixa, categoria, esperado):
    """Com "é o mesmo?" aberto, o "recebi 200" vira transferência interna (ou é
    apagado): a escolha não o ofereceria mais, então o sync refaz a decisão em
    vez de manter uma pergunta que o botão recusa para sempre. Interno de mesmo
    valor não prova o saque novo: pergunta "já anotei?", nunca crédito. Apagado,
    credita. Positivo: outra categoria comum segue casando e "é o mesmo" vale."""
    uid = usuario_pagante()
    c = _conversa_e_banco(uid, 200)
    recebi = _recebi_id(uid)
    if categoria:
        assert db.update_launch_category(uid, recebi, categoria)
    else:
        _diga(uid, f"apagar #{db.get_launch_user_seq(uid, recebi)}")
        _diga(uid, "sim")
        assert not q("select 1 from launches where id=%s", (recebi,), True)
    sync(c, uid, [tx("t1", -200, date.today())])

    (link,) = links(uid)
    assert link["status"] == esperado, "pergunta presa ou crédito sem prova de que o saque é novo"
    assert answer_link(uid, link["id"], "same")["changed"] is (esperado == "perguntar_manual")
    assert carteira(uid) == Decimal("150"), "a Carteira contou o saque em dobro (ou não contou)"


def test_banco_corrige_dentro_da_tolerancia_depois_do_e_o_mesmo_segue_casado(caixa):
    """Positivo do predicado único: o "é o mesmo" deixou o manual interno (é o
    par). O banco corrige a data em 1 dia: o manual segue casando — o interno
    que o próprio vínculo pôs não o desqualifica, senão viria crédito em dobro."""
    uid = usuario_pagante()
    c = _conversa_e_banco(uid, 200)
    assert answer_link(uid, links(uid)[0]["id"], "same")["changed"]
    sync(c, uid, [tx("t1", -200, date.today() - timedelta(days=1))])

    (link,) = links(uid)
    assert (link["status"], link["origem"], link["launch_id"]) == ("ativo", "manual", _recebi_id(uid))
    assert carteira(uid) == Decimal("150")


@pytest.mark.parametrize("resposta, esperado", [("dinheiro vivo", ("perguntar_manual", 0)),
                                                 ("pelo banco", ("ativo", 200))])
def test_ordem_real_depois_da_q40_banco_ja_conectado(caixa, resposta, esperado):
    """A ordem de verdade: banco já conectado, o "recebi 200" pergunta a forma
    (Q40, core/handlers/forma_pagamento.py). "Dinheiro vivo" grava na Carteira e
    o saque pergunta "é o mesmo?"; "pelo banco" não grava e o saque credita."""
    uid = usuario_pagante()
    c = conecta(uid, f"item-{uid}", desde=datetime.now() - timedelta(days=10))
    sync(c, uid, [])
    assert "dinheiro vivo ou pelo banco" in _diga(uid, "recebi 200 do meu pai")
    _diga(uid, resposta)
    sync(c, uid, [tx("t1", -200, date.today())])

    status, saque = esperado
    assert [r["status"] for r in links(uid)] == [status]
    assert carteira(uid) == (Decimal("200") if resposta == "dinheiro vivo" else 0) + saque
