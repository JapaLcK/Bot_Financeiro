"""Q41 grupo 8: um usuário não alcança o vínculo do outro, e o mesmo providerId
em dois usuários são dois saques."""
from decimal import Decimal

import pytest

from conftest import usuario_pagante
from db.open_finance_cash import answer_link, list_pending, undo_link
from tests._of_cash_helpers import caixa, carteira, conecta, dia, links, sync, tx  # noqa: F401


def _com_saque(extrato):
    uid = usuario_pagante()
    sync(conecta(uid, f"item-{uid}"), uid, extrato)
    return uid


def test_b_nao_desfaz_nem_responde_o_vinculo_de_a(caixa):
    a = _com_saque([tx("t1", -200, dia(10)), tx("d1", 90, dia(11), op="DEPOSITO", desc="Transfers")])
    b = usuario_pagante()
    saque, deposito = links(a)

    with pytest.raises(LookupError):
        undo_link(b, saque["id"])
    with pytest.raises(LookupError):
        answer_link(b, deposito["id"], "cash")
    assert list_pending(b) == []

    assert carteira(a) == Decimal("200") and carteira(b) == 0
    assert [r["status"] for r in links(a)] == ["ativo", "perguntar_fraco"]
    assert undo_link(a, saque["id"])["changed"], "o dono tem de conseguir desfazer"


def test_mesmo_providerid_em_dois_usuarios(caixa):
    a = _com_saque([tx("t1", -200, dia(10), pid="MESMO")])
    b = _com_saque([tx("t1", -200, dia(10), pid="MESMO")])
    assert carteira(a) == carteira(b) == Decimal("200")
    assert len(links(a)) == len(links(b)) == 1
