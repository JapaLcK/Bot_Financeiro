"""/familia/entrar (PR 6a do Plano Família): a página de aceite do convite, o retorno do Google e o destino pós-login.

Controles negativos (um a um; restaurado por backup + cmp) na tabela do relato do PR. Os positivos do grupo: a página abre
sem sessão e sem escolha de plano, o `next` do Google grava, o membro novo entra no /painel.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import sessao_de
from db import family as fam
from frontend.routes import shared
from frontend.routes.shared import AUTH_COOKIE_NAME, DASHBOARD_COOKIE_NAME, FRONTEND_DIR

SENHA = "SenhaForte123!"


def _cliente(uid=None):
    c = TestClient(dashboard.app)
    if uid:
        s = sessao_de(uid)
        c.cookies.set(AUTH_COOKIE_NAME, s["access"])
        c.cookies.set(DASHBOARD_COOKIE_NAME, s["dashboard"])
    return c


def _conta_nova() -> int:
    """Cadastro novo: plano free, `plan_selected_at` nulo (o gate de escolha de plano ainda fecha o /painel)."""
    return int(db.register_auth_user(f"fam-{uuid.uuid4().hex[:10]}@example.com", SENHA)["user_id"])


def test_pagina_abre_sem_sessao_e_sem_redirect():
    r = _cliente().get("/familia/entrar", follow_redirects=False)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert "familia-entrar.js" in r.text and "familia-convite.js" in r.text and 'name="robots"' in r.text


def test_pagina_abre_para_quem_ainda_nao_escolheu_plano(monkeypatch):
    monkeypatch.setenv("DASHBOARD_V2_TODOS", "1")
    c = _cliente(_conta_nova())
    assert c.get("/familia/entrar", follow_redirects=False).status_code == 200
    # controle: o gate de escolha de plano segue valendo no /painel (o que a página precisa NÃO herdar)
    r = c.get("/painel", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/precos?escolha=1"


def test_pagina_sem_ga4_nem_pixel_e_o_controle_com_pixel_liga(monkeypatch):
    monkeypatch.setattr(shared, "META_PIXEL_ID", "123456789")
    monkeypatch.setattr(shared, "GA4_MEASUREMENT_ID", "G-TESTE00000")
    html = _cliente().get("/familia/entrar").text
    assert "gtag" not in html and "fbq" not in html and "googletagmanager" not in html
    # controle negativo: a mesma página com pixel=True leva os dois (o GA4 gravaria o fragmento com o token)
    com = shared.html_file(FRONTEND_DIR / "familia-entrar.html", pixel=True).body.decode()
    assert "gtag('config'" in com and "fbq('init'" in com


@pytest.mark.parametrize("arq", ["familia-convite.js", "familia-entrar.js"])
def test_scripts_servidos(arq):
    r = _cliente().get("/" + arq)
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/javascript")
    assert r.headers["cache-control"] == "no-cache" and r.text == (FRONTEND_DIR / arq).read_text(encoding="utf-8")


def test_robots_nega_a_familia():
    assert "Disallow: /familia" in _cliente().get("/robots.txt").text


def test_login_e_cadastro_carregam_o_guardador_do_convite():
    for pagina in ("/login", "/cadastro", "/completar-cadastro"):
        assert "/familia-convite.js" in _cliente().get(pagina).text, pagina


# ── Google: o retorno ao convite ──

def _google_start(monkeypatch, next_):
    import core.services.google_oauth as google_oauth
    monkeypatch.setattr(google_oauth, "is_configured", lambda: True)
    monkeypatch.setattr(google_oauth, "build_authorization_url", lambda state: f"https://accounts.google.test/auth?state={state}")
    r = _cliente().get(f"/auth/google/start?next={next_}", follow_redirects=False)
    assert r.status_code == 302
    return [h for h in r.headers.get_list("set-cookie") if h.startswith(dashboard.GOOGLE_OAUTH_NEXT_COOKIE + "=")]


def test_google_start_grava_o_retorno_ao_convite(monkeypatch):
    (cookie,) = _google_start(monkeypatch, "%2Ffamilia%2Fentrar")
    assert cookie.split(";")[0].split("=", 1)[1].strip('"') == "/familia/entrar" and "Max-Age" in cookie and "HttpOnly" in cookie


@pytest.mark.parametrize("outro", ["%2Ffamilia%2Fentrar%2Fx", "%2Ffamilia", "%2Fpainel", "https%3A%2F%2Fevil.test%2Ffamilia%2Fentrar", "%2F%2Fevil.test"])
def test_google_start_ignora_outro_destino(monkeypatch, outro):
    (cookie,) = _google_start(monkeypatch, outro)  # o cookie volta APAGADO (valor vazio), não gravado
    assert cookie.split(";")[0].split("=", 1)[1].strip('"') == ""


def test_next_do_google_so_aceita_as_duas_rotas_conhecidas():
    f = dashboard._google_oauth_next_url
    assert f("/familia/entrar") == "/familia/entrar" and f("/continuar-compra") == "/continuar-compra"
    assert f("/familia/entrar?x=1") is None and f(None) is None


# ── Ponta a ponta: cadastro novo -> aceite -> /painel ──

def test_cadastro_novo_aceita_o_convite_e_entra_no_painel_sem_ir_aos_precos(monkeypatch):
    monkeypatch.setenv("DASHBOARD_V2_TODOS", "1")
    from test_api_v2_familia import _titular  # o titular com a cobertura de família (grant Pro Max), como no PR 3
    titular = _titular("Ana Souza")
    token = fam.create_invite(titular)
    novo = _conta_nova()
    db.mark_onboarding_completed(novo)  # o wizard é outro gate: aqui só a escolha de plano está em jogo
    c = _cliente(novo)
    antes = c.get("/painel", follow_redirects=False)
    assert antes.status_code == 302 and antes.headers["location"] == "/precos?escolha=1"  # controle: sem o aceite, fecha
    csrf = c.cookies.get("csrf_token")  # emitido no GET acima; o navegador o devolve no X-CSRF-Token (pbCsrfHeaders)
    assert csrf
    r = c.post("/api/v2/familia/convites/aceitar", json={"token": token}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200 and r.json() == {"papel": "membro", "permissoes": []}
    depois = c.get("/painel", follow_redirects=False)
    assert depois.status_code == 200, (depois.status_code, depois.headers.get("location"))
