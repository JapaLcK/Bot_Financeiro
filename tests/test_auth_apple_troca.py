"""Apple no app nativo — `POST /auth/apple/exchange` (item 7 da Fase 3).

Banco e rotas reais; só a rede da Apple é dublê (`apple_de_mentira`), e a
verificação do token roda de verdade em toda requisição.

Controle negativo (medido; comando e resultado no corpo do PR): tirar a
checagem do nonce → A4 vermelho; `verify_aud: False` → A4; buscar a conta
pelo `sub` antes do `jwt.decode` → A4; inverter a ordem dos `except` → A9.
Controle positivo: A-E2E, A1 e A6 (as entradas legítimas passam).
P4 ampliada: voltar a não vincular o relay → A6c vermelho; vincular sem checar
`email_verified` → A6b vermelho.
"""
import os
import uuid

import pyotp
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

# Mesmo motivo do `tests/test_mfa.py`: o módulo de MFA cacheia o Fernet.
os.environ.setdefault("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())

import db
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import APAGA, NONCE_CRU, apple_de_mentira

SENHA = "senha-forte-123"
UA_APP = "PigBankApp/0.1.0 (iPhone 17 Pro; iOS 26.1)"
TOKENS = ("access_token", "refresh_token", "dashboard_token")
COOKIES_DE_SESSAO = ("auth_token", "dashboard_token", "refresh_token")
APP = {dashboard.APP_CLIENT_HEADER: "app", "User-Agent": UA_APP}


@pytest.fixture
def apple(monkeypatch):
    return apple_de_mentira(monkeypatch)


def _email() -> str:
    return f"apple-troca-{uuid.uuid4().hex[:10]}@example.com"


def _sub() -> str:
    return f"000{uuid.uuid4().hex[:12]}.apple"


def _conta(email: str | None = None) -> tuple[int, str]:
    email = email or _email()
    return int(db.register_auth_user(email, SENHA)["user_id"]), email


def _com_mfa(uid: int, email: str) -> str:
    secret = db.mfa_setup_secret(uid, email)["secret"]
    db.mfa_verify_and_enable(uid, pyotp.TOTP(secret).now())
    return secret


def _troca(token: str, **extra):
    """Como o app chama: header do app, UA do app, cookie jar vazio."""
    corpo = {"identity_token": token, "nonce": NONCE_CRU, **extra}
    return TestClient(dashboard.app).post("/auth/apple/exchange", headers=APP, json=corpo)


def _set_cookies(resposta) -> list[str]:
    return [c.split("=", 1)[0] for c in resposta.headers.get_list("set-cookie")]


def _sem_tokens(r) -> None:
    corpo = r.json()
    for chave in TOKENS:
        assert chave not in corpo, f"{chave} saiu numa troca recusada"
    for cookie in COOKIES_DE_SESSAO:
        assert cookie not in _set_cookies(r)


def _identidades_apple(sub: str) -> list[int]:
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select user_id from auth_identities where provider = 'apple' and provider_sub = %s",
            (sub,),
        )
        return [int(row["user_id"]) for row in cur.fetchall()]


def _pendentes_apple(sub: str) -> int:
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select count(*) as n from pending_google_signups"
            " where provider = 'apple' and provider_sub = %s",
            (sub,),
        )
        return int(cur.fetchone()["n"])


def test_a_e2e_conta_nova_cadastro_me_e_login_direto(apple):
    email, sub = _email(), _sub()
    r = _troca(apple.emitir(sub=sub, email=email), name="Fulana Apple")
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["signup_required"] is True and corpo["email"] == email
    assert corpo["name_hint"] == "Fulana Apple"
    assert r.headers["cache-control"] == "no-store"
    _sem_tokens(r)

    fim = TestClient(dashboard.app).post(
        "/auth/apple/complete-signup",
        headers=APP,
        json={"token": corpo["signup_token"], "name": "Fulana Apple",
              "phone": f"55119{uuid.uuid4().int % 100_000_000:08d}", "accepted_terms": True},
    )
    assert fim.status_code == 200, fim.text
    me = TestClient(dashboard.app).get(
        "/auth/me", headers={"Authorization": f"Bearer {fim.json()['access_token']}"}
    )
    assert me.status_code == 200, me.text
    assert me.json()["email"] == email

    de_novo = _troca(apple.emitir(sub=sub, email=email))
    assert de_novo.status_code == 200, de_novo.text
    assert de_novo.json()["user_id"] == fim.json()["user_id"]
    assert de_novo.json()["access_token"]


