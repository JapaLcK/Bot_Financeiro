"""PKCE (S256) no login Google do app nativo — issue #604, parte backend.

O `pigbank://auth?code=` pode ser interceptado por outro app (esquema customizado
não tem dono único). Código/pendente criado COM `code_challenge` só vira sessão
com o `code_verifier` certo. Rota real, banco real; só o Google é dublê.

Cada caso negativo prova a recusa ANTES da sessão: 400, nenhum token no corpo,
nenhum cookie de sessão, nenhuma linha nova em `auth_sessions`/`auth_accounts`
— e que o código/pendente NÃO foi queimado (o dono ainda entra).

Controle negativo (medido; resultado no relato do PR): tirar o `code_challenge`
do `where` de `consume_dashboard_session` → vermelhos P3 a P7; tirar a
comparação de `consume_pending_google_signup` → vermelho P9.
Controle positivo: P1/P2 (fluxo legítimo entra) e C1/C2 (sem PKCE, como antes).
"""
import base64
import hashlib
import secrets
import uuid
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import csrf, google_de_mentira, login_google, sessao_de

UA_APP = "PigBankApp/0.1.0 (iPhone 17 Pro; iOS 26.1)"
TOKENS = ("access_token", "refresh_token", "dashboard_token")
COOKIES_DE_SESSAO = ("auth_token", "dashboard_token", "refresh_token")


def _par() -> tuple[str, str]:
    """(verifier, challenge) com o S256 calculado aqui, não pelo código testado."""
    verifier = secrets.token_urlsafe(32)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _conta() -> tuple[int, str]:
    email = f"pkce-{uuid.uuid4().hex[:10]}@example.com"
    return int(db.register_auth_user(email, "senha-forte-123")["user_id"]), email


def _codigo(monkeypatch, desafio: str | None, app: int = 2) -> tuple[int, str]:
    """start(desafio) → callback de uma conta existente → (uid, código do 302)."""
    uid, email = _conta()
    google_de_mentira(monkeypatch, email)
    _, cb = login_google(app, desafio=desafio)
    assert cb.status_code == 302, cb.text
    return uid, parse_qs(urlparse(cb.headers["location"]).query)["code"][0]


def _troca(code: str, verifier: str | None = None):
    corpo = {"code": code, **({"code_verifier": verifier} if verifier else {})}
    return TestClient(dashboard.app).post(
        "/auth/google/exchange",
        headers={dashboard.APP_CLIENT_HEADER: "app", "User-Agent": UA_APP},
        json=corpo,
    )


def _sessoes(uid: int) -> int:
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) as n from auth_sessions where user_id = %s", (uid,))
        return cur.fetchone()["n"]


def _sem_sessao(r, uid: int, antes: int) -> None:
    """Recusa ANTES da sessão: 400 do contrato, sem token, sem cookie, sem linha."""
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "google_code_invalid"
    for chave in TOKENS:
        assert chave not in r.json()
    cookies = [c.split("=", 1)[0] for c in r.headers.get_list("set-cookie")]
    for cookie in COOKIES_DE_SESSAO:
        assert cookie not in cookies
    assert _sessoes(uid) == antes


def _pendente(monkeypatch, desafio: str | None) -> tuple[str, str]:
    """start(desafio) → callback de e-mail novo → (e-mail, token do onboarding)."""
    email = f"pkce-novo-{uuid.uuid4().hex[:10]}@example.com"
    google_de_mentira(monkeypatch, email)
    _, cb = login_google(2, desafio=desafio)
    assert cb.status_code == 302, cb.text
    return email, parse_qs(urlparse(cb.headers["location"]).query)["onboarding"][0]


def _completa(token: str, verifier: str | None = None):
    corpo = {
        "token": token, "name": "Fulana", "phone": f"55119{uuid.uuid4().int % 100_000_000:08d}",
        "accepted_terms": True, **({"code_verifier": verifier} if verifier else {}),
    }
    return TestClient(dashboard.app).post(
        "/auth/google/complete-signup",
        headers={dashboard.APP_CLIENT_HEADER: "app", "User-Agent": UA_APP},
        json=corpo,
    )


