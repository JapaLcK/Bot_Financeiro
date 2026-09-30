"""Q41 PR B: as rotas do painel do dinheiro vivo (frontend/routes/open_finance_cash.py)
— sessão, CSRF, isolamento, teto, o ciclo pela rota e o item de `alerts` do
/data. Postgres real e o sync de produção (tests/_of_cash_helpers.py)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
from conftest import usuario_pagante
from db.budgets import upsert_budget
from db.open_finance_cash_answers import cash_transfer_summary
from frontend.routes import shared
from tests._of_cash_helpers import caixa, carteira, conecta, dia, links, q, sync, tx  # noqa: F401

H = {dashboard.CSRF_HEADER_NAME: "cash-test"}
ACOES = ("undo", "seen", "same", "different", "already", "credit", "cash", "not_cash")
SAQUE = tx("t1", -200, dia(10))
DEPOSITO = tx("d1", 300, dia(11), op="DEPOSITO", desc="Transfers")
PIX_SAQUE = tx("p1", -100, dia(12), op="PIX", desc="Pix Saque Loja X", category="Transfer - PIX")
COMPRA = {"op": "CARTAO", "desc": "Compra", "category": "Shopping"}


def _sem_pid(t):
    """Sem providerId a chave não sobrevive a reconectar: vira `perguntar_novo`."""
    return {**t, "raw": {k: v for k, v in t["raw"].items() if k != "providerId"}}


@pytest.fixture(autouse=True)
def _teto_zerado():
    """O balde é por IP e o TestClient é um IP só: sem isto, os POSTs dos
    testes vizinhos no mesmo processo somariam no teto de 30/min."""
    shared.limiter.reset()


def _cliente(uid):
    client = TestClient(dashboard.app)
    client.cookies.set(dashboard.AUTH_COOKIE_NAME, dashboard._make_jwt(uid, "cash@test.com"))
    client.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, dashboard.make_dashboard_token(uid, hours=1))
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, "cash-test")
    return client


def _post(client, uid, link_id, action):
    return client.post(f"/open-finance/{uid}/cash-transfers/{link_id}/{action}", headers=H)


def _cenario(uid, *extrato, manual=None):
    """`manual`: (tipo, valor) anotado à mão no dia do extrato, antes do sync."""
    if manual:
        db.add_launch_and_update_balance(uid, manual[0], manual[1], "meu pai", None,
                                         criado_em=datetime(2026, 3, 10, 12))
    c = conecta(uid, f"item-{uid}")
    sync(c, uid, list(extrato))
    return c


def _link(uid, status):
    (link,) = [r for r in links(uid) if r["status"] == status]
    return link


def test_isolamento_b_nao_ve_nem_responde_o_vinculo_de_a(caixa):
    a, b = usuario_pagante(), usuario_pagante()
    _cenario(a, SAQUE, DEPOSITO)
    antes = (carteira(a), links(a))
    cb = _cliente(b)

    r = cb.get(f"/open-finance/{b}/cash-transfers")
    assert r.status_code == 200 and r.json()["items"] == [], r.text
    for link in antes[1]:
        for acao in ACOES:
            assert _post(cb, b, link["id"], acao).status_code == 404, (acao, link["status"])
    assert cb.get(f"/open-finance/{a}/cash-transfers").status_code in (401, 403)
    assert _post(cb, a, antes[1][0]["id"], "undo").status_code in (401, 403)
    assert (carteira(a), links(a)) == antes, "B mexeu no estado de A"

    # positivo: o dono vê os dois
    items = _cliente(a).get(f"/open-finance/{a}/cash-transfers").json()["items"]
    assert {(i["kind"], i["status"]) for i in items} == {("saque", "ativo"), ("deposito", "perguntar_fraco")}


def test_sessao_csrf_e_acao_invalida(caixa):
    uid = usuario_pagante()
    _cenario(uid, SAQUE)
    lid = links(uid)[0]["id"]
    base = f"/open-finance/{uid}/cash-transfers"

    assert TestClient(dashboard.app).get(base).status_code in (401, 403)
    assert TestClient(dashboard.app).post(f"{base}/{lid}/seen", headers=H).status_code in (401, 403)
    client = _cliente(uid)
    assert client.post(f"{base}/{lid}/seen").status_code == 403, "sem CSRF passou"
    assert _post(client, uid, lid, "apagar").status_code == 422
    assert links(uid)[0]["seen_at"] is None
    assert _post(client, uid, lid, "seen").status_code == 200, "positivo: com sessão e CSRF passa"


# (extrato, manual, status da pergunta, ação, Carteira depois, status depois)
CICLO = [
    ([SAQUE], None, "ativo", "undo", 0, "desfeito"),
    ([SAQUE], None, "ativo", "seen", 200, "ativo"),
    ([DEPOSITO], None, "perguntar_fraco", "cash", -300, "ativo"),
    ([DEPOSITO], None, "perguntar_fraco", "not_cash", 0, "nao_dinheiro"),
    ([PIX_SAQUE], None, "perguntar_fraco", "cash", 100, "ativo"),
    ([_sem_pid(SAQUE)], None, "perguntar_novo", "credit", 200, "ativo"),
    ([_sem_pid(SAQUE)], None, "perguntar_novo", "already", 0, "desfeito"),
    ([SAQUE], ("receita", 200), "perguntar_manual", "different", 400, "ativo"),
    ([SAQUE], ("receita", 200), "perguntar_manual", "same", 200, "ativo"),
]


@pytest.mark.parametrize("extrato, manual, status, acao, saldo, depois", CICLO,
                         ids=[f"{c[2]}-{c[3]}" for c in CICLO])
def test_ciclo_pela_rota(caixa, extrato, manual, status, acao, saldo, depois):
    """Cada ação mexe (ou não) na Carteira uma vez só; repetir é 200 sem dobro.
    Responder também vale como ver: o crédito da resposta não vira aviso (1b)."""
    uid = usuario_pagante()
    _cenario(uid, *extrato, manual=manual)
    link = _link(uid, status)
    client = _cliente(uid)

    r = _post(client, uid, link["id"], acao)
    assert r.status_code == 200 and r.json() == {"ok": True, "changed": True}, r.text
    r = _post(client, uid, link["id"], acao)
    assert r.status_code == 200 and r.json()["changed"] is False, r.text

    assert carteira(uid) == Decimal(saldo)
    assert links(uid)[0]["status"] == depois
    assert cash_transfer_summary(uid) == {"pending_count": 0, "unseen_count": 0}
    assert client.get(f"/open-finance/{uid}/cash-transfers").json()["items"] == []


# Decisão do dono: "não era dinheiro vivo" em toda pergunta de depósito e Pix
# Saque; o saque (operationType=SAQUE) é sempre dinheiro.
NAO_ERA = [
    ([_sem_pid(DEPOSITO)], None, "perguntar_novo", True),
    ([_sem_pid(PIX_SAQUE)], None, "perguntar_novo", True),
    ([DEPOSITO], ("despesa", 300), "perguntar_manual", True),
    ([_sem_pid(SAQUE)], None, "perguntar_novo", False),
    ([SAQUE], ("receita", 200), "perguntar_manual", False),
]


@pytest.mark.parametrize("extrato, manual, status, aceita", NAO_ERA,
                         ids=["deposito-novo", "pix-novo", "deposito-manual", "saque-novo", "saque-manual"])
def test_nao_era_dinheiro_nas_outras_perguntas(caixa, extrato, manual, status, aceita):
    uid = usuario_pagante()
    _cenario(uid, *extrato, manual=manual)
    antes = carteira(uid)
    r = _post(_cliente(uid), uid, _link(uid, status)["id"], "not_cash")
    assert r.status_code == 200 and r.json()["changed"] is aceita, r.text
    assert links(uid)[0]["status"] == ("nao_dinheiro" if aceita else status)
    assert carteira(uid) == antes


def test_estornado_e_reaberto_volta_a_avisar(caixa):
    """1c: o Ok vale para AQUELE crédito. O banco estorna e reabre: é crédito
    novo na Carteira, e o aviso volta."""
    uid = usuario_pagante()
    c = _cenario(uid, SAQUE)
    client = _cliente(uid)
    assert _post(client, uid, links(uid)[0]["id"], "seen").json()["changed"]
    assert cash_transfer_summary(uid)["unseen_count"] == 0

    sync(c, uid, [tx("t1", -200, dia(10), **COMPRA)])
    assert links(uid)[0]["status"] == "estornado"
    sync(c, uid, [SAQUE])

    assert (links(uid)[0]["status"], carteira(uid)) == ("ativo", Decimal("200"))
    assert cash_transfer_summary(uid) == {"pending_count": 0, "unseen_count": 1}
    assert [i["status"] for i in client.get(f"/open-finance/{uid}/cash-transfers").json()["items"]] == ["ativo"]


def test_manual_que_mudou_da_409_e_nao_mexe_na_carteira(caixa):
    uid = usuario_pagante()
    _cenario(uid, SAQUE, manual=("receita", 200))
    link = _link(uid, "perguntar_manual")
    manual = link["manual_launch_id"]
    assert db.update_launch_fields(uid, manual, criado_em=datetime(2026, 3, 10, 12) - timedelta(days=10))
    antes = carteira(uid)

    r = _post(_cliente(uid), uid, link["id"], "same")
    assert r.status_code == 409 and "São diferentes" in r.json()["detail"], r.text
    assert carteira(uid) == antes
    assert links(uid)[0]["status"] == "perguntar_manual"


def test_lista_traz_banco_e_o_manual(caixa):
    uid = usuario_pagante()
    _cenario(uid, SAQUE, manual=("receita", 200))
    (item,) = _cliente(uid).get(f"/open-finance/{uid}/cash-transfers").json()["items"]
    assert {k: item[k] for k in ("kind", "status", "amount", "tx_date", "institution",
                                 "manual_alvo", "manual_valor", "manual_date")} == {
        "kind": "saque", "status": "perguntar_manual", "amount": 200.0, "tx_date": "2026-03-10",
        "institution": "Nubank", "manual_alvo": "meu pai", "manual_valor": 200.0, "manual_date": "2026-03-10"}


def _um_registro_do_teto(monkeypatch, rota):
    """Mesma cautela de tests/test_reconciliacao_rotas.py: se algum teste
    recarregar o módulo, o slowapi estende a lista de tetos da rota."""
    nome = f"frontend.routes.open_finance_cash.{rota}"
    limites = shared.limiter._route_limits[nome]
    assert len({(str(l.limit), l.scope) for l in limites}) == 1, limites
    monkeypatch.setitem(shared.limiter._route_limits, nome, limites[:1])
    shared.limiter.reset()


def test_teto_nao_se_contorna_variando_a_url(monkeypatch):
    uid = usuario_pagante()
    client = _cliente(uid)
    _um_registro_do_teto(monkeypatch, "cash_transfer_action_route")
    codes = [_post(client, uid, 900000000 + i, ACOES[i % len(ACOES)]).status_code for i in range(31)]
    assert codes == [404] * 30 + [429], codes

    _um_registro_do_teto(monkeypatch, "cash_transfers_route")
    codes = [client.get(f"/open-finance/{uid + i}/cash-transfers").status_code for i in range(61)]
    assert 429 not in codes[:60] and codes[60] == 429, codes


def test_data_traz_o_contador_em_primeiro_nos_alertas(caixa):
    """Um item só em `alerts`, na frente do alerta de orçamento, lido pelo /app
    e pelo /home. Some quando não há nada para conferir."""
    uid = usuario_pagante()
    _cenario(uid, SAQUE, DEPOSITO)
    upsert_budget(uid, "mercado", 100.0)
    db.add_launch_and_update_balance(uid, "despesa", 90, "teste", "teste", categoria="mercado")

    alerts = asyncio.run(dashboard.get_financial_data(uid))["alerts"]
    assert alerts[0] == {"type": "cash_transfers", "count": 2}, alerts
    assert [a["type"] for a in alerts[1:]] == ["budget_warning"]

    client = _cliente(uid)
    _post(client, uid, _link(uid, "ativo")["id"], "seen")
    _post(client, uid, _link(uid, "perguntar_fraco")["id"], "not_cash")
    alerts = asyncio.run(dashboard.get_financial_data(uid))["alerts"]
    assert [a["type"] for a in alerts] == ["budget_warning"]
