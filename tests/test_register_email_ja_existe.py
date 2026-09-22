"""Cadastro com e-mail que JÁ tem conta — a tela precisa dizer isso.

Até 2026-09 o /auth/register respondia 200 "verification_sent" até quando o
e-mail já existia (anti-enumeração) e só avisava o dono por e-mail: o dono
legítimo caía na tela de código de 6 dígitos esperando um código que nunca
chegava, e só descobria o problema pelo e-mail "Você já tem conta". Decisão
de produto: e-mail duplicado vira 409 com mensagem clara; o aviso por e-mail
ao dono continua (se não foi ele quem tentou, ele fica sabendo); e o TELEFONE
duplicado segue sem revelação (create_email_verification_impl).

CONTROLE NEGATIVO: voltar o `return {"status": "verification_sent"}` genérico
no `except AccountAlreadyExistsError` do auth_register derruba os asserts de
409 dos dois primeiros testes.
CONTROLE POSITIVO: o terceiro teste (e-mail novo segue 200) fecha contra um
"conserto" que respondesse 409 pra tudo.
"""
import uuid

import pytest
from fastapi.testclient import TestClient

import core.services.email_service as email_service
import db
import frontend.finance_bot_websocket_custom as dashboard

CSRF = "test-csrf-regdup"


@pytest.fixture(autouse=True)
def _limpa_rate_limits():
    """Register tem teto de 3/h por IP+e-mail, persistente (auth_rate_limits) e
    em memória (slowapi). Sem limpar, a soma dos 3 testes + outros arquivos da
    suíte no mesmo banco de sessão esbarra no teto e vira 429."""
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("delete from auth_rate_limits where bucket = 'register'")
        conn.commit()
    try:
        dashboard.limiter._storage.reset()
    except Exception:
        pass


def _client() -> TestClient:
    client = TestClient(
        dashboard.app, base_url="https://testserver", raise_server_exceptions=False
    )
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, CSRF)
    return client


def _fone() -> str:
    # Telefone único por teste: a colisão de telefone muda o caminho do
    # cadastro (o número é descartado em silêncio), o que misturaria cenários.
    return "+55119" + str(uuid.uuid4().int % 100_000_000).zfill(8)


def _post_register(client: TestClient, email: str):
    return client.post(
        "/auth/register",
        json={"email": email, "password": "senhaforte123",
              "phone": _fone(), "name": "Fulano"},
        headers={dashboard.CSRF_HEADER_NAME: CSRF},
    )


def _cria_conta(email: str) -> int:
    code = db.create_email_verification(email, "senhaforte123", _fone())
    return db.confirm_email_verification(email, code)["user_id"]


def test_register_email_ja_cadastrado_409_e_dono_avisado(monkeypatch):
    email = f"dup-{uuid.uuid4().hex[:8]}@example.com"
    _cria_conta(email)
    avisos = []
    monkeypatch.setattr(
        email_service, "send_account_exists_notice",
        lambda to, login_url: avisos.append(to) or True,
    )

    r = _post_register(_client(), email)

    assert r.status_code == 409
    assert "já tem conta" in r.json()["detail"]
    assert avisos == [email]


def test_register_conta_google_409_aponta_botao_google(monkeypatch):
    email = f"dupg-{uuid.uuid4().hex[:8]}@example.com"
    uid = _cria_conta(email)
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update auth_accounts set password_hash = null where user_id = %s",
                (uid,),
            )
        conn.commit()
    monkeypatch.setattr(
        email_service, "send_account_exists_notice", lambda *a, **k: True
    )

    r = _post_register(_client(), email)

    assert r.status_code == 409
    assert "Google" in r.json()["detail"]


def test_register_email_novo_segue_200(monkeypatch):
    monkeypatch.setattr(
        email_service, "send_verification_email", lambda to, code: True
    )
    email = f"novo-{uuid.uuid4().hex[:8]}@example.com"

    r = _post_register(_client(), email)

    assert r.status_code == 200
    assert r.json()["status"] == "verification_sent"
