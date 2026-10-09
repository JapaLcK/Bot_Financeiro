"""PUT /agents/{id}/xerife/lancamentos/{lid}/esperado pelo monólito real (sessão e CSRF
reais, `authorize_dashboard_access` SEM monkeypatch, Postgres).

Positivos: marcar, repetir (idempotente) e desmarcar. Negativos: sessão de outro usuário no
caminho = 403; lançamento de outro usuário, receita, movimento interno e id inexistente = o
MESMO 404 (a coluna do dono não muda); Essencial = 403 `pro_required`; fora do beta = 404;
corpo inválido = 422.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import csrf, sessao_de
from conftest import promote_to_pro, usuario_pagante
from test_xerife_deteccao import _lanca
from test_xerife_esperado import _coluna
from tests.test_api_v2_perfil import cliente
from frontend.routes.shared import FRONTEND_DIR


def _put(sessao_uid, path_uid, lid, corpo=None, com_csrf=True):
    c, h = cliente(sessao_uid, com_csrf)
    return c.put(f"/agents/{path_uid}/xerife/lancamentos/{lid}/esperado",
                 json={"esperado": True} if corpo is None else corpo, headers=h)


@pytest.fixture
def uid():
    return usuario_pagante()


def test_marca_repete_e_desmarca(uid):
    lid = _lanca(uid, 100, dias=1)
    for _ in range(2):                                          # idempotente
        r = _put(uid, uid, lid)
        assert r.status_code == 200 and r.json() == {"ok": True, "esperado": True}
        assert _coluna(lid) is not None
    r = _put(uid, uid, lid, {"esperado": False})
    assert r.status_code == 200 and r.json() == {"ok": True, "esperado": False}
    assert _coluna(lid) is None


def test_sessao_de_outro_usuario_no_caminho_e_403(uid):
    outro = usuario_pagante()
    lid = _lanca(uid, 100, dias=1)
    assert _put(outro, uid, lid).status_code == 403
    assert _coluna(lid) is None


def test_lancamento_de_outro_usuario_e_404_e_nao_muda(uid):
    outro = usuario_pagante()
    dele = _lanca(outro, 100, dias=1)
    r = _put(uid, uid, dele)
    assert r.status_code == 404
    assert _coluna(dele) is None
    assert r.json() == _put(uid, uid, 2_000_000_000).json()     # não vaza se existe


@pytest.mark.parametrize("kw", [{"tipo": "receita"}, {"interno": True}])
def test_receita_e_interno_sao_404(uid, kw):
    lid = _lanca(uid, 100, dias=1, **kw)
    assert _put(uid, uid, lid).status_code == 404
    assert _coluna(lid) is None


@pytest.mark.parametrize("corpo", [{}, {"esperado": "talvez"}, {"esperado": None}, []])
def test_corpo_invalido_e_422(uid, corpo):
    lid = _lanca(uid, 100, dias=1)
    assert _put(uid, uid, lid, corpo).status_code == 422
    assert _coluna(lid) is None


def test_sem_csrf_e_recusado(uid):
    lid = _lanca(uid, 100, dias=1)
    assert _put(uid, uid, lid, com_csrf=False).status_code == 403
    assert _coluna(lid) is None


def test_essencial_recebe_403_pro_required(uid):
    promote_to_pro(uid, plan="essencial")
    lid = _lanca(uid, 100, dias=1)
    r = _put(uid, uid, lid)
    assert r.status_code == 403 and r.json()["detail"]["error"] == "pro_required"
    assert _coluna(lid) is None


def test_fora_do_beta_a_rota_nao_existe(uid, monkeypatch):
    monkeypatch.setenv("AGENTS_UI_ENABLED", "0")
    monkeypatch.setenv("AGENTS_BETA_EMAILS", "")
    lid = _lanca(uid, 100, dias=1)
    assert _put(uid, uid, lid).status_code == 404
    assert _coluna(lid) is None


def test_script_novo_e_servido_e_carregado_pelo_painel():
    r = TestClient(dashboard.app).get("/dashboard-agent-esperado.js")
    assert r.status_code == 200 and "javascript" in r.headers["content-type"]
    assert "data-esperado-lancamento" in (FRONTEND_DIR / "dashboard.js").read_text(encoding="utf-8")
    assert "/dashboard-agent-esperado.js" in (FRONTEND_DIR / "dashboard.html").read_text(encoding="utf-8")
