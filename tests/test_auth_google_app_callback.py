"""Google no app nativo — o `start` e o `callback` (item 6 da Fase 3).

`GET /auth/google/start?app=2` marca o `state` com `nat-`; o callback devolve
ao app nativo pelo scheme `pigbank://` o código (conta existente), o token do
pré-cadastro (conta nova) e os ERROS, como código fixo. O app antigo (`app=1`,
`app-`, `pigbankai://`) e o site ficam como estavam.

Banco, rotas, state e cookie são os de verdade; só o Google é dublê
(`_apoio_auth_app.google_de_mentira`).

Controle negativo (medido; comando e resultado no corpo do PR):
`_google_app_scheme` devolvendo `pigbankai` para `nat-` → B3 e B4 vermelhos;
erro de `nat-` indo para a landing → B5 vermelho;
destino do erro sem conferir o state contra o cookie → B6 vermelho.
Controle positivo: B2, B3b e B5b (app antigo e site intactos); B5 para B6.
"""
import uuid
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import google_de_mentira, login_google

SENHA = "senha-forte-123"
COOKIES_DE_SESSAO = ("auth_token", "dashboard_token", "refresh_token")


def _email() -> str:
    return f"google-app-{uuid.uuid4().hex[:10]}@example.com"


def _conta_existente() -> tuple[int, str]:
    email = _email()
    return int(db.register_auth_user(email, SENHA)["user_id"]), email


def _set_cookies(resposta) -> list[str]:
    return [c.split("=", 1)[0] for c in resposta.headers.get_list("set-cookie")]


def _query(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


# ── start ────────────────────────────────────────────────────────────────────

def test_b1_start_app2_marca_nat_e_nao_grava_cookie_de_next(monkeypatch):
    google_de_mentira(monkeypatch, _email())
    r = TestClient(dashboard.app).get(
        "/auth/google/start?app=2&next=%2Fcontinuar-compra", follow_redirects=False
    )
    assert r.status_code == 302, r.text
    assert _query(r.headers["location"])["state"].startswith("nat-")
    proximos = [c for c in r.headers.get_list("set-cookie")
                if c.startswith(dashboard.GOOGLE_OAUTH_NEXT_COOKIE + "=")]
    # Só o delete (max-age=0) pode sair; o destino da compra, nunca.
    assert all("/continuar-compra" not in c for c in proximos), proximos


def test_b2_start_app1_continua_app(monkeypatch):
    google_de_mentira(monkeypatch, _email())
    r = TestClient(dashboard.app).get("/auth/google/start?app=1", follow_redirects=False)
    assert r.status_code == 302, r.text
    assert _query(r.headers["location"])["state"].startswith("app-")


# ── callback: sucesso ────────────────────────────────────────────────────────

def test_b3_callback_nat_conta_existente_devolve_codigo_pelo_pigbank(monkeypatch):
    _, email = _conta_existente()
    google_de_mentira(monkeypatch, email)
    _, r = login_google(2)
    assert r.status_code == 302, r.text
    assert r.headers["location"].startswith("pigbank://auth?code="), r.headers["location"]
    assert _query(r.headers["location"])["code"]
    for cookie in COOKIES_DE_SESSAO:
        assert cookie not in _set_cookies(r), f"{cookie} saiu no navegador do login"


def test_b3b_callback_app_antigo_continua_pigbankai_e_o_codigo_abre_o_d(monkeypatch):
    _, email = _conta_existente()
    google_de_mentira(monkeypatch, email)
    client, r = login_google(1)
    assert r.status_code == 302, r.text
    assert r.headers["location"].startswith("pigbankai://auth?code="), r.headers["location"]

    d = client.get(f"/d/{_query(r.headers['location'])['code']}", follow_redirects=False)
    assert d.status_code == 302, d.text
    assert "auth_token" in _set_cookies(d)


def test_b4_callback_nat_conta_nova_devolve_onboarding_pelo_pigbank(monkeypatch):
    google_de_mentira(monkeypatch, _email())
    _, r = login_google(2)
    assert r.status_code == 302, r.text
    assert r.headers["location"].startswith("pigbank://auth?onboarding=gso_"), r.headers["location"]


# ── callback: erros ──────────────────────────────────────────────────────────

def _erro(monkeypatch, app: int, caso: str) -> str:
    """Location do callback no erro `caso`, pelo fluxo real do `app`."""
    if caso == "exclusao":
        uid, email = _conta_existente()
        db.schedule_account_deletion(uid, SENHA)
    else:
        email = _email()
    google_de_mentira(monkeypatch, email, verificado=caso != "email_nao_verificado")
    query = {
        "cancelado": {"error": "access_denied"},
        "sem_code": {"code": ""},
        "state_errado": {"state": {2: "nat-", 1: "app-"}.get(app, "") + "outro"},
    }.get(caso, {})
    _, r = login_google(app, **query)
    assert r.status_code == 302, r.text
    return r.headers["location"]


# Só com o state conferido contra o cookie o erro volta ao app; state errado
# cai na landing (B6).
ERROS = {
    "cancelado": "cancelado",
    "sem_code": "falha",
    "email_nao_verificado": "email_nao_verificado",
    "exclusao": "conta_em_exclusao",
}


@pytest.mark.parametrize("caso", list(ERROS))
def test_b5_callback_nat_devolve_o_erro_ao_app_com_codigo_fixo(monkeypatch, caso):
    assert _erro(monkeypatch, 2, caso) == f"pigbank://auth?erro={ERROS[caso]}"


@pytest.mark.parametrize("app", [1, 0], ids=["app_antigo", "site"])
@pytest.mark.parametrize("caso", [*ERROS, "state_errado"])
def test_b5b_callback_app_antigo_e_site_continuam_na_landing(monkeypatch, app, caso):
    assert _erro(monkeypatch, app, caso).startswith("/?google_error=")


# ── callback: state que não bate com o cookie ────────────────────────────────

def test_b6_nat_forjado_sem_cookie_nao_volta_ao_app(monkeypatch):
    # Sem passar pelo /start: nenhum cookie de state.
    google_de_mentira(monkeypatch, _email())
    r = TestClient(dashboard.app).get(
        "/auth/google/callback",
        params={"state": "nat-forjado", "error": "access_denied"},
        follow_redirects=False,
    )
    assert r.status_code == 302, r.text
    assert r.headers["location"].startswith("/?google_error="), r.headers["location"]


@pytest.mark.parametrize("query", [{"error": "access_denied"}, {}],
                         ids=["com_error", "com_code"])
def test_b6_nat_com_cookie_diferente_cai_na_landing(monkeypatch, query):
    # Passa pelo /start (cookie gravado) e volta com OUTRO state `nat-`.
    google_de_mentira(monkeypatch, _email())
    _, r = login_google(2, state="nat-outro", **query)
    assert r.status_code == 302, r.text
    assert r.headers["location"].startswith("/?google_error="), r.headers["location"]
