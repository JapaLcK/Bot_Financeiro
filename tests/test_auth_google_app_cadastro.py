"""Conta nova pelo Google — `POST /auth/google/complete-signup` e a #585.

Telefone de outra conta não enumera mais: o número é descartado em silêncio e
o cadastro segue (igual ao register). Na corrida (outra conta grava o número
entre a busca e o INSERT), `gravar_descartando_telefone_disputado`
(`db_support.py`) desfaz e grava sem telefone, em vez do 500.

Pré-cadastro real (`create_pending_google_signup`), rota real, banco real.

Controle negativo (medido; comando e resultado no corpo do PR): voltar o
`raise ValueError` no SELECT → G2 vermelho; tirar o `except` do helper → G3
vermelho (e o B11 de `test_auth_app_cadastro.py`); `except UniqueViolation`
genérico → G4 vermelho; `_entrega_sessao` pelo ramo do navegador → B10 vermelho.
Controle positivo: G1 (telefone livre segue gravado).
"""
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

import db
import db.google_auth as db_google_auth
import db_support
import frontend.finance_bot_websocket_custom as dashboard
from core.crypto import hash_pii_optional
from core.services import email_service
from utils_phone import normalize_phone_e164

UA_APP = "PigBankApp/0.1.0 (iPhone 17 Pro; iOS 26.1)"
COOKIES_DE_SESSAO = ("auth_token", "dashboard_token", "refresh_token")


@pytest.fixture
def correio(monkeypatch):
    """Nenhum e-mail pode sair do cadastro pelo Google, nem o de aviso."""
    enviados = []
    for nome in ("send_verification_email", "send_welcome_email", "send_account_exists_notice"):
        monkeypatch.setattr(email_service, nome, lambda to, *a, _n=nome, **k: enviados.append((_n, to)) or True)
    return enviados


def _email() -> str:
    return f"google-cad-{uuid.uuid4().hex[:10]}@example.com"


def _telefone() -> str:
    return f"55119{uuid.uuid4().int % 100_000_000:08d}"


def _dono_do(telefone: str) -> str:
    """Outra conta, criada pelo register, que já tem `telefone`."""
    email = _email()
    db.confirm_email_verification(email, db.create_email_verification(email, "senha-forte-123", telefone))
    return email


def _complete_signup(telefone: str, email: str | None = None):
    email = email or _email()
    token = db.create_pending_google_signup(f"sub-{email}", email, "Fulana")
    r = TestClient(dashboard.app).post(
        "/auth/google/complete-signup",
        headers={dashboard.APP_CLIENT_HEADER: "app", "User-Agent": UA_APP},
        json={"token": token, "name": "Fulana", "phone": telefone, "accepted_terms": True},
    )
    return r, email


def _conta(email: str) -> dict:
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select phone_e164, phone_hash, phone_enc, phone_status, signup_source"
            " from auth_accounts where email_hash = %s",
            (hash_pii_optional(email, kind="email"),),
        )
        return cur.fetchone()


def test_b10_complete_signup_do_app_entrega_tokens_sem_cookie():
    r, email = _complete_signup(_telefone())
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["access_token"] and corpo["refresh_token"].startswith("rt_")
    enviados = [c.split("=", 1)[0] for c in r.headers.get_list("set-cookie")]
    for cookie in COOKIES_DE_SESSAO:
        assert cookie not in enviados, f"{cookie} foi mandado para o app"
    assert _conta(email)["signup_source"] == "google_app"


def test_g1_telefone_livre_e_gravado(correio):
    telefone = _telefone()
    r, email = _complete_signup(telefone)
    assert r.status_code == 200, r.text
    assert r.json()["access_token"]
    assert _conta(email)["phone_e164"] == normalize_phone_e164(telefone)


def test_g2_telefone_de_outra_conta_e_descartado_sem_enumerar(correio):
    telefone = _telefone()
    dono = _dono_do(telefone)
    livre, _ = _complete_signup(_telefone())
    correio.clear()

    r, email = _complete_signup(telefone)
    assert r.status_code == 200, r.text
    assert set(r.json()) == set(livre.json())
    assert "em uso" not in r.text
    conta = _conta(email)
    assert (conta["phone_e164"], conta["phone_hash"], conta["phone_enc"]) == (None, None, None)
    assert conta["phone_status"] == "pending"
    assert _conta(dono)["phone_e164"] == normalize_phone_e164(telefone)
    assert correio == []


@pytest.mark.parametrize("indice", ["phone_e164", "phone_hash"])
def test_g3_corrida_no_insert_descarta_o_telefone_em_vez_de_500(correio, monkeypatch, indice):
    """A busca não acha (a outra conta "ainda não tinha gravado") e o INSERT
    bate no índice único: é a corrida, reproduzida sem thread. São dois índices
    de telefone; com o `phone_e164` do dono apagado, quem acusa é o do hash."""
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select indexname from pg_indexes where tablename = 'auth_accounts'"
            " and indexname = any(%s)",
            (list(db_support.INDICES_TELEFONE_UNICO),),
        )
        achados = {row["indexname"] for row in cur.fetchall()}
    assert achados == set(db_support.INDICES_TELEFONE_UNICO), "índice renomeado no db/schema.py"
    telefone = _telefone()
    dono = _dono_do(telefone)
    if indice == "phone_hash":
        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "update auth_accounts set phone_e164 = null where email_hash = %s",
                (hash_pii_optional(dono, kind="email"),),
            )
            conn.commit()
    monkeypatch.setattr(db_google_auth, "phone_lookup_candidates", lambda p: [])

    r, email = _complete_signup(telefone)
    assert r.status_code == 200, r.text
    assert r.json()["access_token"]
    assert _conta(email)["phone_hash"] is None
    assert _conta(dono)["phone_hash"] == hash_pii_optional(normalize_phone_e164(telefone), kind="phone")


def test_g4_unique_violation_de_outra_constraint_sobe():
    """Positivo do filtro: só o índice do telefone é engolido."""
    uid = int(db.register_auth_user(_email(), "senha-forte-123")["user_id"])
    codigo = db.create_dashboard_session(uid, 5 / 60)
    chamadas = []

    def gravar(telefone):
        chamadas.append(telefone)
        with conn.cursor() as cur:
            cur.execute(
                "insert into dashboard_sessions (code, user_id, expires_at) values (%s, %s, now())",
                (codigo, uid),
            )

    with db.get_conn() as conn:
        with pytest.raises(psycopg.errors.UniqueViolation):
            db_support.gravar_descartando_telefone_disputado(conn, gravar, "+5511999990000")
        conn.rollback()
    assert chamadas == ["+5511999990000"]