def test_a1_vinculada_sem_mfa_entra_com_o_email_da_conta(apple):
    uid, email = _conta()
    sub = _sub()
    db.link_google_identity(uid, sub, email, db.PROVIDER_APPLE)

    r = _troca(apple.emitir(sub=sub, email="outro-email@privaterelay.appleid.com"))
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["user_id"] == uid and corpo["email"] == email
    assert corpo["refresh_token"].startswith("rt_") and corpo["access_token"]
    assert _set_cookies(r) == []
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select user_agent from auth_sessions where user_id = %s", (uid,))
        assert [row["user_agent"] for row in cur.fetchall()] == [UA_APP]


def test_a2_conta_com_mfa_pede_o_desafio_e_o_verify_login_completa(apple):
    uid, email = _conta()
    secret = _com_mfa(uid, email)
    sub = _sub()
    db.link_google_identity(uid, sub, email, db.PROVIDER_APPLE)

    r = _troca(apple.emitir(sub=sub, email=email))
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["mfa_required"] is True and corpo["mfa_challenge"]
    _sem_tokens(r)

    fim = TestClient(dashboard.app).post(
        "/auth/mfa/verify-login",
        headers=APP,
        json={"challenge": corpo["mfa_challenge"], "code": pyotp.TOTP(secret).now()},
    )
    assert fim.status_code == 200, fim.text
    assert fim.json()["user_id"] == uid and fim.json()["access_token"]


def test_a3_conta_com_exclusao_agendada_nao_entra(apple):
    uid, email = _conta()
    sub = _sub()
    db.link_google_identity(uid, sub, email, db.PROVIDER_APPLE)
    db.schedule_account_deletion(uid, SENHA)

    r = _troca(apple.emitir(sub=sub, email=email))
    assert r.status_code == 403, r.text
    _sem_tokens(r)


@pytest.mark.parametrize("defeito", ["aud", "nonce", "assinatura"])
def test_a4_token_ruim_de_conta_com_mfa_e_exclusao_da_400_antes_de_tudo(apple, defeito):
    """I1/I2: com a conta vinculada tendo MFA E exclusão agendada, qualquer
    decisão tomada antes da verificação apareceria como 403 ou `mfa_required`."""
    uid, email = _conta()
    _com_mfa(uid, email)
    sub = _sub()
    db.link_google_identity(uid, sub, email, db.PROVIDER_APPLE)
    db.schedule_account_deletion(uid, SENHA)

    if defeito == "aud":
        token = apple.emitir(sub=sub, email=email, aud="host.exp.Exponent")
    elif defeito == "nonce":
        token = apple.emitir(sub=sub, email=email, nonce="0" * 64)
    else:
        outra = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        token = apple.emitir(sub=sub, email=email, chave_de=outra)

    r = _troca(token)
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "apple_token_invalid"
    _sem_tokens(r)


@pytest.mark.parametrize(
    "corpo, status",
    [
        ({"identity_token": "x", "nonce": "x" * 16}, 400),
        ({"identity_token": "a.b.c", "nonce": "x" * 16}, 400),
        ({"identity_token": "abc\x00def", "nonce": "x" * 16}, 422),
        ({"identity_token": "", "nonce": "x" * 16}, 422),
        ({"identity_token": "x", "nonce": "curto"}, 422),
        ({"identity_token": "x" * 5000, "nonce": "x" * 16}, 422),
        ({"nonce": "x" * 16}, 422),
    ],
    ids=["lixo", "tres-pedacos", "nul", "vazio", "nonce-curto", "longo-demais", "sem-token"],
)
def test_a5_lixo_nunca_da_500(apple, corpo, status):
    r = TestClient(dashboard.app).post("/auth/apple/exchange", headers=APP, json=corpo)
    assert r.status_code == status, r.text
    if status == 400:
        assert r.json()["code"] == "apple_token_invalid"
    _sem_tokens(r)


