"""Conta nova pela Apple — `POST /auth/apple/complete-signup` e o pendente.

O pendente mora na mesma `pending_google_signups`, com `provider='apple'`, e o
corpo da rota é o do Google (`_completar_cadastro_social`). Banco e rotas
reais; só a rede da Apple é dublê.

Controle negativo (medido; comando e resultado no corpo do PR): tirar o filtro
de `provider` do `get_pending_google_signup` → A11 vermelho; tirar a guarda de
e-mail do `consume_pending_google_signup` → C2 vermelho (e o G5, no arquivo do
Google); tirar a herança do nome → A10 vermelho.
Controle positivo: C1 (o cadastro legítimo entra).
"""
import uuid

import pytest
from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import NONCE_CRU, apple_de_mentira
from core.crypto import hash_pii_optional

UA_APP = "PigBankApp/0.1.0 (iPhone 17 Pro; iOS 26.1)"
APP = {dashboard.APP_CLIENT_HEADER: "app", "User-Agent": UA_APP}
TOKENS = ("access_token", "refresh_token", "dashboard_token")


@pytest.fixture
def apple(monkeypatch):
    return apple_de_mentira(monkeypatch)


def _email() -> str:
    return f"apple-cad-{uuid.uuid4().hex[:10]}@example.com"


def _telefone() -> str:
    return f"55119{uuid.uuid4().int % 100_000_000:08d}"


def _troca(apple, email: str, sub: str, **extra):
    return TestClient(dashboard.app).post(
        "/auth/apple/exchange",
        headers=APP,
        json={"identity_token": apple.emitir(sub=sub, email=email), "nonce": NONCE_CRU, **extra},
    )


def _completa(provedor: str, token: str, telefone: str | None = None, **extra):
    return TestClient(dashboard.app).post(
        f"/auth/{provedor}/complete-signup",
        headers=APP,
        json={"token": token, "name": "Fulana Apple", "phone": telefone or _telefone(),
              "accepted_terms": True, **extra},
    )


def _conta(email: str) -> dict | None:
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select user_id, phone_e164, signup_source from auth_accounts where email_hash = %s",
            (hash_pii_optional(email, kind="email"),),
        )
        return cur.fetchone()


def _provedores(user_id: int) -> list[str]:
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select provider from auth_identities where user_id = %s", (user_id,))
        return sorted(row["provider"] for row in cur.fetchall())


def test_c1_cadastro_entrega_tokens_sem_cookie_com_origem_e_identidade_apple(apple):
    email = _email()
    token = _troca(apple, email, f"sub-{email}").json()["signup_token"]
    r = _completa("apple", token)
    assert r.status_code == 200, r.text
    assert all(r.json()[chave] for chave in TOKENS)
    assert r.headers.get_list("set-cookie") == []
    conta = _conta(email)
    assert conta["signup_source"] == "apple_app"
    assert _provedores(conta["user_id"]) == ["apple"]


def test_a10_nome_da_primeira_autorizacao_sobrevive_a_segunda(apple):
    email = _email()
    primeira = _troca(apple, email, f"sub-{email}", name="Fulana Apple")
    assert primeira.json()["name_hint"] == "Fulana Apple"
    segunda = _troca(apple, email, f"sub-{email}")
    assert segunda.status_code == 200, segunda.text
    assert segunda.json()["name_hint"] == "Fulana Apple"
    assert segunda.json()["signup_token"] != primeira.json()["signup_token"]


def test_a11_pendente_de_um_provedor_nao_serve_no_outro(apple):
    email = _email()
    token_apple = _troca(apple, email, f"sub-{email}").json()["signup_token"]

    r = _completa("google", token_apple)
    assert r.status_code == 400, r.text
    assert r.json()["detail"].startswith("Cadastro expirado")
    pendente = TestClient(dashboard.app).get(f"/auth/google/pending/{token_apple}")
    assert pendente.status_code == 404, pendente.text

    outro = _email()
    token_google = db.create_pending_google_signup(f"sub-{outro}", outro, "Fulana")
    r = _completa("apple", token_google)
    assert r.status_code == 400, r.text
    assert r.json()["detail"].startswith("Cadastro expirado")
    assert _conta(email) is None and _conta(outro) is None


def test_c2_email_que_ganhou_conta_no_meio_nao_funde(apple):
    """P7: sem a guarda, o `on conflict (email)` abria sessão na conta que
    surgiu, sem MFA. Recomeçar leva ao vínculo, que passa pelo `_concluir_login`."""
    email = _email()
    token = _troca(apple, email, f"sub-{email}").json()["signup_token"]
    uid = int(db.register_auth_user(email, "senha-forte-123")["user_id"])

    r = _completa("apple", token)
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "Cadastro expirado. Entre com a Apple de novo."
    assert not any(chave in r.json() for chave in TOKENS)
    assert _provedores(uid) == []
    assert _completa("apple", token).json()["detail"].startswith("Cadastro expirado")


def test_c3_telefone_de_outra_conta_e_descartado_em_silencio(apple):
    telefone = _telefone()
    dono = _email()
    db.confirm_email_verification(dono, db.create_email_verification(dono, "senha-forte-123", telefone))

    email = _email()
    token = _troca(apple, email, f"sub-{email}").json()["signup_token"]
    r = _completa("apple", token, telefone)
    assert r.status_code == 200, r.text
    assert "em uso" not in r.text
    assert _conta(email)["phone_e164"] is None


def test_c4_sem_aceitar_os_termos_da_400(apple):
    email = _email()
    token = _troca(apple, email, f"sub-{email}").json()["signup_token"]
    r = _completa("apple", token, accepted_terms=False)
    assert r.status_code == 400, r.text
    assert _conta(email) is None