def _contas(email: str) -> int:
    from core.crypto import hash_pii_optional

    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select count(*) as n from auth_accounts where email_hash = %s",
            (hash_pii_optional(email, kind="email"),),
        )
        return cur.fetchone()["n"]


def test_vetor_da_rfc_7636():
    """Apêndice B da RFC: o S256 do código de produção bate com o oráculo público."""
    from core.services.pkce import s256

    assert s256("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk") == (
        "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    )


# ── Positivos ────────────────────────────────────────────────────────────────

def test_p1_start_callback_troca_com_o_verifier_certo(monkeypatch):
    verifier, desafio = _par()
    uid, code = _codigo(monkeypatch, desafio)
    r = _troca(code, verifier)
    assert r.status_code == 200, r.text
    assert r.json()["user_id"] == uid and r.json()["access_token"]
    assert _sessoes(uid) == 1


def test_p2_complete_signup_com_o_verifier_certo(monkeypatch):
    verifier, desafio = _par()
    email, token = _pendente(monkeypatch, desafio)
    r = _completa(token, verifier)
    assert r.status_code == 200, r.text
    assert r.json()["access_token"] and _contas(email) == 1


# ── Negativos: troca ─────────────────────────────────────────────────────────

def test_p3_verifier_errado_recusa_e_nao_queima_o_codigo(monkeypatch):
    verifier, desafio = _par()
    uid, code = _codigo(monkeypatch, desafio)
    _sem_sessao(_troca(code, _par()[0]), uid, 0)
    assert _troca(code, verifier).status_code == 200  # o dono ainda entra


def test_p4_verifier_ausente_em_codigo_com_challenge(monkeypatch):
    verifier, desafio = _par()
    uid, code = _codigo(monkeypatch, desafio)
    _sem_sessao(_troca(code), uid, 0)
    assert _troca(code, verifier).status_code == 200


def test_p5_verifier_de_outro_fluxo(monkeypatch):
    verifier_a, desafio_a = _par()
    verifier_b, desafio_b = _par()
    uid_a, code_a = _codigo(monkeypatch, desafio_a)
    uid_b, code_b = _codigo(monkeypatch, desafio_b)
    _sem_sessao(_troca(code_a, verifier_b), uid_a, 0)
    _sem_sessao(_troca(code_b, verifier_a), uid_b, 0)
    assert _troca(code_a, verifier_a).status_code == 200
    assert _troca(code_b, verifier_b).status_code == 200


def test_p6_codigo_com_challenge_nao_abre_o_d(monkeypatch):
    verifier, desafio = _par()
    uid, code = _codigo(monkeypatch, desafio)
    r = TestClient(dashboard.app).get(f"/d/{code}", follow_redirects=False)
    assert r.status_code == 401, r.text
    assert "dashboard_token" not in r.headers.get("set-cookie", "")
    assert _sessoes(uid) == 0
    assert _troca(code, verifier).status_code == 200


def test_p7_codigo_com_challenge_nao_serve_ao_dashboard_link(monkeypatch):
    verifier, desafio = _par()
    uid, code = _codigo(monkeypatch, desafio)
    dono = sessao_de(uid)  # o próprio dono logado: nem ele consome por esta rota
    antes = _sessoes(uid)
    client = TestClient(dashboard.app)
    r = client.post(
        "/auth/dashboard-link",
        headers={"Authorization": f"Bearer {dono['access']}", **csrf(client)},
        json={"code": code},
    )
    assert r.status_code == 401, r.text
    assert "dashboard_token" not in r.headers.get("set-cookie", "")
    assert _sessoes(uid) == antes
    assert _troca(code, verifier).status_code == 200


def test_p8_verifier_malformado_e_recusado_na_borda_sem_queimar(monkeypatch):
    verifier, desafio = _par()
    uid, code = _codigo(monkeypatch, desafio)
    for ruim in ("curto", "a" * 129, verifier[:-1] + "!"):
        r = _troca(code, ruim)
        assert r.status_code == 422, r.text
        assert _sessoes(uid) == 0
    assert _troca(code, verifier).status_code == 200


# ── Negativos: cadastro ──────────────────────────────────────────────────────

def test_p9_complete_signup_com_token_interceptado(monkeypatch):
    verifier, desafio = _par()
    email, token = _pendente(monkeypatch, desafio)
    for r in (_completa(token), _completa(token, _par()[0])):
        assert r.status_code == 400, r.text
        assert "access_token" not in r.json()
        assert "auth_token" not in r.headers.get("set-cookie", "")
        assert _contas(email) == 0
    assert _completa(token, verifier).status_code == 200  # o pendente seguiu de pé


# ── Start ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("ruim", ["curto", "a" * 44, "a" * 42 + "=", "a" * 42 + "!", "a" * 200])
def test_s1_start_com_challenge_invalido_da_400_sem_cookie(monkeypatch, ruim):
    google_de_mentira(monkeypatch, "x@example.com")
    r = TestClient(dashboard.app).get(
        f"/auth/google/start?app=2&code_challenge={ruim}", follow_redirects=False
    )
    assert r.status_code == 400, r.text
    assert "google_oauth" not in r.headers.get("set-cookie", "")


def test_s2_challenge_nao_viaja_no_state_do_google(monkeypatch):
    _, desafio = _par()
    google_de_mentira(monkeypatch, "x@example.com")
    r = TestClient(dashboard.app).get(
        f"/auth/google/start?app=2&code_challenge={desafio}", follow_redirects=False
    )
    assert r.status_code == 302
    assert desafio not in r.headers["location"]


def test_s3_challenge_de_tentativa_abandonada_nao_gruda_no_login_seguinte(monkeypatch):
    """Mesmo cliente: start com challenge (abandonado) e depois start SEM. O
    código novo nasce sem challenge e a troca sem verifier entra."""
    _, desafio = _par()
    uid, email = _conta()
    google_de_mentira(monkeypatch, email)
    client = TestClient(dashboard.app)
    abandonado = client.get(f"/auth/google/start?app=2&code_challenge={desafio}", follow_redirects=False)
    assert "google_oauth_challenge" in abandonado.headers.get("set-cookie", "")
    _, cb = login_google(2, client)
    code = parse_qs(urlparse(cb.headers["location"]).query)["code"][0]
    assert _troca(code).status_code == 200


# ── Compatibilidade: sem PKCE nada muda ──────────────────────────────────────

def test_c1_sem_challenge_e_sem_verifier_troca_como_antes(monkeypatch):
    uid, code = _codigo(monkeypatch, None)
    assert _troca(code).status_code == 200


def test_c2_verifier_enviado_em_codigo_sem_challenge_passa(monkeypatch):
    """Decisão: app novo × código emitido antes do deploy da PKCE."""
    uid, code = _codigo(monkeypatch, None)
    assert _troca(code, _par()[0]).status_code == 200


def test_c3_app_antigo_ignora_o_challenge_e_o_d_abre(monkeypatch):
    _, desafio = _par()
    uid, code = _codigo(monkeypatch, desafio, app=1)
    r = TestClient(dashboard.app).get(f"/d/{code}", follow_redirects=False)
    assert r.status_code == 302 and "dashboard_token=" in r.headers["set-cookie"]


def test_c4_cadastro_sem_challenge_ignora_o_verifier(monkeypatch):
    email, token = _pendente(monkeypatch, None)
    assert _completa(token, _par()[0]).status_code == 200
    assert _contas(email) == 1


def test_c5_web_nao_ganha_challenge(monkeypatch):
    """Fluxo do navegador (app=0) com `code_challenge`: ignorado, cookie de sessão normal."""
    _, desafio = _par()
    uid, email = _conta()
    google_de_mentira(monkeypatch, email)
    client = TestClient(dashboard.app)
    inicio = client.get(f"/auth/google/start?code_challenge={desafio}", follow_redirects=False)
    assert client.cookies.get("google_oauth_challenge") is None
    state = parse_qs(urlparse(inicio.headers["location"]).query)["state"][0]
    cb = client.get("/auth/google/callback", params={"code": "c", "state": state}, follow_redirects=False)
    assert cb.status_code == 302 and "auth_token=" in cb.headers["set-cookie"]