def test_a6_email_real_verificado_vincula_e_entra(apple):
    uid, email = _conta()
    sub = _sub()
    r = _troca(apple.emitir(sub=sub, email=email.upper()))
    assert r.status_code == 200, r.text
    assert r.json()["user_id"] == uid and r.json()["access_token"]
    assert _identidades_apple(sub) == [uid]


def test_a6_email_real_de_conta_com_mfa_vincula_e_pede_o_desafio(apple):
    uid, email = _conta()
    _com_mfa(uid, email)
    sub = _sub()
    r = _troca(apple.emitir(sub=sub, email=email))
    assert r.status_code == 200, r.text
    assert r.json()["mfa_required"] is True
    _sem_tokens(r)
    assert _identidades_apple(sub) == [uid]


@pytest.mark.parametrize("verificado", [False, "false"], ids=["bool", "str"])
def test_a6b_nao_verificado_nunca_vincula(apple, verificado):
    uid, email = _conta()
    sub = _sub()
    r = _troca(apple.emitir(sub=sub, email=email, email_verified=verificado))
    assert r.status_code == 400, r.text
    _sem_tokens(r)
    assert _identidades_apple(sub) == []


@pytest.mark.parametrize("mfa", [False, True], ids=["sem-mfa", "com-mfa"])
def test_a6c_conta_com_email_relay_e_sub_novo_vincula(apple, mfa):
    """P4 ampliada: conta cujo e-mail é o relay e perdeu o vínculo. Antes, a
    troca devolvia `signup_required` e o complete-signup caía na P7
    ("Cadastro expirado") — um laço sem saída."""
    uid, relay = _conta(f"{uuid.uuid4().hex[:10]}@privaterelay.appleid.com")
    if mfa:
        _com_mfa(uid, relay)
    sub = _sub()
    r = _troca(apple.emitir(sub=sub, email=relay, is_private_email="true"))
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert "signup_required" not in corpo
    if mfa:
        assert corpo["mfa_required"] is True
        _sem_tokens(r)
    else:
        assert corpo["user_id"] == uid and corpo["email"] == relay and corpo["access_token"]
    assert _identidades_apple(sub) == [uid]
    assert _pendentes_apple(sub) == 0


def test_a7_relay_sem_vinculo_pede_cadastro_com_o_relay(apple):
    relay = f"{uuid.uuid4().hex[:10]}@privaterelay.appleid.com"
    sub = _sub()
    r = _troca(apple.emitir(sub=sub, email=relay, is_private_email="true"))
    assert r.status_code == 200, r.text
    assert r.json()["signup_required"] is True and r.json()["email"] == relay
    _sem_tokens(r)
    assert db.find_user_id_by_email(relay) is None
    assert _identidades_apple(sub) == []


@pytest.mark.parametrize(
    "claims",
    [{"email": APAGA}, {"email_verified": False}, {"email": ""}],
    ids=["sem-email", "nao-verificado", "email-vazio"],
)
def test_a8_sem_email_verificado_nao_grava_nada(apple, claims):
    sub = _sub()
    r = _troca(apple.emitir(sub=sub, **claims))
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "apple_sem_email"
    _sem_tokens(r)
    assert _pendentes_apple(sub) == 0


def test_a9_jwks_fora_da_503(apple):
    uid, email = _conta()
    sub = _sub()
    db.link_google_identity(uid, sub, email, db.PROVIDER_APPLE)
    apple.fora = True
    r = _troca(apple.emitir(sub=sub, email=email))
    assert r.status_code == 503, r.text
    assert r.json()["code"] == "apple_indisponivel"
    _sem_tokens(r)
