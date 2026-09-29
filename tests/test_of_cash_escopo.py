"""Q41: só as contas do saldo consolidado (`BANK_ACCOUNTS_SQL`: BRL, conexão não
pausada nem apagada) criam ou revisam vínculo de saque em espécie. Fora dele o
vínculo que já existe fica como está (pausar não desfaz), e a janela que a
conexão pausada já viu continua contando contra crédito em dobro."""
from datetime import datetime
from decimal import Decimal

import pytest

from conftest import usuario_pagante
from tests._of_cash_helpers import caixa, carteira, conecta, dia, links, q, sync, tx  # noqa: F401


def _status(conn_id, uid, status):
    q("update open_finance_connections set status=%s where id=%s and user_id=%s", (status, conn_id, uid))


def test_conta_em_dolar_nao_credita(caixa):
    """USD 100 não vira R$ 100; a conta BRL ao lado credita (positivo)."""
    uid = usuario_pagante()
    usd = conecta(uid, f"item-usd-{uid}", nome="Nomad")
    sync(usd, uid, [tx("u-1", -100, dia(10))], numero="7777-1", moeda="USD")
    brl = conecta(uid, f"item-brl-{uid}")
    sync(brl, uid, [tx("b-1", -200, dia(10))])

    assert [(r["amount"], r["status"]) for r in links(uid)] == [(Decimal("200.00"), "ativo")]
    assert carteira(uid) == Decimal("200")


@pytest.mark.parametrize("status", ["PAUSED", "DELETED"])
def test_saque_antigo_de_conexao_fora_do_escopo_nao_credita(caixa, monkeypatch, status):
    """Saque visto com o switch desligado numa conexão hoje pausada/apagada: o
    sync de OUTRA conexão não o credita; o saque dela credita (positivo)."""
    uid = usuario_pagante()
    monkeypatch.setenv("OF_CASH_ENABLED", "0")
    nu = conecta(uid, f"item-nu-{uid}")
    sync(nu, uid, [tx("n-1", -200, dia(10))])
    _status(nu, uid, status)
    monkeypatch.setenv("OF_CASH_ENABLED", "1")
    inter = conecta(uid, f"item-inter-{uid}", instituicao=77, nome="Inter")
    sync(inter, uid, [tx("i-1", -70, dia(15))], numero="5555-0")

    assert [(r["amount"], r["status"]) for r in links(uid)] == [(Decimal("70.00"), "ativo")]
    assert carteira(uid) == Decimal("70")


def test_vinculo_de_conexao_pausada_fica_intacto(caixa):
    """Pausar não é o banco apagar: a Carteira não volta, o vínculo não muda, e
    ao despausar o mesmo saque não credita de novo."""
    uid = usuario_pagante()
    nu = conecta(uid, f"item-nu-{uid}")
    sync(nu, uid, [tx("n-1", -200, dia(10))])
    antes = links(uid)
    _status(nu, uid, "PAUSED")
    inter = conecta(uid, f"item-inter-{uid}", instituicao=77, nome="Inter")
    sync(inter, uid, [tx("i-1", -5, dia(15), op="PIX", desc="Pix enviado")], numero="5555-0")

    assert links(uid) == antes and antes[0]["status"] == "ativo" and antes[0]["launch_id"]
    assert carteira(uid) == Decimal("200")
    _status(nu, uid, "UPDATED")
    sync(nu, uid, [tx("n-1", -200, dia(10))])
    assert [r["status"] for r in links(uid)] == ["ativo"] and carteira(uid) == Decimal("200")


def test_janela_da_conexao_pausada_continua_contando(caixa):
    """A conexão pausada já viu 10/03 nesta conta: a nova conexão do mesmo banco
    com providerId trocado pergunta, e com o mesmo providerId não credita."""
    uid = usuario_pagante()
    velha = conecta(uid, f"item-a-{uid}")
    sync(velha, uid, [tx("a-1", -200, dia(10), pid="P1")])
    _status(velha, uid, "PAUSED")
    nova = conecta(uid, f"item-b-{uid}", desde=datetime(2026, 2, 1, 12))
    sync(nova, uid, [tx("b-1", -200, dia(10), pid="P1"), tx("b-2", -200, dia(10), pid="Q1")])

    assert [r["status"] for r in links(uid)] == ["ativo", "perguntar_novo"]
    assert carteira(uid) == Decimal("200")
