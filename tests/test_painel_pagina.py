"""`/painel` (dashboard v2): quem recebe a página, quem vai para onde, pelo monólito real.

Sessão real (`_issue_session_token`), banco real e o `TestClient` no app do
monólito. Negativos: cada porta fechada redireciona para o lugar certo — e a chave
que falha fecha. Positivos: os dois cookies de sessão, a liberação por e-mail e a
carência recebem o HTML; sem eles o grupo passaria numa rota que redireciona todos.
"""
import pytest
from fastapi.testclient import TestClient

import db
import db_support
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import libera, sessao_de, sql
from conftest import em_carencia, promote_to_pro
from core.sessions import revoke_session
from frontend.routes.shared import AUTH_COOKIE_NAME, DASHBOARD_COOKIE_NAME, FRONTEND_DIR, _asset_hash

UA_APP = "Mozilla/5.0 PigBankApp/1.0"


@pytest.fixture(autouse=True)
def _ninguem_liberado(monkeypatch):
    monkeypatch.setenv("DASHBOARD_V2_BETA_EMAILS", "")
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", "")


def pronto(uid, monkeypatch=None):
    """Pro com o wizard feito (senão o gate de onboarding manda para /onboarding)."""
    promote_to_pro(uid)
    db.mark_onboarding_completed(uid)
    if monkeypatch:
        libera(monkeypatch, uid)
    return sessao_de(uid)


def get(path="/painel", *, auth=None, dash=None, ua=None):
    client = TestClient(dashboard.app)
    if auth:
        client.cookies.set(AUTH_COOKIE_NAME, auth)
    if dash:
        client.cookies.set(DASHBOARD_COOKIE_NAME, dash)
    return client.get(path, headers={"User-Agent": ua} if ua else {}, follow_redirects=False)


def vai_para(r, destino):
    assert r.status_code == 302, (r.status_code, r.text[:200])
    assert r.headers["location"] == destino


def serve_a_pagina(r):
    assert r.status_code == 200, (r.status_code, r.headers.get("location"))
    assert r.headers["content-type"].startswith("text/html")
    assert r.headers["cache-control"] == "no-store"
    versao = _asset_hash("dashboard-app.js", (FRONTEND_DIR / "dashboard-app.js").stat().st_mtime_ns)
    assert f'src="/dashboard-app.js?v={versao}"' in r.text
    assert 'id="pigbank-dashboard"' in r.text


# ── Negativos ────────────────────────────────────────────────────────────────

def test_anonimo_vai_ao_login_com_next():
    vai_para(get(), "/login?next=/painel")


def test_dashboard_token_revogado_vai_ao_login(monkeypatch, user_id):
    s = pronto(user_id, monkeypatch)
    assert revoke_session(user_id, s["jti"])
    vai_para(get(dash=s["dashboard"]), "/login?next=/painel")


def test_liberado_com_user_agent_do_app_vai_ao_app(monkeypatch, user_id):
    s = pronto(user_id, monkeypatch)
    vai_para(get(auth=s["access"], dash=s["dashboard"], ua=UA_APP), "/app")


def test_nao_liberado_vai_ao_app(user_id):
    s = pronto(user_id)
    vai_para(get(auth=s["access"], dash=s["dashboard"]), "/app")


def test_nao_liberado_com_user_agent_do_app_vai_ao_app(user_id):
    s = pronto(user_id)
    vai_para(get(auth=s["access"], dash=s["dashboard"], ua=UA_APP), "/app")


def test_liberado_sem_escolha_de_plano_vai_aos_precos(monkeypatch, user_id):
    s = pronto(user_id, monkeypatch)
    sql("update auth_accounts set plan='free', plan_selected_at=null where user_id=%s", user_id)
    vai_para(get(dash=s["dashboard"]), "/precos?escolha=1")


@pytest.mark.parametrize("rota", ["/painel", "/app"])
def test_sem_plano_e_sem_onboarding_vai_aos_precos_antes_do_wizard(monkeypatch, user_id, rota):
    """Plano primeiro, onboarding depois (docstring de `gate_onboarding`): trocar a
    ordem dos dois gates desviaria quem nem escolheu plano para o wizard."""
    promote_to_pro(user_id)
    libera(monkeypatch, user_id)
    sql("update auth_accounts set plan='free', plan_selected_at=null where user_id=%s", user_id)
    s = sessao_de(user_id)
    vai_para(get(rota, auth=s["access"], dash=s["dashboard"]), "/precos?escolha=1")


def test_liberado_cortado_vai_aos_precos(monkeypatch, user_id):
    s = pronto(user_id, monkeypatch)
    sql("update auth_accounts set plan='free' where user_id=%s", user_id)
    db.mark_plan_selected(user_id)
    db_support.invalidate_auth_user_cache(user_id)
    vai_para(get(dash=s["dashboard"]), "/precos?escolha=1")


