"""Q41: conta sem número (o banco não manda `raw.number`). Duas contas do mesmo
banco sem número não se distinguem da mesma conta vista por outra conexão —
então a chave é por conta, e o corte e a janela das outras conexões são
checados pela instituição: ambíguo pergunta, nunca credita nem some por data."""
from datetime import datetime
from decimal import Decimal

import pytest

import db
from conftest import usuario_pagante
from tests._of_cash_helpers import caixa, carteira, conecta, dia, links, sync, tx  # noqa: F401


def _status(uid):
    return [(r["tx_date"], r["status"]) for r in links(uid)]


def test_outra_conta_sem_numero_nao_empresta_o_corte(caixa):
    """Conta A do Nubank conectada em janeiro; conta B, outra do mesmo banco,
    conectada em abril. O saque de março de B é anterior à conexão dela: não
    herda o corte de janeiro de A (pergunta); o de abril credita."""
    uid = usuario_pagante()
    a = conecta(uid, f"item-a-{uid}", desde=datetime(2026, 1, 10, 12), instituicao=212)
    sync(a, uid, [tx("a-1", -5, dia(20, 1), op="PIX", desc="Pix enviado")], numero=None)
    b = conecta(uid, f"item-b-{uid}", desde=datetime(2026, 4, 1, 12))
    sync(b, uid, [tx("b-1", -200, dia(15), pid="B1"), tx("b-2", -70, dia(15, 4), pid="B2")],
         numero=None, conta="acc-2")

    assert _status(uid) == [(dia(15), "perguntar_novo"), (dia(15, 4), "ativo")]
    assert carteira(uid) == Decimal("70")


def test_mesmo_providerid_em_duas_contas_da_mesma_conexao(caixa):
    """Corrente e poupança sem número no mesmo item, providerId repetido entre
    elas: são duas transações — nenhuma some como duplicata da outra."""
    uid = usuario_pagante()
    c = conecta(uid, f"item-{uid}")
    db.save_open_finance_sync(c, [{
        "provider_account_id": f"{conta}-{c}", "name": conta, "type": "BANK", "subtype": "CHECKING_ACCOUNT",
        "currency": "BRL", "balance": Decimal("0"), "raw": {},
        "transactions": [tx(f"{conta}-1", valor, dia(10), pid="P1")],
    } for conta, valor in (("corrente", -200), ("poupanca", -50))])

    assert _status(uid) == [(dia(10), "ativo"), (dia(10), "ativo")]
    assert carteira(uid) == Decimal("250")


@pytest.mark.parametrize("pid", ["P1", "Q1"], ids=["mesmo_providerid", "outro_providerid"])
def test_duas_conexoes_vivas_sem_numero_nao_creditam_em_dobro(caixa, pid):
    """Nubank direto (212) e Nubank Open Finance (612) vivos, os dois sem
    número: o mesmo saque visto pelo segundo pergunta em vez de creditar."""
    uid = usuario_pagante()
    c1 = conecta(uid, f"item-nu-{uid}", instituicao=212)
    sync(c1, uid, [tx("n-1", -200, dia(10), pid="P1"),
                   tx("n-2", -5, dia(20, 4), op="PIX", desc="Pix enviado")], numero=None)
    c2 = conecta(uid, f"item-of-{uid}", instituicao=612)
    sync(c2, uid, [tx("o-1", -200, dia(10), pid=pid)], numero=None)

    assert _status(uid) == [(dia(10), "ativo"), (dia(10), "perguntar_novo")]
    assert carteira(uid) == Decimal("200")


def test_reconectar_conta_sem_numero_nao_credita_em_dobro(caixa):
    """Sem número a chave não sobrevive a reconectar: o que a conexão antiga já
    creditou pergunta (janela dela), o saque feito durante a desconexão também
    (banda entre a 1ª conexão do banco e a nova) — nada credita de novo e nada
    vira histórico por data."""
    uid = usuario_pagante()
    c1 = conecta(uid, f"item-a-{uid}")
    sync(c1, uid, [tx("a-1", -200, dia(10), pid="P1"), tx("a-2", -50, dia(12), pid="P2")], numero=None)
    assert carteira(uid) == Decimal("250")
    assert db.disconnect_open_finance_connection(uid, c1) == 1
    c2 = conecta(uid, f"item-b-{uid}", desde=datetime(2026, 4, 1, 12))
    sync(c2, uid, [tx("b-1", -200, dia(10), pid="P1"), tx("b-3", -70, dia(20), pid="P3")], numero=None)

    assert carteira(uid) == Decimal("250")
    assert [r["status"] for r in links(uid)] == ["ativo", "ativo", "perguntar_novo", "perguntar_novo"]
