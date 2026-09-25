"""Criar conta pelo app: `register` → `verify-email`, pelas rotas reais.

Os testes de rotação/replay de `test_auth_app_refresh.py` usam sessão emitida
direto (`_issue_session_token`) e nunca passavam pelo `verify-email`; aqui a
sessão é a que o cadastro entrega. O e-mail é o único mock: a função de envio
captura o código, o resto (banco, rate limit, emissão de sessão) é o de verdade.

Todo cadastro manda um telefone válido e distinto: um número repetido seria
descartado em silêncio (anti-enumeração) e mudaria o que o teste prova — só o
B9 repete de propósito, porque é esse descarte que ele prova.

Controle negativo do grupo: forçar `_entrega_sessao` a ir sempre pelo ramo do
navegador deixa B1, B3, B4 e B5 vermelhos; pular a busca por `phone_hash` deixa
B9 vermelho (medido em 2026-09-25). Controle positivo: B2 (o navegador segue
recebendo cookie e nenhum token no corpo).
"""
import uuid

import pytest
from fastapi.testclient import TestClient

import db
import db_support
import frontend.finance_bot_websocket_custom as dashboard
from core.crypto import hash_pii_optional
from core.services import email_service
from utils_phone import normalize_phone_e164
from _apoio_auth_app import csrf, limpa_rate_limits

SENHA = "senha-forte-123"
UA_APP = "PigBankApp/0.1.0 (iPhone 17 Pro; iOS 26.1)"
COOKIES_DE_SESSAO = ("auth_token", "dashboard_token", "refresh_token")


@pytest.fixture
def correio(monkeypatch):
    """Captura o que sairia por e-mail. `codigos[email]` é o último código."""
    enviados = {"codigos": {}, "avisos": []}

    def _verificacao(to, code):
        enviados["codigos"][to] = code
        return True

    monkeypatch.setattr(email_service, "send_verification_email", _verificacao)
    monkeypatch.setattr(email_service, "send_welcome_email", lambda *a, **k: True)
    monkeypatch.setattr(
        email_service,
        "send_account_exists_notice",
        lambda to, *a, **k: enviados["avisos"].append(to) or True,
    )
    return enviados


def _email() -> str:
    email = f"cadastro-app-{uuid.uuid4().hex[:10]}@example.com"
    limpa_rate_limits("register", email)
    limpa_rate_limits("verify-email", email)
    return email


def _telefone() -> str:
    return f"55119{uuid.uuid4().int % 100_000_000:08d}"


def _cabecalhos_app() -> dict[str, str]:
    return {dashboard.APP_CLIENT_HEADER: "app", "User-Agent": UA_APP}


def _cadastro_do_app(correio, email: str, phone: str | None = None):
    """Register + verify como o app: header do app, cookie jar vazio."""
    client = TestClient(dashboard.app)
    r = client.post(
        "/auth/register",
        headers=_cabecalhos_app(),
        json={
            "email": email, "password": SENHA,
            "phone": phone or _telefone(), "name": "Fulana",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "verification_sent", "email": email}
    client.cookies.clear()  # o app não guarda cookie; o verify tem de ver jar vazio
    return client.post(
        "/auth/verify-email",
        headers=_cabecalhos_app(),
        json={"email": email, "code": correio["codigos"][email]},
    )


def _refresh_do_app(token: str):
    return TestClient(dashboard.app).post(
        "/auth/refresh",
        headers={
            "Authorization": f"Bearer {token}",
            dashboard.APP_CLIENT_HEADER: "app",
            "Content-Type": "application/json",
        },
    )


def _conta(email: str) -> dict:
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select user_id, phone_e164, signup_source from auth_accounts"
            " where email_hash = %s",
            (hash_pii_optional(email, kind="email"),),
        )
        return cur.fetchone()


def _set_cookies(resposta) -> list[str]:
    return [c.split("=", 1)[0] for c in resposta.headers.get_list("set-cookie")]


# ── B1–B5: a sessão que o cadastro entrega ao app ────────────────────────────

def test_b1_verify_do_app_entrega_credenciais_no_corpo_sem_cookie(correio):
    r = _cadastro_do_app(correio, _email())
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["access_token"]
    assert corpo["refresh_token"].startswith("rt_")
    assert corpo["dashboard_token"]
    assert corpo["expires_in"] == dashboard.AUTH_COOKIE_MAX_AGE
    for cookie in COOKIES_DE_SESSAO:
        assert cookie not in _set_cookies(r), f"{cookie} foi mandado para o app"
    assert "no-store" in (r.headers.get("cache-control") or "")