def test_liberado_sem_onboarding_vai_ao_wizard(monkeypatch, user_id):
    promote_to_pro(user_id)
    libera(monkeypatch, user_id)
    vai_para(get(dash=sessao_de(user_id)["dashboard"]), "/onboarding")


def test_chave_que_falha_fecha(monkeypatch, user_id):
    import core.services.plan_service as plan_service

    s = pronto(user_id, monkeypatch)

    def quebra(*a, **k):
        raise RuntimeError("banco fora")

    monkeypatch.setattr(plan_service, "dashboard_v2_enabled", quebra)
    vai_para(get(auth=s["access"], dash=s["dashboard"]), "/app")


# ── Positivos ────────────────────────────────────────────────────────────────

def test_liberado_com_os_dois_cookies_recebe_a_pagina(monkeypatch, user_id):
    s = pronto(user_id, monkeypatch)
    serve_a_pagina(get(auth=s["access"], dash=s["dashboard"]))


def test_so_dashboard_token_access_vencido_recebe_a_pagina(monkeypatch, user_id):
    """O access (15 min) já saiu do navegador; o dashboard_token (12 h) segue."""
    s = pronto(user_id, monkeypatch)
    serve_a_pagina(get(dash=s["dashboard"]))


def test_so_auth_token_recebe_a_pagina(monkeypatch, user_id):
    s = pronto(user_id, monkeypatch)
    serve_a_pagina(get(auth=s["access"]))


def test_liberado_por_email_recebe_a_pagina(monkeypatch, user_id):
    s = pronto(user_id)
    email = db.get_auth_user(user_id)["email"]
    monkeypatch.setenv("DASHBOARD_V2_BETA_EMAILS", f"outro@x.com, {email.upper()}")
    serve_a_pagina(get(dash=s["dashboard"]))


def test_em_carencia_recebe_a_pagina(monkeypatch, user_id):
    em_carencia(user_id)
    db.mark_onboarding_completed(user_id)
    libera(monkeypatch, user_id)
    serve_a_pagina(get(dash=sessao_de(user_id)["dashboard"]))


# ── Anti-loop com a /login (#644) ────────────────────────────────────────────

@pytest.mark.parametrize("cookies", ["nenhum", "so_dashboard", "so_auth", "dashboard_revogado", "os_dois"])
def test_login_que_aceita_a_sessao_nao_e_mandado_de_volta(monkeypatch, user_id, cookies):
    """A /login pula para o `next` quando o /auth/validate dá 200; aí o /painel não
    pode devolver ao login, senão o navegador fica num laço."""
    s = pronto(user_id, monkeypatch)
    if cookies == "dashboard_revogado":
        assert revoke_session(user_id, s["jti"])
    kw = {
        "nenhum": {}, "so_dashboard": {"dash": s["dashboard"]}, "so_auth": {"auth": s["access"]},
        "dashboard_revogado": {"dash": s["dashboard"]},
        "os_dois": {"auth": s["access"], "dash": s["dashboard"]},
    }[cookies]
    validate = get("/auth/validate", **kw)
    painel = get(**kw)
    if validate.status_code == 200:
        assert painel.headers.get("location", "").split("?")[0] != "/login", cookies
    else:
        assert validate.status_code == 401, (cookies, validate.status_code)


# ── /auth/me ─────────────────────────────────────────────────────────────────

def test_auth_me_devolve_a_chave(monkeypatch, user_id):
    s = pronto(user_id)
    assert get("/auth/me", auth=s["access"]).json()["dashboard_v2_enabled"] is False
    libera(monkeypatch, user_id)
    assert get("/auth/me", auth=s["access"]).json()["dashboard_v2_enabled"] is True
    # A chave é pura: o UA do app não a muda (quem esconde o link no app é o /app).
    assert get("/auth/me", auth=s["access"], ua=UA_APP).json()["dashboard_v2_enabled"] is True


# ── Assets ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("nome, mime", [("dashboard-app.js", "application/javascript"),
                                        ("dashboard-app.css", "text/css")])
def test_assets_do_painel(nome, mime):
    r = get(f"/{nome}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(mime)
    assert r.headers["cache-control"] == "no-cache"


def test_build_do_painel_emite_apenas_assets_com_rotas():
    assert sorted(p.name for p in FRONTEND_DIR.glob("dashboard-app.*")) == [
        "dashboard-app.css", "dashboard-app.js",
    ]


def test_robots_esconde_o_painel():
    assert "Disallow: /painel" in get("/robots.txt").text.splitlines()
