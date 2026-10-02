"""`GET`/`PUT /api/v2/perfil` pelo monólito real: sessão, CSRF do pai, banco real.

Positivos: os 6 valores gravam e voltam, NULL volta `null`. Negativos: valor fora da
lista é 422 no envelope (e não grava), PUT sem CSRF é 403, B não lê nem muda o de A.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import csrf, sessao_de
from conftest import usuario_pagante
from db.signup_quiz import PERFIL_PADRAO, PERFIS, parse_quiz_cookie

PERFIL = "/api/v2/perfil"


@pytest.fixture
def libera_ids(monkeypatch):
    monkeypatch.setenv("DASHBOARD_V2_BETA_EMAILS", "")
    return lambda *ids: monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", ",".join(map(str, ids)))


def cliente(uid, com_csrf=True):
    c = TestClient(dashboard.app)
    c.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, sessao_de(uid)["dashboard"])
    return c, (csrf(c) if com_csrf else {})


def ler(quem, **params):
    c, _ = cliente(quem)
    r = c.get(PERFIL, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def gravar(quem, corpo, **params):
    c, h = cliente(quem)
    return c.put(PERFIL, json=corpo, headers=h, params=params)


@pytest.fixture
def uid(libera_ids):
    u = usuario_pagante()
    libera_ids(u)
    return u


def test_nunca_escolheu_volta_null(uid):
    assert ler(uid) == {"perfil": None}


@pytest.mark.parametrize("perfil", PERFIS + (PERFIL_PADRAO,))
def test_os_seis_valores_gravam_e_voltam(uid, perfil):
    r = gravar(uid, {"perfil": perfil})
    assert (r.status_code, r.json()) == (200, {"perfil": perfil}), r.text
    assert ler(uid) == {"perfil": perfil}


@pytest.mark.parametrize("corpo", [
    {"perfil": "PADRAO"}, {"perfil": "pj"}, {"perfil": None}, {"perfil": "padrao\x00"},
    {"perfil": 1}, {"perfil": ["padrao"]}, {}, {"perfil": " padrao"},
], ids=["maiuscula", "pj", "null", "nul", "numero", "lista", "sem_campo", "espaco"])
def test_invalido_e_422_no_envelope_e_nao_grava(uid, corpo):
    assert gravar(uid, {"perfil": "investir"}).status_code == 200
    r = gravar(uid, corpo)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "validation_error"
    assert ler(uid) == {"perfil": "investir"}


def test_put_sem_csrf_e_403(uid):
    c, _ = cliente(uid, com_csrf=False)
    r = c.put(PERFIL, json={"perfil": "padrao"})
    assert (r.status_code, r.json()) == (403, {"detail": "Token CSRF inválido ou ausente."})
    assert ler(uid) == {"perfil": None}


def test_rowcount_zero_e_404_conta_nao_encontrada(uid, monkeypatch):
    import api.v2.perfil as rota

    monkeypatch.setattr(rota, "gravar_perfil", lambda *_: 0)
    r = gravar(uid, {"perfil": "padrao"})
    assert (r.status_code, r.json()["error"]["code"]) == (404, "conta_nao_encontrada")


def test_quiz_segue_recusando_padrao():
    assert parse_quiz_cookie("v1.padrao") is None
    assert parse_quiz_cookie("v1.dividas") == ("dividas", None)  # controle positivo


def test_b_nao_le_nem_muda_o_perfil_de_a(libera_ids):
    a, b = usuario_pagante(), usuario_pagante()
    libera_ids(a, b)
    assert gravar(a, {"perfil": "investir"}).status_code == 200
    assert ler(b, user_id=a, uid=a) == {"perfil": None}
    assert gravar(b, {"perfil": "padrao"}, user_id=a, uid=a).status_code == 200
    assert (ler(a), ler(b)) == ({"perfil": "investir"}, {"perfil": "padrao"})
