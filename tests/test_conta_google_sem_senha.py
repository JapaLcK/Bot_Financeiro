"""Conta criada só via Google (auth_accounts.password_hash NULL) nos 6 endpoints
que re-autenticam por senha.

Antes: todos devolviam 401 "Senha incorreta." — o usuário não tem senha nenhuma
que funcione, então a orientação era tentar pra sempre. Agora: 409 + mensagem
mandando DEFINIR a senha (o fluxo de reset por e-mail já grava password_hash
partindo de NULL).

Os 6: POST /auth/account/export, POST /auth/mfa/setup, POST /auth/mfa/disable,
POST /auth/mfa/regenerate-backup-codes, POST /settings/reset, DELETE /auth/account.

CONTROLES DO GRUPO (§3 do CLAUDE.md). Cada mutação abaixo foi APLICADA e a suíte
deste arquivo rodada inteira — os números são a saída do pytest, não estimativa.
Baseline sem mutação: 7 passed.

  negativo A — em db/privacy.py, trocando o `raise PasswordNotSetError` de
    verify_user_password de volta pelo `return False` antigo: 4 vermelhos, 3
    verdes. Muda de status SÓ os 5 endpoints que passam por ela (409 → 401
    "Senha incorreta."); `test_delete_account_...` continua VERDE, porque o
    DELETE tem select próprio e nunca chama verify_user_password.
  negativo B — removendo SÓ a guarda `if not account["password_hash"]` de
    schedule_account_deletion (o segundo caminho de código): 4 vermelhos, 3
    verdes — NÃO é um vermelho sozinho. O que B discrimina é o inverso de A:
    só o DELETE muda de status (401 "Senha incorreta." quando o campo tem
    conteúdo, 400 quando é ""), e os 5 de verify_user_password seguem em 409
    nos três testes que os medem. A e B juntos são a prova de que os dois
    caminhos de código são independentes: cada um deixa verde o teste que o
    outro derruba.
  negativo C — devolvendo o `if not password` para a PRIMEIRA linha de
    verify_user_password (e o `raise ValueError` para a primeira de
    schedule_account_deletion): 2 vermelhos, 5 verdes.
    `test_campo_de_senha_vazio_...[""]` cai com 401 nos 5 e 400 no DELETE, e
    `test_verify_user_password_...` cai no `pytest.raises` do campo vazio com
    password_hash NULL. O param `["   "]` fica VERDE e não discrimina C:
    `not "   "` é False em Python, então ele nunca chega na checagem de campo
    vazio — contra C ele é uma repetição do negativo A. Fica no arquivo assim
    mesmo, porque é ele que documenta que espaço em branco é truthy: o "campo
    vazio" do usuário e o do Python não são o mesmo conjunto.
  positivo — `test_conta_com_senha_...` e
    `test_campo_de_senha_vazio_numa_conta_COM_senha_...` ficaram VERDES nas
    TRÊS mutações, que é o trabalho deles: não medem o conserto, medem que ele
    não fechou porta nenhuma. Com senha de verdade os 6 continuam aceitando a
    certa e recusando a errada com 401 "Senha incorreta."; campo vazio em conta
    COM senha continua 401/400, nunca 409 nem 2xx. Sem eles o grupo passaria num
    código que recusasse todo mundo com 409.
"""
from __future__ import annotations

import os
import uuid

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

os.environ.setdefault("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())

import frontend.finance_bot_websocket_custom as dashboard  # noqa: E402
from db.connection import get_conn  # noqa: E402
from db.privacy import PASSWORD_NOT_SET_MSG, PasswordNotSetError, verify_user_password  # noqa: E402

SENHA = "senha-certa-123"


@pytest.fixture(autouse=True)
def _zera_rate_limit():
    """Os endpoints têm tetos baixos por IP (export 3/hora, regenerate 3/hora) e
    o TestClient é sempre o mesmo IP: sem zerar entre testes, o 4º POST levaria
    429 no lugar do status medido. Mesmo padrão de tests/test_account_reset.py."""
    from frontend.routes import shared as routes_shared

    routes_shared.limiter.reset()
    yield


@pytest.fixture(autouse=True)
def _sem_email_de_verdade(monkeypatch):
    """Os caminhos de SUCESSO do export e da exclusão disparam e-mail."""
    import core.services.email_service as email_service

    for nome in (
        "send_data_export_link_email",
        "send_data_export_completed_email",
        "send_account_deletion_scheduled_email",
    ):
        monkeypatch.setattr(email_service, nome, lambda *a, **kw: True, raising=False)
    yield


def _semeia_conta(uid: int, senha: str | None) -> str:
    """auth_accounts do usuário. senha=None → password_hash NULL (só Google)."""
    from db.users import _hash_password

    email = f"google-{uid}-{uuid.uuid4().hex[:6]}@t.local"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into auth_accounts (user_id, email, password_hash, plan) "
                "values (%s, %s, %s, 'pro')",
                (uid, email, _hash_password(senha) if senha else None),
            )
        conn.commit()
    return email


