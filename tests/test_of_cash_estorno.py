"""Q41 grupo 3: o banco apaga a transação (webhook transactions/deleted) ou ela
deixa de ser saque → a Carteira volta, sem mexer no resto."""
import hashlib
import hmac
import json
from decimal import Decimal

from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.open_finance as of_routes
from conftest import usuario_pagante
from tests._of_cash_helpers import caixa, carteira, conecta, dia, links, q, sync, tx  # noqa: F401

SEGREDO = "test-webhook-secret"


def _webhook_apaga(monkeypatch, item, ids):
    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(of_routes, "log_system_event", _noop)
    monkeypatch.setenv("PLUGGY_WEBHOOK_SECRET", SEGREDO)
    corpo = json.dumps({"event": "transactions/deleted", "itemId": item,
                        "transactionIds": ids}).encode()
    assinatura = hmac.new(SEGREDO.encode(), corpo, hashlib.sha256).hexdigest()
    r = TestClient(dashboard.app, raise_server_exceptions=False).post(
        "/open-finance/pluggy/webhook", content=corpo,
        headers={"Content-Type": "application/json", "X-Pluggy-Signature": f"sha256={assinatura}"})
    assert r.status_code == 200, r.text


def _manuais(uid):
    return q("select count(*) as n from launches where user_id=%s and source='manual'", (uid,), True)[0]["n"]


def test_webhook_apagou_o_saque_a_carteira_volta(caixa, monkeypatch):
    uid = usuario_pagante()
    item = f"item-{uid}"
    c = conecta(uid, item)
    sync(c, uid, [tx("t1", -200, dia(10)), tx("t2", -30, dia(11))])
    assert carteira(uid) == Decimal("230")

    _webhook_apaga(monkeypatch, item, ["t1"])

    assert carteira(uid) == Decimal("30")
    assert [r["status"] for r in links(uid)] == ["estornado", "ativo"]
    assert _manuais(uid) == 1


def test_apagar_outra_transacao_nao_mexe(caixa, monkeypatch):
    """Positivo: tx sem vínculo apagada pelo banco não toca a Carteira."""
    uid = usuario_pagante()
    item = f"item-{uid}"
    c = conecta(uid, item)
    sync(c, uid, [tx("t1", -200, dia(10)),
                  tx("m1", -45, dia(10), op="CARTAO", desc="Mercado", category="Groceries")])
    _webhook_apaga(monkeypatch, item, ["m1"])
    assert carteira(uid) == Decimal("200")
    assert [r["status"] for r in links(uid)] == ["ativo"]


def test_saque_reclassificado_pelo_banco_estorna(caixa):
    uid = usuario_pagante()
    c = conecta(uid, f"item-{uid}")
    sync(c, uid, [tx("t1", -200, dia(10))])
    sync(c, uid, [tx("t1", -200, dia(10), op="CARTAO", desc="Compra", category="Shopping")])
    assert carteira(uid) == 0
    assert [r["status"] for r in links(uid)] == ["estornado"]
    assert _manuais(uid) == 0
