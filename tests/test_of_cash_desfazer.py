"""Q41 grupo 4: desfazer (pela função da rota e por "apagar #N" na conversa) é
permanente — o sync seguinte não recria. Saque novo continua creditando."""
from decimal import Decimal

import db
from conftest import usuario_pagante
from db.open_finance_cash import undo_link
from tests._of_cash_helpers import (  # noqa: F401  (fixture)
    caixa, carteira, conecta, dia, launches_visiveis, links, sync, tx,
)
from tests.test_pending_rollback import _diga

EXTRATO = [tx("t1", -200, dia(10))]


def test_desfazer_devolve_a_carteira_e_nao_volta(caixa):
    uid = usuario_pagante()
    c = conecta(uid, f"item-{uid}")
    sync(c, uid, EXTRATO)
    (link,) = links(uid)

    assert undo_link(uid, link["id"]) == {"ok": True, "changed": True}
    assert undo_link(uid, link["id"])["changed"] is False
    sync(c, uid, EXTRATO)

    assert carteira(uid) == 0
    assert [(r["status"], r["launch_id"]) for r in links(uid)] == [("desfeito", None)]
    assert launches_visiveis(uid) == [], "desfazer não pode transformar a saída do banco em gasto"


def test_apagar_pela_conversa_e_desfazer_permanente(caixa):
    uid = usuario_pagante()
    c = conecta(uid, f"item-{uid}")
    sync(c, uid, EXTRATO)
    (link,) = links(uid)
    seq = db.get_launch_user_seq(uid, link["launch_id"])

    assert "confirma" in _diga(uid, f"apagar #{seq}").lower()
    assert "apagad" in _diga(uid, "sim").lower()
    assert carteira(uid) == 0
    sync(c, uid, EXTRATO)

    assert carteira(uid) == 0, "o sync recriou o saque que o usuário apagou"
    assert [(r["status"], r["launch_id"]) for r in links(uid)] == [("ativo", None)]


def test_saque_novo_depois_do_desfazer_credita(caixa):
    """Positivo: o desfazer é daquele saque, não do recurso."""
    uid = usuario_pagante()
    c = conecta(uid, f"item-{uid}")
    sync(c, uid, EXTRATO)
    undo_link(uid, links(uid)[0]["id"])
    sync(c, uid, EXTRATO + [tx("t2", -80, dia(15))])
    assert carteira(uid) == Decimal("80")
