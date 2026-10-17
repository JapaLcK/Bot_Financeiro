"""KPI "vs período anterior" das Análises (`/analytics/{uid}/kpis`) na regra única do PL-07 (K2).

O delta sai de `comparar_mes(..., meses=N)`: do dia 1 do 1º dos N meses até hoje contra os mesmos
dias N meses antes, cartão pela fatura. A regra antiga (os mesmos N dias corridos antes de uma
janela que inclui o mês aberto pela metade) dava −16,7% para quem gasta igual todo mês: é o
controle negativo do primeiro teste. O gate vem antes de ler o anterior. A autorização de sessão
é isolada como em `tests/test_plan_permissions.py` (dono/sessão: `tests/test_routes_analytics.py`).
"""
from __future__ import annotations

import logging
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard
import utils_date
from conftest import em_carencia, usuario_pagante
from core.services import comparacao_periodos as cp
from db import analytics
from frontend.routes import shared
from frontend.routes.analytics import FORA_DO_HISTORICO
from test_comparacao_periodos import _lanc
from utils_date import _tz

HOJE = datetime(2026, 10, 15, 10, 0, tzinfo=_tz())
CHAVES = {"prev", "delta_pct", "delta_escolha", "delta_meses"}


class _Data(date):
    @classmethod
    def today(cls):
        return HOJE.date()


@pytest.fixture(autouse=True)
def _mundo(monkeypatch):
    """Um relógio só: o do serviço (`now_tz`) e o da janela dos totais (`resolve_window`, `date.today()`)."""
    monkeypatch.setenv("PLANS_V2_ENABLED", "1")
    monkeypatch.setattr(utils_date, "now_tz", lambda: HOJE)
    monkeypatch.setattr(analytics, "date", _Data)
    monkeypatch.setattr(shared, "authorize_dashboard_access", lambda *a, **k: None)


def kpis(uid, months=None, **params):
    params = {"months": months, **params} if months else params
    r = TestClient(dashboard.app).get(f"/analytics/{uid}/kpis", params=params)
    assert r.status_code == 200, r.text
    return r.json()["kpis"]


def gasto_constante(uid):
    """R$ 3.000 todo mês (1.500 no dia 5 e 1.500 no dia 25), de maio a setembro; outubro só até hoje (15)."""
    for mes in range(5, 10):
        _lanc(uid, "despesa", 1500, mes, 5, "Mercado")
        _lanc(uid, "despesa", 1500, mes, 25, "Mercado")
    _lanc(uid, "despesa", 1500, 10, 5, "Mercado")
    _lanc(uid, "receita", 4000, 8, 1)
    _lanc(uid, "receita", 5000, 5, 1)


def test_gasto_igual_todo_mes_da_zero_e_nao_menos_16_7(monkeypatch):
    uid = usuario_pagante("plus")
    gasto_constante(uid)
    k = kpis(uid, 3)
    assert k["delta_pct"] == {"income": -20.0, "expense": 0.0}
    assert k["delta_escolha"].startswith("De 1º de agosto de 2026 a 15 de outubro de 2026 contra os mesmos dias 3 meses antes")
    assert "Cartão pela fatura" in k["delta_escolha"]
    assert "prev" not in k and k["total_expense"] == 7500.0             # os totais: a mesma janela
    assert k["delta_meses"] == 3


def test_essencial_nao_recebe_nem_le_o_anterior(monkeypatch):
    uid = usuario_pagante("essencial")
    gasto_constante(uid)
    chamadas: list = []
    for nome in ("comparar_mes", "_comparar"):
        monkeypatch.setattr(cp, nome, lambda *a, **k: chamadas.append(a))
    k = kpis(uid, 3)
    assert not CHAVES & set(k) and chamadas == []
    assert "total_expense" in k                                         # o básico continua


def test_doze_meses_no_plus_sai_do_historico_e_no_pro_tem_numero():
    plus, pro = usuario_pagante("plus"), usuario_pagante("pro_max")
    for uid in (plus, pro):
        gasto_constante(uid)
        _lanc(uid, "despesa", 900, 6, 5, "Mercado", ano=2025)            # dentro dos 12 meses de antes
    k = kpis(plus, 12)                                    # o `history_days` do Plus não alcança 11/2024
    assert k["delta_pct"] == {"income": None, "expense": None}
    assert k["delta_escolha"].startswith(FORA_DO_HISTORICO + " De 1º de novembro de 2025")   # o "—" se explica
    kp = kpis(pro, 12)                                    # o do Pro alcança
    assert kp["delta_pct"]["expense"] is not None and FORA_DO_HISTORICO not in kp["delta_escolha"]


def test_janela_livre_fica_sem_delta(monkeypatch):
    uid = usuario_pagante("plus")
    gasto_constante(uid)
    r = TestClient(dashboard.app).get(f"/analytics/{uid}/kpis", params={"from": "2026-08-01", "to": "2026-10-16"})
    k = r.json()["kpis"]
    assert k["delta_pct"] == {"income": None, "expense": None} and k["delta_escolha"] is None


def test_so_from_sem_to_segue_a_janela_de_meses_e_tem_delta():
    """`resolve_window` só usa a janela livre com `from` E `to`; com um só, valem os meses."""
    uid = usuario_pagante("plus")
    gasto_constante(uid)
    for extra in ({"from": "2026-01-01"}, {"to": "2026-12-01"}):
        k = kpis(uid, 3, **extra)
        assert (k["from"], k["delta_pct"]["expense"]) == ("2026-08-01", 0.0), extra


def test_falha_na_comparacao_nao_derruba_os_kpis_e_loga_so_a_classe(monkeypatch, caplog):
    uid = usuario_pagante("plus")
    gasto_constante(uid)

    def falha(*a, **k):
        raise RuntimeError("segredo-do-usuario 1234")
    monkeypatch.setattr(cp, "comparar_mes", falha)
    with caplog.at_level(logging.WARNING):
        k = kpis(uid, 3)
    assert k["delta_pct"] == {"income": None, "expense": None} and k["total_expense"] == 7500.0
    assert "RuntimeError" in caplog.text and "segredo" not in caplog.text


def test_plus_vencido_em_carencia_nao_recebe_comparacao_em_lugar_nenhum(monkeypatch):
    """Carência: tier `free` com acesso ao app. O endpoint da v2 recusa e o KPI sai sem delta."""
    from test_api_v2_comparacao import pede
    uid = em_carencia(usuario_pagante("plus"), plan="plus")
    gasto_constante(uid)
    monkeypatch.setenv("DASHBOARD_V2_BETA_EMAILS", "")
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", str(uid))
    r = pede(uid, mes="2026-10")
    assert r.status_code == 403 and r.json()["error"]["code"] == "pro_required", r.text
    assert not CHAVES & set(kpis(uid, 3))


def test_delta_meses_e_o_n_da_janela_com_o_teto_do_resolve_window():
    """O rótulo do painel antigo diz "N meses antes" com este N; acima do teto (36) vale o teto."""
    uid = usuario_pagante("pro_max")
    gasto_constante(uid)
    assert [kpis(uid, m)["delta_meses"] for m in (3, 12, 40)] == [3, 12, 36]
    assert kpis(uid, None, **{"from": "2026-08-01", "to": "2026-10-16"})["delta_meses"] is None   # janela livre