def test_b2_verify_do_navegador_continua_so_com_cookies(correio):
    """Controle positivo: sem o header do app, a resposta do site não mudou."""
    email = _email()
    client = TestClient(dashboard.app)
    cabecalhos = csrf(client)
    r = client.post(
        "/auth/register",
        headers=cabecalhos,
        json={"email": email, "password": SENHA, "phone": _telefone()},
    )
    assert r.status_code == 200, r.text
    r = client.post(
        "/auth/verify-email",
        headers=cabecalhos,
        json={"email": email, "code": correio["codigos"][email]},
    )
    assert r.status_code == 200, r.text
    for cookie in COOKIES_DE_SESSAO:
        assert cookie in _set_cookies(r), f"{cookie} sumiu do navegador"
    for chave in ("access_token", "refresh_token", "dashboard_token", "expires_in"):
        assert chave not in r.json(), f"{chave} vazou para o navegador"


def test_b3_refresh_do_cadastro_rotaciona(correio):
    velho = _cadastro_do_app(correio, _email()).json()["refresh_token"]

    r = _refresh_do_app(velho)
    assert r.status_code == 200, r.text
    novo = r.json()["refresh_token"]
    assert novo.startswith("rt_") and novo != velho
    assert _refresh_do_app(novo).status_code == 200


def test_b4_replay_do_refresh_do_cadastro_revoga_tudo(correio):
    velho = _cadastro_do_app(correio, _email()).json()["refresh_token"]

    primeira = _refresh_do_app(velho)
    assert primeira.status_code == 200, primeira.text
    legitimo = primeira.json()["refresh_token"]

    replay = _refresh_do_app(velho)
    assert replay.status_code == 401, replay.text
    assert replay.json()["detail"] == "invalid_refresh_token"
    assert _refresh_do_app(legitimo).status_code == 401


def test_b5_access_do_cadastro_abre_auth_me_e_origem_e_app(correio):
    email = _email()
    access = _cadastro_do_app(correio, email).json()["access_token"]

    r = TestClient(dashboard.app).get(
        "/auth/me", headers={"Authorization": f"Bearer {access}"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["email"] == email
    assert _conta(email)["signup_source"] == "app"


# ── B8: e-mail já cadastrado ─────────────────────────────────────────────────

@pytest.mark.parametrize("so_google", [False, True])
def test_b8_email_existente_nao_enumera(correio, so_google):
    email = _email()
    db.confirm_email_verification(
        email, db.create_email_verification(email, SENHA, _telefone())
    )
    if so_google:
        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "update auth_accounts set password_hash = null where email_hash = %s",
                (hash_pii_optional(email, kind="email"),),
            )
            conn.commit()
        db_support.invalidate_auth_user_cache(_conta(email)["user_id"])
    limpa_rate_limits("register", email)

    r = TestClient(dashboard.app).post(
        "/auth/register",
        headers=_cabecalhos_app(),
        json={"email": email, "password": SENHA, "phone": _telefone()},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "verification_sent", "email": email}
    assert correio["avisos"] == [email]
    assert email not in correio["codigos"]
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select count(*) as n from email_verification_codes"
            " where email_hash = %s and used_at is null",
            (hash_pii_optional(email, kind="email"),),
        )
        assert cur.fetchone()["n"] == 0


# ── B9: telefone de outra conta ──────────────────────────────────────────────

def test_b9_telefone_de_outra_conta_e_descartado_sem_enumerar(correio):
    """Par do B8 pelo telefone: o número já em uso é descartado em silêncio
    (`db_support.py`, busca por `phone_hash`) — mesma resposta, código enviado,
    nenhum aviso, e a conta nasce sem telefone."""
    telefone = _telefone()
    dono, email = _email(), _email()
    assert _cadastro_do_app(correio, dono, phone=telefone).status_code == 200

    r = _cadastro_do_app(correio, email, phone=telefone)  # confere o corpo do register
    assert r.status_code == 200, r.text
    assert correio["avisos"] == []
    assert _conta(email)["phone_e164"] is None
    assert _conta(dono)["phone_e164"] == normalize_phone_e164(telefone)
