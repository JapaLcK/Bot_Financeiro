"""Google no app nativo — `POST /auth/google/exchange` (item 6 da Fase 3).

A troca consome o código que o callback `nat-` devolve e entrega a sessão
pelo mesmo final do `/auth/login` (`_concluir_login`): exclusão agendada →
MFA → tokens no corpo para o app. Banco e rotas reais; só o Google é dublê.

Controle negativo (medido; comando e resultado no corpo do PR):
`_entrega_sessao` forçado pelo ramo do navegador → B6 vermelho (e o B10, no
arquivo do cadastro); aceitar o código sem consumir → B7 vermelho; tirar o
ramo do MFA do `_concluir_login` → B8 vermelho.
Controle positivo: B-E2E e B6 (a troca legítima entra), e a suíte de login e
MFA (`test_mfa.py`, `test_auth_app_*.py`), que prova a extração do final comum.
"""
import os
import uuid
from urllib.parse import parse_qs, urlparse

import pyotp
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

# Mesmo motivo do `tests/test_mfa.py`: o módulo de MFA cacheia o Fernet.
os.environ.setdefault("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())

import db
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import google_de_mentira, login_google

SENHA = "senha-forte-123"
UA_APP = "PigBankApp/0.1.0 (iPhone 17 Pro; iOS 26.1)"
TOKENS = ("access_token", "refresh_token", "dashboard_token")
COOKIES_DE_SESSAO = ("auth_token", "dashboard_token", "refresh_token")


def _conta() -> tuple[int, str]:
    email = f"troca-app-{uuid.uuid4().hex[:10]}@example.com"
    return int(db.register_auth_user(email, SENHA)["user_id"]), email


def _troca(code: str):
    """Como o app chama: header do app, UA do app, cookie jar vazio."""
    return TestClient(dashboard.app).post(
        "/auth/google/exchange",
        headers={dashboard.APP_CLIENT_HEADER: "app", "User-Agent": UA_APP},
        json={"code": code},
    )


def _set_cookies(resposta) -> list[str]:
    return [c.split("=", 1)[0] for c in resposta.headers.get_list("set-cookie")]


def _sem_tokens(r) -> None:
    corpo = r.json()
    for chave in TOKENS:
        assert chave not in corpo, f"{chave} saiu numa troca recusada"
    for cookie in COOKIES_DE_SESSAO:
        assert cookie not in _set_cookies(r)


def test_b_e2e_start_callback_troca_e_auth_me(monkeypatch):
    """A conversa inteira: start → callback (state do cookie) → troca → Bearer."""
    _, email = _conta()
    google_de_mentira(monkeypatch, email)
    _, callback = login_google(2)
    assert callback.status_code == 302, callback.text
    code = parse_qs(urlparse(callback.headers["location"]).query)["code"][0]

    r = _troca(code)
    assert r.status_code == 200, r.text
    me = TestClient(dashboard.app).get(
        "/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"}
    )
    assert me.status_code == 200, me.text
    assert me.json()["email"] == email


def test_b6_troca_valida_entrega_tokens_no_corpo_com_ua_do_app():
    uid, email = _conta()
    r = _troca(db.create_dashboard_session(uid, 5 / 60))
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["user_id"] == uid and corpo["email"] == email
    assert corpo["refresh_token"].startswith("rt_")
    assert corpo["access_token"] and corpo["dashboard_token"]
    for cookie in COOKIES_DE_SESSAO:
        assert cookie not in _set_cookies(r), f"{cookie} foi mandado para o app"
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select user_agent from auth_sessions where user_id = %s", (uid,)
        )
        assert [row["user_agent"] for row in cur.fetchall()] == [UA_APP]


def _codigo(caso: str) -> str:
    uid, _ = _conta()
    if caso == "repetido":
        code = db.create_dashboard_session(uid, 5 / 60)
        assert _troca(code).status_code == 200
        return code
    if caso == "vencido":
        return db.create_dashboard_session(uid, -1 / 60)
    return "codigo-que-nao-existe"


@pytest.mark.parametrize("caso", ["repetido", "vencido", "lixo"])
def test_b7_codigo_invalido_da_400_sem_tokens(caso):
    r = _troca(_codigo(caso))
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "google_code_invalid"
    _sem_tokens(r)


def test_b7_codigo_com_nul_e_recusado_na_borda_sem_tokens():
    """NUL morre no `_CorpoSemVeneno` (422), como em toda rota anônima (#369)."""
    r = _troca("abc\x00def")
    assert r.status_code == 422, r.text
    _sem_tokens(r)


def test_b8_conta_com_mfa_pede_o_desafio_e_o_verify_login_completa():
    uid, email = _conta()
    secret = db.mfa_setup_secret(uid, email)["secret"]
    db.mfa_verify_and_enable(uid, pyotp.TOTP(secret).now())

    r = _troca(db.create_dashboard_session(uid, 5 / 60))
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["mfa_required"] is True and corpo["mfa_challenge"]
    _sem_tokens(r)

    fim = TestClient(dashboard.app).post(
        "/auth/mfa/verify-login",
        headers={dashboard.APP_CLIENT_HEADER: "app", "User-Agent": UA_APP},
        json={"challenge": corpo["mfa_challenge"], "code": pyotp.TOTP(secret).now()},
    )
    assert fim.status_code == 200, fim.text
    assert fim.json()["user_id"] == uid and fim.json()["access_token"]


def test_b9_conta_com_exclusao_agendada_nao_troca():
    """Agendar apaga os códigos que já existiam (`db/privacy.py`), e o callback
    recusa antes de criar um; o código aqui nasce DEPOIS do agendamento (um do
    bot, por exemplo), senão o 400 viria por outro motivo e o teste não mediria
    a guarda da troca."""
    uid, _ = _conta()
    db.schedule_account_deletion(uid, SENHA)
    r = _troca(db.create_dashboard_session(uid, 5 / 60))
    assert r.status_code == 403, r.text
    _sem_tokens(r)
