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
