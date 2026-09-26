"""Apagar em lote pelo WhatsApp: do id mais novo para o mais velho, sem repetidos,
e a recusa de domínio com a frase do apagar singular (não "⚠️ Falha").

Tudo pela conversa (`handle_incoming`), com estado real no banco.
"""
from decimal import Decimal

import db
from conftest import usuario_pagante
from tests.test_investimento_juro_e_desfazer import _carteira_com_cdb, _estado, _resgate
from tests.test_pending_rollback import _diga


def _mercado(uid):
    assert "mercado" in _diga(uid, "gastei 50 no mercado").lower()
    lid = int(db.list_launches(uid, limit=1)[0]["id"])
    return lid, db.get_launch_user_seq(uid, lid)


def _apagar(uid, *seqs):
    pedido = "apagar " + " e ".join(f"#{s}" for s in seqs)
    assert "confirma" in _diga(uid, pedido).lower()
    return _diga(uid, "sim")


def test_dois_resgates_na_ordem_digitada_apagam_os_dois():
    uid = usuario_pagante()
    _carteira_com_cdb(uid)
    _mercado(uid)
    antes = _estado(uid)
    r1, r2 = _resgate(uid, "cdb", 100), _resgate(uid, "cdb", 100)
    s1, s2 = db.get_launch_user_seq(uid, r1), db.get_launch_user_seq(uid, r2)

    resp = _apagar(uid, s1, s2)

    assert "Apagados" in resp and resp.index(f"*#{s1}*") < resp.index(f"*#{s2}*"), resp
    assert "não é o mais recente" not in resp and "Falha" not in resp, resp
    assert _estado(uid) == antes


def test_recusa_de_dominio_sai_com_a_frase_do_singular_e_o_resto_apaga():
    uid = usuario_pagante()
    _carteira_com_cdb(uid)
    r1 = _resgate(uid, "cdb", 100)
    _resgate(uid, "cdb", 100)
    m, sm = _mercado(uid)
    s1 = db.get_launch_user_seq(uid, r1)
    conta, lotes, invs, _ = _estado(uid)

    resp = _apagar(uid, s1, sm)

    assert f"✅ Apagados: *#{sm}*" in resp, resp
    linha = next(li for li in resp.splitlines() if f"#{s1}" in li)
    assert "não é o mais recente do investimento" in linha, resp
    assert "Falha" not in resp, resp
    ids = {int(r["id"]) for r in db.list_launches(uid, limit=50)}
    assert r1 in ids and m not in ids
    assert _estado(uid)[:3] == (conta + 50, lotes, invs)


def test_id_repetido_apaga_uma_vez_sem_falha():
    uid = usuario_pagante()
    db.add_launch_and_update_balance(uid, "receita", 200, None, "seed")
    _, sm = _mercado(uid)

    resp = _apagar(uid, sm, sm)

    assert "Apagados" in resp and "Falha" not in resp, resp
    assert _estado(uid)[0] == Decimal("200")
