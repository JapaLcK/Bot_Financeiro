"""Q41 grupo 6, pela conversa real: o usuário anotou "recebi 200" e "gastei 50
no mercado"; o banco mostra o saque de 200 → pergunta; "é o mesmo" não credita
de novo."""
from datetime import date, datetime, timedelta
from decimal import Decimal

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