def _auth(client: TestClient, uid: int, email: str) -> dict:
    client.cookies.set(dashboard.AUTH_COOKIE_NAME, dashboard._make_jwt(uid, email))
    client.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, dashboard.make_dashboard_token(uid, hours=1))
    token = "test-csrf-token"
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, token)
    return {dashboard.CSRF_HEADER_NAME: token, "Content-Type": "application/json"}


def _chamadas(client: TestClient, headers: dict, senha: str):
    """Os 6 endpoints, com o DELETE por último (ele agenda exclusão e os outros
    passam a devolver 403 depois disso)."""
    corpo = {"password": senha, "code": "000000"}
    return [
        ("POST /auth/account/export",
         lambda: client.post("/auth/account/export", json=corpo, headers=headers)),
        ("POST /auth/mfa/setup",
         lambda: client.post("/auth/mfa/setup", json=corpo, headers=headers)),
        ("POST /auth/mfa/disable",
         lambda: client.post("/auth/mfa/disable", json=corpo, headers=headers)),
        ("POST /auth/mfa/regenerate-backup-codes",
         lambda: client.post("/auth/mfa/regenerate-backup-codes", json=corpo, headers=headers)),
        ("POST /settings/reset",
         lambda: client.post("/settings/reset", json=corpo, headers=headers)),
        ("DELETE /auth/account",
         lambda: client.request("DELETE", "/auth/account", json=corpo, headers=headers)),
    ]


# ── negativo do produto: conta SEM senha ─────────────────────────────────────

def test_seis_endpoints_sem_senha_devolvem_409_orientando_definir_senha(user_id):
    email = _semeia_conta(user_id, None)
    client = TestClient(dashboard.app)
    headers = _auth(client, user_id, email)

    for nome, chamada in _chamadas(client, headers, "qualquer-coisa"):
        resp = chamada()
        assert resp.status_code == 409, f"{nome} devolveu {resp.status_code}: {resp.text}"
        detail = resp.json().get("detail", "")
        # A mensagem tem de EXPLICAR (usuário já autenticado como ele mesmo, não
        # há e-mail alheio a enumerar) e não pode repetir a mentira antiga.
        assert "Senha incorreta." not in detail, f"{nome} ainda fala em senha incorreta: {detail}"
        assert detail == PASSWORD_NOT_SET_MSG, f"{nome}: {detail}"


def test_delete_account_sem_senha_e_o_segundo_caminho_de_codigo(user_id):
    """schedule_account_deletion NÃO passa por verify_user_password: tem select
    próprio e chamava _check_password(senha, None). Discrimina a guarda dele."""
    email = _semeia_conta(user_id, None)
    client = TestClient(dashboard.app)
    headers = _auth(client, user_id, email)

    resp = client.request("DELETE", "/auth/account", json={"password": "x"}, headers=headers)

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == PASSWORD_NOT_SET_MSG
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select deletion_status from auth_accounts where user_id = %s", (user_id,))
            row = cur.fetchone()
        conn.commit()
    assert not row["deletion_status"] or row["deletion_status"] == "none", \
        "recusa por falta de senha não podia ter agendado a exclusão"


