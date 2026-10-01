"""#712 — cada porta de edição de categoria marca a linha, e o sync do Open
Finance seguinte (o de produção, Postgres real) não a desfaz.

O banco corrige o valor junto com a categoria: sem isso o UPDATE do sync nem
roda e o teste não mede o CASE.

Controles negativos (CLAUDE.md §3): sem o CASE do sync, todas caem; sem a marca
em `_SET_CATEGORIA` (db/accounts.py), caem as portas de lançamento; sem a marca
em db/cards.py, as de cartão. O positivo (não editada segue o banco) mora em
`test_of_categoria_editada.py` (A3, B3).
"""
from __future__ import annotations

import pytest

import db
from adapters.whatsapp import wa_runtime
from conftest import usuario_pagante
from core.services.ai_chat.tools.categories import _recategorize_launch_execute
from tests._of_cash_helpers import sync
from tests.test_category_launches_query import _cliente_logado
from tests.test_category_normalization import _botao, _wa
from tests.test_of_categoria_editada import _ct, _parcela
from tests.test_of_categoria_pt import _banco, _cartao, _launch, _tx


def _patch(uid, url):
    client, headers = _cliente_logado(uid)
    resp = client.patch(url, json={"categoria": "lazer"}, headers=headers)
    assert resp.status_code == 200, resp.text


def _via_whatsapp(uid, lid, monkeypatch):
    respostas = _wa(monkeypatch, uid)
    wa_runtime.process_message(_botao(f"{wa_runtime.WA_RECAT_PICK_PREFIX}{lid}:lazer", uid))
    assert "atualizada" in respostas[-1], respostas


PORTAS_LANCAMENTO = {
    "patch_launches": lambda uid, lid, mp: _patch(uid, f"/launches/{uid}/{lid}"),
    "whatsapp": _via_whatsapp,
    "ia": lambda uid, lid, mp: _recategorize_launch_execute(
        uid, {"launch_id": lid, "new_category": "lazer"}),
    "lote": lambda uid, lid, mp: db.update_launch_categories_bulk(uid, [(lid, "lazer")]),
}


@pytest.mark.parametrize("porta", PORTAS_LANCAMENTO)
def test_c_lancamento_editado_por_cada_porta_sobrevive_ao_sync(porta, monkeypatch):
    uid = usuario_pagante()  # id pequeno: o WhatsApp troca id grande por hash
    cid = _banco(uid, [_tx("a", -10, "Shopping")])
    lid = _launch(uid, "a")["id"]

    PORTAS_LANCAMENTO[porta](uid, lid, monkeypatch)
    assert _launch(uid, "a")["categoria"] == "lazer"
    sync(cid, uid, [_tx("a", -12, "Groceries")])  # valor junto: senão o UPDATE nem roda

    assert _launch(uid, "a")["categoria"] == "lazer"


def test_c_patch_credit_transactions_sobrevive_ao_sync():
    uid = usuario_pagante()
    cid = _cartao(uid, [_tx("c1", -50, "Shopping")])

    _patch(uid, f"/credit-transactions/{uid}/{_ct(uid, 'c1')['id']}")
    _cartao(uid, [_tx("c1", -60, "Groceries")], cid)

    assert _ct(uid, "c1")["categoria"] == "lazer"


def test_c_patch_installments_sobrevive_ao_sync():
    uid = usuario_pagante()
    cid = _cartao(uid, [_parcela("p1", 1, 3), _parcela("p2", 2, 4)])

    _patch(uid, f"/installments/{uid}/{_ct(uid, 'p1')['group_id']}")
    _cartao(uid, [_parcela("p1", 1, 3, "Groceries", -60), _parcela("p2", 2, 4, "Groceries", -60)], cid)

    assert [_ct(uid, i)["categoria"] for i in ("p1", "p2")] == ["lazer", "lazer"]
