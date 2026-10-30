"""Vínculo Google/Apple numa conta do quiz (sem credencial) revoga o que o criador tinha.

Ataque: A faz o quiz com o e-mail da vítima V e fica com sessão + refresh. V entra
com Google/Apple; o vínculo tira o `exigir_credencial` do caminho e a sessão de A
passaria a ver os dados de V. Banco e rotas reais; só Google/Apple são dublês.

Controle negativo (medido; resultado no relato do PR): sem `_revogar_sessoes_do_quiz`
em `_vincular_se_permitido` → testes 1, 2, 3 vermelhos; o 5 (conta com senha) e os
positivos (4) seguem verdes.
"""
import uuid
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import NONCE_CRU, apple_de_mentira, google_de_mentira, login_google, sessao_de
from test_quiz_conta import _conta_quiz, _email, _navegador, env  # noqa: F401 (env é autouse)

APP = {dashboard.APP_CLIENT_HEADER: "app", "User-Agent": "PigBankApp/0.1.0 (iPhone 17 Pro; iOS 26.1)"}


def _com_plano(uid: int) -> None:
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("update auth_accounts set plan = 'plus', plan_expires_at = now() + interval '30 days'"
                    " where user_id = %s", (uid,))
        conn.commit()


def _atacante(email: str):
    """O quiz com o e-mail da vítima: devolve (cliente com o cookie, uid, refresh)."""
    client = _navegador()
    r = _conta_quiz(client, email)
    assert r.status_code == 200 and r.json()["estado"] == "criada", r.text
    uid = int(r.json()["user_id"])
    _com_plano(uid)
    dash = client.cookies.get("dashboard_token")
    assert _dados(dash, uid) == 403  # antes do vínculo: password_required
    return dash, uid, client.cookies.get("refresh_token")


def _dados(token: str, uid: int) -> int:
    """Rota de dados com o token no Bearer (o cookie é Secure e o TestClient é http)."""
    return TestClient(dashboard.app).get(f"/data/{uid}", headers={"Authorization": f"Bearer {token}"}).status_code


def _apple(apple, email: str):
    return TestClient(dashboard.app).post(
        "/auth/apple/exchange", headers=APP,
        json={"identity_token": apple.emitir(sub=f"000{uuid.uuid4().hex[:12]}.apple", email=email),
              "nonce": NONCE_CRU},
    )


def _google(monkeypatch, email: str):
    google_de_mentira(monkeypatch, email)
    _, cb = login_google(2)
    assert cb.status_code == 302, cb.text
    code = parse_qs(urlparse(cb.headers["location"]).query).get("code", [cb.headers["location"]])[0]
    return TestClient(dashboard.app).post("/auth/google/exchange", headers=APP, json={"code": code})


def _refresh(refresh: str):
    return TestClient(dashboard.app).post("/auth/refresh", headers={**APP, "Content-Type": "application/json", "Authorization": f"Bearer {refresh}"})


def test_1_apple_revoga_a_sessao_do_quiz(monkeypatch):
    email = _email()
    atacante, uid, _ = _atacante(email)
    r = _apple(apple_de_mentira(monkeypatch), email)
    assert r.status_code == 200 and r.json()["user_id"] == uid, r.text
    assert _dados(atacante, uid) == 401


def test_2_refresh_do_quiz_e_recusado_depois_do_vinculo(monkeypatch):
    email = _email()
    _, _, refresh = _atacante(email)
    assert _apple(apple_de_mentira(monkeypatch), email).status_code == 200
    r = _refresh(refresh)
    assert r.status_code == 401, r.text


def test_3_google_revoga_a_sessao_do_quiz(monkeypatch):
    email = _email()
    atacante, uid, refresh = _atacante(email)
    r = _google(monkeypatch, email)
    assert r.status_code == 200 and r.json()["user_id"] == uid, r.text
    assert _dados(atacante, uid) == 401
    r = _refresh(refresh)
    assert r.status_code == 401, r.text


def test_4_sessao_nova_do_login_social_chega_aos_dados(monkeypatch):
    email = _email()
    _, uid, _ = _atacante(email)
    r = _apple(apple_de_mentira(monkeypatch), email)
    assert r.status_code == 200, r.text
    assert _dados(r.json()["dashboard_token"], uid) == 200


def test_5_conta_com_senha_mantem_a_sessao_anterior(monkeypatch):
    email = _email()
    uid = int(db.register_auth_user(email, "senha-forte-123")["user_id"])
    _com_plano(uid)
    antes = sessao_de(uid)["dashboard"]
    assert _dados(antes, uid) == 200
    assert _google(monkeypatch, email).status_code == 200
    assert _dados(antes, uid) == 200


def test_6_revogacao_que_nao_pega_falha_fechada(monkeypatch):
    """Revogação engole erro e devolve 0 sem revogar: não pode vincular, e a sessão
    do quiz segue barrada (sem vínculo, `exigir_credencial` continua valendo)."""
    import core.sessions
    monkeypatch.setattr(core.sessions, "revoke_other_sessions", lambda *a, **k: 0)
    email = _email()
    atacante, uid, _ = _atacante(email)
    _apple(apple_de_mentira(monkeypatch), email)
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) as n from auth_identities where user_id = %s", (uid,))
        assert cur.fetchone()["n"] == 0
    assert _dados(atacante, uid) == 403
