"""`GET /api/v2/comparacao` (e `/api/app/comparacao`) pelo monólito real: sessão, gate, banco real.

A regra (janela, cartão pela fatura, bordas de calendário) é do serviço e está em
`tests/test_comparacao_periodos*.py`; aqui, o fio: o mesmo número que o chat, o gate ANTES de
qualquer leitura, o downgrade na sessão, dois usuários e as bordas que o endpoint adiciona.
Relógio congelado em 12/10/2026 (o `semeia` do PR A). Planos v2 ligados, como em produção.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import sessao_de
from api.v2 import comparacao as rota
from api.v2 import resumo_mes as rota_mes
from api.v2.comparacao import Comparacao
from conftest import promote_to_pro, usuario_pagante
from core.services import comparacao_periodos as cp
from core.services.ai_chat.tools import launches
from core.services.ai_chat.tools.launches import _compare_periods
from db import resumo_mes
from test_api_app_sessao import completo
from test_comparacao_periodos import AGORA, semeia

URL = "/api/v2/comparacao"
LINHAS = ("despesa", "receita", "resultado")
CAMPOS = ("total_atual", "total_anterior", "total_delta", "pct")


@pytest.fixture(autouse=True)
def _mundo(monkeypatch):
    monkeypatch.setenv("PLANS_V2_ENABLED", "1")
    monkeypatch.setenv("DASHBOARD_V2_BETA_EMAILS", "")
    monkeypatch.setattr(rota_mes, "now_tz", lambda: AGORA)           # o relógio do endpoint (`mes_pedido`)
    monkeypatch.setattr(cp, "now_tz", lambda: AGORA)                 # o da tool do chat
    monkeypatch.setattr(launches, "today_tz", lambda: AGORA.date())


def libera(monkeypatch, *uids):
    """Depois de semear: com a chave ligada, a trava Q36 recusa as escritas manuais da semente."""
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", ",".join(map(str, uids)))


def pede(quem, url=URL, bearer=False, **params):
    c = TestClient(dashboard.app)
    s = sessao_de(quem)
    if bearer:
        return c.get(url, params=params, headers={"Authorization": f"Bearer {s['access']}"})
    c.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, s["dashboard"])
    return c.get(url, params=params)


def ok(quem, **params) -> dict:
    r = pede(quem, **params)
    assert r.status_code == 200, r.text
    assert Comparacao.model_validate(r.json()).model_dump(mode="json") == r.json()
    return r.json()


def _dec(v):
    return None if v is None else D(str(v))


def _proibe_leitura(monkeypatch) -> list:
    """Toda leitura financeira do caminho vira falha registrada (o `pytest.fail` dentro da rota
    pode ser engolido pelo envelope de erro; a lista não)."""
    chamadas: list = []

    def boom(*a, **k):
        chamadas.append(a)
        pytest.fail("leu dado antes do gate")
    for alvo, nome in ((rota, "comparar_mes"), (cp, "_comparar"), (resumo_mes, "totais"), (resumo_mes, "categorias")):
        monkeypatch.setattr(alvo, nome, boom)
    return chamadas


def _sem_payload(r) -> None:
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "pro_required"
    assert not {"despesa", "escolha", "categorias", "total_atual"} & set(r.text.replace('"', " ").split())


# ── o mesmo número que o chat ────────────────────────────────────────────────

@pytest.mark.parametrize("mes", ["2026-10", "2026-09"])
def test_endpoint_tool_do_chat_e_servico_dao_o_mesmo_numero(monkeypatch, mes):
    uid = usuario_pagante("plus")
    semeia(uid)
    libera(monkeypatch, uid)
    r = ok(uid, mes=mes)
    srv = cp.comparar_mes(uid, int(mes[:4]), int(mes[5:]), AGORA)
    tool = _compare_periods(uid, {"mes": mes})
    for k in LINHAS:
        for c in CAMPOS:
            assert _dec(r[k][c]) == srv[k][c] == _dec(tool[k][c]), (k, c)
        assert r[k]["estado"] == srv[k]["estado"] == tool[k]["estado"]
    chave = lambda x: [(c["categoria"], *(_dec(c[f]) for f in CAMPOS), c["estado"]) for c in x["categorias"]]  # noqa: E731
    assert chave(r) == chave(srv) == chave(tool) and chave(r)
    assert r["escolha"] == srv["escolha"] == tool["escolha"]
    assert r["motivo"] == srv["motivo"] and r["categorias_omitidas"] == srv["categorias_omitidas"]
    for lado in ("atual", "anterior"):
        assert (r[lado]["inicio"], r[lado]["fim"]) == (srv[lado]["inicio"].isoformat(), srv[lado]["fim"].isoformat())


def test_dinheiro_e_pct_saem_como_texto_e_a_nota_do_llm_nao_sai(monkeypatch):
    uid = usuario_pagante("plus")
    semeia(uid)
    libera(monkeypatch, uid)
    r = ok(uid, mes="2026-10")
    assert (r["despesa"]["total_atual"], r["despesa"]["total_anterior"], r["despesa"]["pct"]) == ("1235.00", "1550.00", "-20.3")
    assert r["resultado"]["pct"] is None and r["modo"] == "mes_aberto"
    assert (r["atual"], r["anterior"]) == ({"inicio": "2026-10-01", "fim": "2026-10-12", "dias": 12},
                                           {"inicio": "2026-09-01", "fim": "2026-09-12", "dias": 12})
    assert "nota" not in r and r["escolha"].startswith("Dias 1 a 12 de outubro de 2026")


# ── gate antes da leitura, downgrade ─────────────────────────────────────────

def test_essencial_recebe_403_sem_payload_e_nada_e_lido(monkeypatch):
    uid = usuario_pagante("essencial")
    libera(monkeypatch, uid)
    chamadas = _proibe_leitura(monkeypatch)
    _sem_payload(pede(uid, mes="2026-10"))
    _sem_payload(pede(uid))
    assert chamadas == []


def test_downgrade_na_sessao_corta_nos_dois_namespaces(monkeypatch):
    uid = completo(usuario_pagante("plus"))
    semeia(uid)
    libera(monkeypatch, uid)
    c = TestClient(dashboard.app)
    c.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, sessao_de(uid)["dashboard"])
    assert c.get(URL).json()["despesa"]["total_atual"] == "1235.00"                     # positivo: Plus lê
    app = pede(uid, "/api/app/comparacao", bearer=True)
    assert app.status_code == 200 and app.json() == c.get(URL).json()                   # o mesmo corpo no app
    promote_to_pro(uid, plan="essencial")
    chamadas = _proibe_leitura(monkeypatch)
    _sem_payload(c.get(URL))                                                            # o mesmo cliente
    _sem_payload(pede(uid, "/api/app/comparacao", bearer=True))
    assert chamadas == []


# ── dois usuários ────────────────────────────────────────────────────────────

def test_cada_um_recebe_o_seu_e_user_id_na_query_nao_muda_nada(monkeypatch):
    a, b = usuario_pagante("plus"), usuario_pagante("plus")
    semeia(a)
    semeia(b, fator=7)
    libera(monkeypatch, a, b)
    rb = ok(b, mes="2026-10", user_id=a, uid=a)
    assert (rb["despesa"]["total_atual"], rb["despesa"]["total_anterior"]) == ("8645.00", "10850.00")
    assert {c["categoria"]: c["total_atual"] for c in rb["categorias"]}["Alimentação"] == "6790.00"
    ra = ok(a, mes="2026-10", user_id=b)
    assert (ra["despesa"]["total_atual"], ra["despesa"]["total_anterior"]) == ("1235.00", "1550.00")


# ── bordas do endpoint (as do calendário são do serviço) ─────────────────────

def test_mes_ausente_e_o_corrente(monkeypatch):
    uid = usuario_pagante("plus")
    semeia(uid)
    libera(monkeypatch, uid)
    assert ok(uid) == ok(uid, mes="2026-10")


@pytest.mark.parametrize("mes", ["2026-11", "2027-01", "2026-13", "2026-1", "1999-12", "1000-01"])
def test_mes_futuro_malformado_ou_fora_do_servico_e_422_nunca_500(monkeypatch, mes):
    uid = usuario_pagante("plus")
    libera(monkeypatch, uid)
    r = pede(uid, mes=mes)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["details"][0]["loc"] == ["query", "mes"]


def test_mes_anterior_fora_do_historico_do_plano_vem_sem_totais(monkeypatch):
    uid = usuario_pagante("plus")
    semeia(uid)
    libera(monkeypatch, uid)
    r = ok(uid, mes="2025-10")                         # setembro/2025 começa antes do corte do `history_days` do Plus
    assert r["motivo"] == "inicio_do_historico" and r["despesa"] is None and r["categorias"] == []
    assert ok(uid, mes="2026-09")["despesa"] is not None   # positivo: dentro da janela
