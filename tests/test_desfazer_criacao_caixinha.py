"""Desfazer "criar caixinha" não apaga caixinha com saldo ou com histórico (#609).

Antes o `delete from pockets` rodava sem guarda: levava saldo e lotes junto.
"""
import re
from decimal import Decimal

import pytest

import db
from db.accounts import MENSAGEM_CAIXINHA_COM_MOVIMENTO


def _estado(uid):
    """Tudo o que a recusa não pode mexer: conta, caixinhas, lotes, launches."""
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select balance from accounts where user_id=%s", (uid,))
        conta = cur.fetchone()["balance"]
        cur.execute("select id, name, balance from pockets where user_id=%s order by id", (uid,))
        caixinhas = [tuple(r.values()) for r in cur.fetchall()]
        cur.execute("select id, balance, principal_remaining, status from pocket_lots "
                    "where user_id=%s order by id", (uid,))
        lotes = [tuple(r.values()) for r in cur.fetchall()]
        cur.execute("select id from launches where user_id=%s order by id", (uid,))
        return conta, caixinhas, lotes, [r["id"] for r in cur.fetchall()]


def _recusa(uid, launch_id):
    antes = _estado(uid)
    with pytest.raises(db.PocketHasMovement) as exc:
        db.delete_launch_and_rollback(uid, launch_id)
    assert exc.value.motivo == "caixinha_com_movimento"
    assert _estado(uid) == antes


def test_caixinha_com_saldo_recusa_e_nao_mexe_em_nada(user_id):
    db.add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    lid, pid, _ = db.create_pocket(user_id, "viagem")
    db.pocket_deposit_from_account(user_id, "viagem", 300)

    _recusa(user_id, lid)
    conta, caixinhas, lotes, launches = _estado(user_id)
    assert conta == Decimal("700")
    assert caixinhas == [(pid, "viagem", Decimal("300"))]
    assert len(lotes) == 1 and lid in launches


def test_caixinha_zerada_que_ja_teve_movimento_recusa(user_id):
    db.add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    lid, pid, _ = db.create_pocket(user_id, "viagem")
    db.pocket_deposit_from_account(user_id, "viagem", 100)
    db.pocket_withdraw_to_account(user_id, "viagem", withdraw_all=True)
    assert _estado(user_id)[1][0][2] == 0

    _recusa(user_id, lid)
    assert _estado(user_id)[1][0][0] == pid


def test_caixinha_com_saldo_sem_launch_de_movimento_recusa(user_id):
    """Saldo sem launch depois da criação (meta vinculada a caixinha do Open
    Finance, que o sync atualiza sem lançamento): só a checagem de saldo pega."""
    lid, pid, _ = db.create_pocket(user_id, "viagem")
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("update pockets set balance=300 where id=%s and user_id=%s", (pid, user_id))
        conn.commit()

    _recusa(user_id, lid)


def test_caixinha_sem_movimento_desfaz(user_id):
    """Controle positivo: criar e desfazer na hora continua apagando."""
    lid, _, _ = db.create_pocket(user_id, "viagem")
    db.delete_launch_and_rollback(user_id, lid)
    _, caixinhas, _, launches = _estado(user_id)
    assert caixinhas == [] and lid not in launches


def test_conversa_apagar_criacao_de_caixinha_com_saldo_recusa():
    from conftest import usuario_pagante
    from tests.test_pending_rollback import _diga

    uid = usuario_pagante()
    db.add_launch_and_update_balance(uid, "receita", 1000, None, "seed")
    resp = _diga(uid, "criar caixinha viagem")
    n = re.search(r"#(\d+)", resp)
    assert n, resp
    assert "viagem" in _diga(uid, "coloquei 300 na caixinha viagem").lower()
    assert "mercado" in _diga(uid, "gastei 50 no mercado").lower()

    assert "sim" in _diga(uid, f"apagar #{n.group(1)}").lower()
    resp = _diga(uid, "sim")
    assert f"#{n.group(1)}" in resp and MENSAGEM_CAIXINHA_COM_MOVIMENTO in resp, resp
    assert [(c[1], c[2]) for c in _estado(uid)[1]] == [("viagem", Decimal("300"))]


def test_dashboard_apagar_criacao_de_caixinha_com_saldo_responde_400_com_a_frase(user_id):
    """Porta HTTP: a subclasse tem frase própria, não a genérica da mãe."""
    from tests.test_delete_endpoints_nao_vazam import _client, _headers

    db.add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    lid, _, _ = db.create_pocket(user_id, "viagem")
    db.pocket_deposit_from_account(user_id, "viagem", 300)
    resp = _client(user_id).delete(f"/launches/{user_id}/{lid}", headers=_headers())
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == MENSAGEM_CAIXINHA_COM_MOVIMENTO, resp.text
    assert [(c[1], c[2]) for c in _estado(user_id)[1]] == [("viagem", Decimal("300"))]
