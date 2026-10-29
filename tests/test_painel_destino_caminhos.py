"""Destino pós-autenticação com DASHBOARD_V2_TODOS=1: cada caminho deste arquivo que emite `dashboard_url`
(ou `Location`) tem de passar o `request` ao `_post_login_url`, senão o navegador cai no /home.
Aqui: MFA, Google, e-mail e cadastros. O `/auth/login` e o Apple (`_concluir_login`) estão em
`test_painel_pagina.py`.

Um caso por caminho real; o dublê é só o Google, o e-mail e (nos dois cadastros novos, onde o
/precos vence por padrão) o `needs_plan_selection`. Controle negativo: tirar o `request` de
uma chamada deixa vermelho exatamente o caso dela. Positivo: o app (UA PigBankApp) segue no
/home e a conta sem senha vai ao /home (onde mora o "Crie sua senha"), nunca ao /painel."""
import os
import uuid

import pyotp
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

os.environ.setdefault("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())

import core.services.plan_service as plan_service  # noqa: E402
import db  # noqa: E402
import frontend.finance_bot_websocket_custom as dashboard  # noqa: E402
from _apoio_auth_app import csrf, google_de_mentira, login_google, sessao_de, sql  # noqa: E402
from conftest import promote_to_pro  # noqa: E402
from frontend.routes.shared import AUTH_COOKIE_NAME, DASHBOARD_COOKIE_NAME  # noqa: E402

SENHA = "Senha-forte-1234"
UA_APP = "Mozilla/5.0 PigBankApp/1.0"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("DASHBOARD_V2_BETA_EMAILS", "")
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", "")
    monkeypatch.setenv("DASHBOARD_V2_TODOS", "1")


def _telefone():
    return f"55119{uuid.uuid4().int % 100_000_000:08d}"


def _email():
    return f"destino-{uuid.uuid4().hex[:10]}@example.com"


def _conta(mfa=False):
    email = _email()
    uid = int(db.register_auth_user(email, SENHA)["user_id"])
    promote_to_pro(uid)
    db.mark_onboarding_completed(uid)
    segredo = None
    if mfa:
        segredo = db.mfa_setup_secret(uid, email)["secret"]
        db.mfa_verify_and_enable(uid, pyotp.TOTP(segredo).now())
    return uid, email, segredo


def _logado(uid, ua=None):
    s = sessao_de(uid)
    c = TestClient(dashboard.app)
    c.cookies.set(AUTH_COOKIE_NAME, s["access"])
    c.cookies.set(DASHBOARD_COOKIE_NAME, s["dashboard"])
    return c, ({"User-Agent": ua} if ua else {})


def _sem_escolha_de_plano(monkeypatch):
    """Conta de cadastro novo cai no /precos; aqui o assunto é o que vem depois dele."""
    monkeypatch.setattr(plan_service, "needs_plan_selection", lambda *a, **k: False)
    monkeypatch.setattr(plan_service, "has_app_access", lambda *a, **k: True)


def _verify_email(monkeypatch, ua=None):
    import core.services.email_service as email_service
    monkeypatch.setattr(email_service, "send_welcome_email", lambda *a, **k: True)
    _sem_escolha_de_plano(monkeypatch)
    email = _email()
    code = db.create_email_verification(email, SENHA, _telefone())
    c = TestClient(dashboard.app)
    return c.post("/auth/verify-email", headers={**csrf(c), **({"User-Agent": ua} if ua else {})},
                  json={"email": email, "code": code}).json()["dashboard_url"]


def _mfa_verify_login(monkeypatch, ua=None):
    _, email, segredo = _conta(mfa=True)
    c = TestClient(dashboard.app)
    h = {**csrf(c), **({"User-Agent": ua} if ua else {})}
    desafio = c.post("/auth/login", headers=h, json={"email": email, "password": SENHA}).json()["mfa_challenge"]
    return c.post("/auth/mfa/verify-login", headers=h, json={
        "challenge": desafio, "code": pyotp.TOTP(segredo).now()}).json()["dashboard_url"]


def _dashboard_token(monkeypatch, ua=None):
    c, h = _logado(_conta()[0], ua)
    return c.post("/auth/dashboard-token", headers={**csrf(c), **h}).json()["dashboard_url"]


def _dashboard_link(monkeypatch, ua=None):
    uid = _conta()[0]
    c, h = _logado(uid, ua)
    return c.post("/auth/dashboard-link", headers={**csrf(c), **h},
                  json={"code": db.create_dashboard_session(uid)}).json()["dashboard_url"]


def _google_callback(monkeypatch, ua=None):
    _, email, _ = _conta()
    google_de_mentira(monkeypatch, email)
    c = TestClient(dashboard.app, headers={"User-Agent": ua} if ua else None)
    return login_google(0, c)[1].headers["location"]


def _google_complete_signup(monkeypatch, ua=None):
    _sem_escolha_de_plano(monkeypatch)
    email = _email()
    token = db.create_pending_google_signup(f"sub-{email}", email, "Fulana")
    c = TestClient(dashboard.app)
    return c.post("/auth/google/complete-signup",
                  headers={**csrf(c), **({"User-Agent": ua} if ua else {})},
                  json={"token": token, "name": "Fulana", "phone": _telefone(),
                        "accepted_terms": True}).json()["dashboard_url"]


CAMINHOS = [_verify_email, _mfa_verify_login, _dashboard_token, _dashboard_link,
            _google_callback, _google_complete_signup]


@pytest.mark.parametrize("caminho", CAMINHOS, ids=lambda f: f.__name__.lstrip("_"))
def test_cada_caminho_leva_o_navegador_ao_painel_e_o_app_ao_home(caminho, monkeypatch):
    assert caminho(monkeypatch).endswith("/painel")
    assert caminho(monkeypatch, UA_APP).endswith("/home")


# ── Conta sem senha (só link mágico): o /painel não tem o "Crie sua senha" ────

def _sem_senha():
    uid = _conta()[0]
    sql("update auth_accounts set password_hash = NULL where user_id = %s", uid)
    return uid


def test_conta_sem_senha_nao_vai_ao_painel():
    uid = _sem_senha()
    r = TestClient(dashboard.app).get(f"/d/{db.create_dashboard_session(uid)}", follow_redirects=False)
    assert r.headers["location"] == "/app"
    c, _ = _logado(uid)
    assert c.get("/auth/validate").json()["dashboard_url"] == "/home"
    assert c.post("/auth/dashboard-token", headers=csrf(c)).json()["dashboard_url"].endswith("/home")


def test_conta_com_senha_vai_ao_painel_pelo_d_e_pelo_validate():
    uid = _conta()[0]
    r = TestClient(dashboard.app).get(f"/d/{db.create_dashboard_session(uid)}", follow_redirects=False)
    assert r.headers["location"] == "/painel"
    c, _ = _logado(uid)
    assert c.get("/auth/validate").json()["dashboard_url"] == "/painel"