@pytest.mark.parametrize("campo_vazio", ["", "   "])
def test_campo_de_senha_vazio_numa_conta_sem_senha_tambem_da_409(user_id, campo_vazio):
    """O estado da conta não depende do que o cliente mandou.

    Enquanto o `if not password` era a PRIMEIRA linha de verify_user_password e
    de schedule_account_deletion, o mesmo estado (conta só-Google) devolvia TRÊS
    status diferentes: 401 "Senha incorreta." nos 5 que passam por verify (a
    mentira que este PR mata) e 400 "Informe sua senha" no DELETE. Alcançável
    por chamada direta à API — os abridores do settings.html barram antes, mas
    a API é pública e a mensagem enganava quem integra.
    """
    email = _semeia_conta(user_id, None)
    client = TestClient(dashboard.app)
    headers = _auth(client, user_id, email)

    # Os 6 de uma vez: a falha mostra o MAPA inteiro (era {400, 401}), que é o
    # que denuncia "três status para o mesmo estado".
    respostas = [(nome, chamada()) for nome, chamada in _chamadas(client, headers, campo_vazio)]
    assert {nome: r.status_code for nome, r in respostas} == {nome: 409 for nome, _ in respostas}
    for nome, resp in respostas:
        assert resp.json().get("detail") == PASSWORD_NOT_SET_MSG, f"{nome}: {resp.text}"


def test_campo_de_senha_vazio_numa_conta_COM_senha_continua_recusando(user_id):
    """Positivo do conserto acima: mover a checagem de campo vazio para depois
    do estado da conta não pode ter aberto porta nenhuma. Quem TEM senha e manda
    o campo vazio continua recusado — nunca 409 (não é falta de senha) e nunca
    2xx. 401 nos 5 de verify_user_password, 400 no DELETE (o ValueError dele)."""
    email = _semeia_conta(user_id, SENHA)
    client = TestClient(dashboard.app)
    headers = _auth(client, user_id, email)

    esperado = {"DELETE /auth/account": 400}
    for nome, chamada in _chamadas(client, headers, ""):
        resp = chamada()
        assert resp.status_code == esperado.get(nome, 401), \
            f"{nome} devolveu {resp.status_code}: {resp.text}"

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select deletion_status from auth_accounts where user_id = %s", (user_id,))
            row = cur.fetchone()
        conn.commit()
    assert not row["deletion_status"] or row["deletion_status"] == "none", \
        "recusa por campo vazio não podia ter agendado a exclusão"


def test_verify_user_password_separa_sem_senha_de_senha_errada(user_id):
    _semeia_conta(user_id, SENHA)
    assert verify_user_password(user_id, SENHA) is True
    assert verify_user_password(user_id, "errada") is False   # contrato bool preservado
    assert verify_user_password(user_id, "") is False         # campo vazio, conta COM senha

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("update auth_accounts set password_hash = null where user_id = %s", (user_id,))
        conn.commit()
    with pytest.raises(PasswordNotSetError):
        verify_user_password(user_id, "qualquer")
    with pytest.raises(PasswordNotSetError):
        verify_user_password(user_id, "")   # o vazio não pode encobrir o estado da conta


# ── positivo do grupo: conta COM senha continua como sempre ──────────────────

def test_conta_com_senha_aceita_a_certa_e_recusa_a_errada_com_401(user_id):
    email = _semeia_conta(user_id, SENHA)
    client = TestClient(dashboard.app)
    headers = _auth(client, user_id, email)

    # Senha ERRADA → 401 "Senha incorreta." nos 6 (nunca 409: o 409 é só falta
    # de senha). Sem este bloco o grupo passaria num código que recusa tudo.
    for nome, chamada in _chamadas(client, headers, "senha-errada"):
        resp = chamada()
        assert resp.status_code == 401, f"{nome} devolveu {resp.status_code}: {resp.text}"
        assert resp.json()["detail"] == "Senha incorreta.", nome

    # Senha CERTA → passa da re-autenticação nos 6 (nem 401 nem 409 nem 500).
    for nome, chamada in _chamadas(client, headers, SENHA):
        resp = chamada()
        assert resp.status_code not in (401, 409, 500), \
            f"{nome} barrou a senha certa: {resp.status_code} {resp.text}"

    # E a exclusão (última da lista) foi de fato agendada com a senha certa.
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select deletion_status from auth_accounts where user_id = %s", (user_id,))
            row = cur.fetchone()
        conn.commit()
    assert row["deletion_status"] == "scheduled"
