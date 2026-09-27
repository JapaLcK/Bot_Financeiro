"""Q41: manual INTERNO da Carteira de mesmo sinal, valor e ±3 dias (ex.: o
"recebi 200" que o usuário marcou como transferência interna) não vira par do
saque, mas o saque deixa de se provar novo: pergunta "já anotei?" em vez de
creditar sozinho (regra do dono: o que não se prova novo vira pergunta)."""
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

import db
from conftest import usuario_pagante
from db.open_finance_cash_answers import answer_link
from tests._of_cash_helpers import caixa, carteira, conecta, links, q, sync, tx  # noqa: F401
from tests.test_pending_rollback import _diga


def _interno_e_banco(uid, valor=200, dias_antes=0):
    """"recebi <valor>" marcado como transferência interna, e o saque de 200 no banco."""
    assert "Receita registrada" in _diga(uid, f"recebi {valor} do meu pai")
    (lid,) = [r["id"] for r in q("select id from launches where user_id=%s", (uid,), True)]
    assert db.update_launch_category(uid, lid, "transferencia_interna")
    if dias_antes:
        assert db.update_launch_fields(uid, lid, criado_em=datetime.now() - timedelta(days=dias_antes))
    c = conecta(uid, f"item-{uid}", desde=datetime.now() - timedelta(days=10))
    sync(c, uid, [tx("t1", -200, date.today())])
    return c


def test_saque_com_manual_interno_do_mesmo_valor_pergunta(caixa):
    uid = usuario_pagante()
    c = _interno_e_banco(uid)
    sync(c, uid, [tx("t1", -200, date.today())])

    assert [(r["status"], r["manual_launch_id"]) for r in links(uid)] == [("perguntar_novo", None)]
    assert carteira(uid) == Decimal("200"), "creditou por cima do manual interno (dobro)"


@pytest.mark.parametrize("valor, dias_antes", [(300, 0), (200, 5)], ids=["outro_valor", "fora_da_janela"])
def test_manual_interno_que_nao_e_o_saque_nao_bloqueia(caixa, valor, dias_antes):
    """Positivo: interno de outro valor, ou fora dos ±3 dias, não segura o crédito."""
    uid = usuario_pagante()
    _interno_e_banco(uid, valor, dias_antes)
    assert [r["status"] for r in links(uid)] == ["ativo"]
    assert carteira(uid) == Decimal(valor) + 200


def test_o_par_de_outro_saque_nao_bloqueia(caixa):
    """Positivo: o lançamento automático de um saque (interno, mesmo valor) é
    par de outro vínculo — dois saques de 200 no mesmo dia creditam os dois."""
    uid = usuario_pagante()
    c = conecta(uid, f"item-{uid}", desde=datetime.now() - timedelta(days=10))
    sync(c, uid, [tx("t1", -200, date.today()), tx("t2", -200, date.today())])
    sync(c, uid, [tx("t1", -200, date.today()), tx("t2", -200, date.today())])
    assert [r["status"] for r in links(uid)] == ["ativo", "ativo"]
    assert carteira(uid) == Decimal("400")


@pytest.mark.parametrize("resposta, status, saldo", [("already", "desfeito", 200), ("credit", "ativo", 400)])
def test_resposta_a_pergunta(caixa, resposta, status, saldo):
    """"Já anotei" → desfeito, sem crédito; "credita" → credita uma vez, e o sync
    seguinte não credita de novo."""
    uid = usuario_pagante()
    c = _interno_e_banco(uid)
    assert answer_link(uid, links(uid)[0]["id"], resposta)["changed"]
    for _ in range(2):
        sync(c, uid, [tx("t1", -200, date.today())])

    assert [r["status"] for r in links(uid)] == [status]
    assert carteira(uid) == Decimal(saldo)
