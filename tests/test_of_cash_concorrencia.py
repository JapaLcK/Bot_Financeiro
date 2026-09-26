"""Q41 grupo 7: dois reconciliadores ao mesmo tempo (dois syncs do mesmo
usuário) creditam o saque uma vez só."""
import threading
from decimal import Decimal

from conftest import usuario_pagante
from db.connection import get_conn
from db.open_finance_cash import reconcile_cash_transfers
from tests._of_cash_helpers import caixa, carteira, conecta, dia, links, sync, tx  # noqa: F401


def test_dois_reconciliadores_creditam_uma_vez(caixa, monkeypatch):
    uid = usuario_pagante()
    c = conecta(uid, f"item-{uid}")
    monkeypatch.setenv("OF_CASH_ENABLED", "0")  # espelho gravado sem reconciliar
    sync(c, uid, [tx("t1", -200, dia(10))])
    monkeypatch.setenv("OF_CASH_ENABLED", "1")

    barreira, erros = threading.Barrier(2), []

    def roda():
        try:
            with get_conn() as conn, conn.cursor() as cur:
                barreira.wait(timeout=10)
                reconcile_cash_transfers(cur, uid)
                conn.commit()
        except Exception as exc:  # noqa: BLE001 — o teste lê a lista
            erros.append(repr(exc))

    ts = [threading.Thread(target=roda) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=30)

    assert erros == []
    assert carteira(uid) == Decimal("200")
    assert [r["status"] for r in links(uid)] == ["ativo"]
