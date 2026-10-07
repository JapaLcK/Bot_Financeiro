"""`GET /api/v2/investido` e `/api/app/investido` pelo monólito real: sessão, gate, banco real.

A regra está em `tests/test_investido_regra.py`; aqui, o fio: formato do contrato
(dinheiro em texto, `null` ≠ 0), o gate `investments`, o mesmo corpo no namespace do app
e o isolamento entre usuários.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import sessao_de
from api.v2 import investido as rota
from api.v2.investido import Investido
from conftest import usuario_pagante
from test_api_app_sessao import cliente, completo
from tests._patrimonio_helpers import conexao, investimento_manual, posicao

ROTA = "/api/v2/investido"


@pytest.fixture
def uid(monkeypatch):
    monkeypatch.setenv("DASHBOARD_V2_BETA_EMAILS", "")
    u = completo(usuario_pagante())
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", str(u))
    return u


def get(quem):
    c = TestClient(dashboard.app)
    c.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, sessao_de(quem)["dashboard"])
    return c.get(ROTA)


def ok(quem) -> dict:
    r = get(quem)
    assert r.status_code == 200, r.text
    assert Investido.model_validate(r.json()).model_dump(mode="json") == r.json()
    return r.json()


def test_formato_texto_decimal_e_partes(uid):
    c = conexao(uid, f"item-{uid}", banco="Nubank")
    posicao(c, "inv-1", "40000", subtipo="CDB")
    posicao(c, "inv-2", "17123.45", tipo="EQUITY", subtipo="STOCK")
    posicao(c, "inv-3", None, tipo="MUTUAL_FUND")
    investimento_manual(uid, "SEGREDO-MANUAL", "70")
    corpo = ok(uid)
    assert corpo == {
        "total": "57123.45",
        "por_tipo": [
            {"tipo": "renda_fixa", "rotulo": "Renda fixa", "valor": "40000.00", "motivos": []},
            {"tipo": "acoes", "rotulo": "Ações", "valor": "17123.45", "motivos": []},
            {"tipo": "fundos", "rotulo": "Fundos de investimento", "valor": None, "motivos": ["saldo_ausente"]},
        ],
        "por_banco": [{"banco": "Nubank", "valor": "57123.45", "motivos": ["saldo_ausente"]}],
        "motivos": ["saldo_ausente"],
    }
    assert "inv-1" not in str(corpo) and "SEGREDO" not in str(corpo)


def test_sem_banco_e_null_nao_zero(uid):
    investimento_manual(uid, "CDB manual", "70")
    assert ok(uid) == {"total": None, "por_tipo": [], "por_banco": [], "motivos": ["sem_banco_conectado"]}


def test_app_com_bearer_devolve_o_mesmo(uid):
    posicao(conexao(uid, f"item-{uid}", banco="XP"), "inv-1", "10.5")
    r = cliente(uid).get("/api/app/investido")
    assert r.status_code == 200, r.text
    assert r.json() == ok(uid) and r.json()["total"] == "10.50"


def test_sem_o_recurso_e_403_antes_de_ler(uid, monkeypatch):
    monkeypatch.setattr(rota, "plan_gate_ok", lambda u, f: f != "investments")
    monkeypatch.setattr(rota.investido, "ler", lambda u: pytest.fail("leu sem o recurso"))
    r = get(uid)
    assert r.status_code == 403 and r.json()["error"]["code"] == "pro_required"


def test_a_nao_ve_b(uid, monkeypatch):
    b = completo(usuario_pagante())
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", f"{uid},{b}")
    posicao(conexao(uid, f"item-{uid}", banco="Nubank"), "inv-1", "100")
    posicao(conexao(b, f"item-{b}", banco="SEGREDO-B"), "inv-1", "9999")
    assert ok(uid)["total"] == "100.00" and "SEGREDO-B" not in get(uid).text
    assert ok(b)["total"] == "9999.00"
