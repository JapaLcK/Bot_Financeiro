"""O link de "esqueci a senha" vale só para o e-mail para o qual foi emitido.

O token guarda o `email_hash` da conta no instante da emissão
(`create_password_reset_token_impl`, um INSERT…SELECT) e o consumo só troca a
senha com `update auth_accounts … where user_id = %s and email_hash = <do token>`.
Sem isso, quem pediu o link e depois trocou o e-mail deixava 30 min de link válido
na caixa ANTIGA: quem a lê define a senha, o reset derruba as sessões do dono, e a
conta é tomada. Comparar o hash em Python antes de gravar não basta (READ
COMMITTED): é o UPDATE condicional que pega a trava da linha e reavalia depois dela.

CONTROLES NEGATIVOS MEDIDOS (cada mutação aplicada sozinha, arquivo rodado, revertida):

  a. tirar `and email_hash = %s` do UPDATE de senha → VERMELHOS: link_antigo,
     token_gravado_depois, troca_simultanea, token_de_antes_da_migracao, isolamento;
  b. na rota, revogar as sessões do dono do token ANTES do `if not user_id` →
     VERMELHO: link_antigo (sozinho — é o único que olha a sessão na recusa);
  c. `select email_hash` + comparar em Python + UPDATE só por user_id →
     VERMELHO: troca_simultanea (sozinho — é a corrida de READ COMMITTED);
  d. consumo da `main` (SELECT do token e UPDATEs em conexões separadas) →
     VERMELHOS: dois_consumos (e todos os de e-mail, que a main não confere);
  e. coluna inline no `create table` em vez do ALTER → VERMELHO: token_de_antes_da_migracao;
  f. aceitar hash nulo (`%s is null or email_hash = %s`) → VERMELHO: token_de_antes_da_migracao;
  g. emissão gravando `null` no lugar do hash → VERMELHOS: link_pedido_depois_da_troca,
     isolamento.

POSITIVOS: `test_link_pedido_depois_da_troca_funciona` (reset 200 + login com a
senha nova + sessão antiga revogada) e o token de B no teste de isolamento — sem
eles o grupo passaria num consumo que recusa tudo.
"""
from __future__ import annotations

import re
import secrets
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard
from conftest import promote_to_pro
from core.crypto import encrypt_pii_optional, hash_pii_optional
from core.sessions import create_session, get_active_session
from db import consume_password_reset_token, create_password_reset_token, ensure_user
from db.connection import get_conn
from db_support import consume_password_reset_token_impl
from tests._espera_lock import _esperar_backend_travado
from tests._helpers_pii import insert_auth_account_pii
from test_copy_definir_senha import _client_for, spy_email  # noqa: F401 (fixture por import)

ESPERA = 20  # teto de toda espera entre threads: falha vira vermelho, nunca trava a suíte


def _email() -> str:
    return f"reset-amarrado-{uuid.uuid4().hex[:10]}@test.local"


def _conta(user_id: int) -> str:
    email = _email()
    with get_conn() as conn, conn.cursor() as cur:
        insert_auth_account_pii(cur, user_id, email)  # password_hash = "hash"
        conn.commit()
    return email


# O mesmo UPDATE que a PATCH /settings/{uid}/security/contact faz.
_SQL_TROCA_EMAIL = "update auth_accounts set email = %s, email_hash = %s, email_enc = %s where user_id = %s"


def _args_troca(user_id: int, email: str) -> tuple:
    return (email, hash_pii_optional(email, kind="email"), encrypt_pii_optional(email), user_id)


def _troca_email(user_id: int, email: str) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(_SQL_TROCA_EMAIL, _args_troca(user_id, email))
        conn.commit()


def _senha(user_id: int) -> str | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select password_hash from auth_accounts where user_id = %s", (user_id,))
        return cur.fetchone()["password_hash"]


def _used_at(token: str):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select used_at from password_reset_tokens where token = %s", (token,))
        return cur.fetchone()["used_at"]


def _grava_token(user_id: int, email_hash: str | None) -> str:
    """Token gravado direto: o `email_hash` é o que o teste escolhe."""
    token = secrets.token_urlsafe(32)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into password_reset_tokens (token, user_id, expires_at, email_hash) values (%s, %s, %s, %s)",
            (token, user_id, datetime.now(timezone.utc) + timedelta(minutes=30), email_hash),
        )
        conn.commit()
    return token


def _navegador() -> tuple[TestClient, dict]:
    client = TestClient(dashboard.app)
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, "csrf-reset")
    return client, {dashboard.CSRF_HEADER_NAME: "csrf-reset"}


def _pede_link(email: str, spy: dict) -> str:
    client, headers = _navegador()
    spy.clear()
    r = client.post("/auth/forgot-password", headers=headers, json={"email": email})
    assert r.status_code == 200, r.text
    assert spy, "nenhum e-mail de reset saiu"
    return re.search(r"#token=([\w-]+)", spy["html"]).group(1)


def _redefine(token: str, senha: str):
    client, headers = _navegador()
    return client.post("/auth/reset-password", headers=headers, json={"token": token, "new_password": senha})


def _patch_email(user_id: int, email_atual: str, novo: str) -> None:
    client, headers = _client_for(user_id, email_atual)
    r = client.patch(f"/settings/{user_id}/security/contact", json={"email": novo}, headers=headers)
    assert r.status_code == 200, r.text


# ── A conversa, pelas rotas ─────────────────────────────────────────────────

def test_link_antigo_depois_da_troca_de_email_e_recusado(user_id, spy_email):
    email = _conta(user_id)
    promote_to_pro(user_id)
    jti = create_session(user_id)
    token = _pede_link(email, spy_email)

    _patch_email(user_id, email, _email())
    r = _redefine(token, "senhadoinvasor1")

    assert r.status_code == 400, r.text
    assert _senha(user_id) == "hash"
    assert _used_at(token) is not None, "a recusa tem de queimar o token"
    assert get_active_session(jti) is not None, "o reset recusado derrubou a sessão do dono"


def test_link_pedido_depois_da_troca_funciona(user_id, spy_email):
    """POSITIVO: sem ele o grupo passa num consumo que recusa tudo."""
    email = _conta(user_id)
    promote_to_pro(user_id)
    jti = create_session(user_id)
    novo = _email()
    _patch_email(user_id, email, novo)

    token = _pede_link(novo, spy_email)
    assert _redefine(token, "senhanova123").status_code == 200

    client, headers = _navegador()
    r = client.post("/auth/login", headers=headers, json={"email": novo, "password": "senhanova123"})
    assert r.status_code == 200, r.text
    assert get_active_session(jti) is None, "o reset aceito tem de revogar as sessões"


# ── As corridas ───────────────────────────────────────────────────────────────

def test_token_gravado_depois_da_troca_com_hash_antigo_e_recusado(user_id):
    """O pedido em voo: leu a conta com o e-mail antigo e grava depois da troca."""
    email = _conta(user_id)
    _troca_email(user_id, _email())
    token = _grava_token(user_id, hash_pii_optional(email, kind="email"))

    assert consume_password_reset_token(token, "senhadoinvasor1") is None
    assert _senha(user_id) == "hash"


def test_troca_de_email_simultanea_ao_consumo_nao_troca_a_senha(user_id):
    """A troca de e-mail entra DEPOIS de o consumo reivindicar o token e ANTES de
    ele gravar a senha: o UPDATE de senha fica parado na trava da linha e, quando
    ela solta, reavalia `email_hash` e não casa. Uma versão que lê o hash e compara
    em Python lê o valor antigo (a troca ainda não commitou) e troca a senha."""
    email = _conta(user_id)
    token = _grava_token(user_id, hash_pii_optional(email, kind="email"))
    trocou_sem_commit = threading.Event()
    travou: list[bool] = []

    def _troca_concorrente():
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(_SQL_TROCA_EMAIL, _args_troca(user_id, _email()))
            trocou_sem_commit.set()
            travou.append(_esperar_backend_travado(timeout=ESPERA))
            conn.commit()

    t = threading.Thread(target=_troca_concorrente, daemon=True)

    def _gancho(senha):  # roda entre a reivindicação e o UPDATE de senha
        t.start()
        assert trocou_sem_commit.wait(ESPERA)
        return "hash-do-invasor"

    try:
        resultado = consume_password_reset_token_impl(get_conn, _gancho, token, "x")
    finally:
        t.join(ESPERA)
    assert not t.is_alive()

    assert travou == [True], "o UPDATE de senha não parou na trava: o teste não mediu a corrida"
    assert resultado is None
    assert _senha(user_id) == "hash"


def test_dois_consumos_simultaneos_so_um_vence(user_id):
    email = _conta(user_id)
    token = _grava_token(user_id, hash_pii_optional(email, kind="email"))
    a_no_gancho, libera_a = threading.Event(), threading.Event()
    resultados: dict[str, int | None] = {}

    def _gancho_a(senha):
        a_no_gancho.set()
        libera_a.wait(ESPERA)
        return "hash-a"

    def _consome(nome, gancho):
        resultados[nome] = consume_password_reset_token_impl(get_conn, gancho, token, "x")

    ta = threading.Thread(target=_consome, args=("a", _gancho_a), daemon=True)
    tb = threading.Thread(target=_consome, args=("b", lambda s: "hash-b"), daemon=True)
    try:
        ta.start()
        assert a_no_gancho.wait(ESPERA)
        tb.start()
        _esperar_backend_travado(timeout=5)  # B parado na trava do token (na versão certa)
    finally:
        libera_a.set()
        ta.join(ESPERA)
        tb.join(ESPERA)
    assert not ta.is_alive() and not tb.is_alive()

    vencedores = [nome for nome, uid in resultados.items() if uid]
    assert len(vencedores) == 1, resultados
    assert _senha(user_id) == f"hash-{vencedores[0]}"


# ── Migração ──────────────────────────────────────────────────────────────────

def test_token_de_antes_da_migracao_e_recusado(user_id):
    """A tabela já existe em produção: só o ALTER põe a coluna lá. E o token que
    nasceu antes dela (email_hash NULL) não serve para trocar senha."""
    from db.schema import init_db

    _conta(user_id)
    token = secrets.token_urlsafe(32)
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("alter table password_reset_tokens drop column email_hash")
            cur.execute(
                "insert into password_reset_tokens (token, user_id, expires_at) values (%s, %s, %s)",
                (token, user_id, datetime.now(timezone.utc) + timedelta(minutes=30)),
            )
            conn.commit()

        init_db()

        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "select 1 from information_schema.columns"
                " where table_name = 'password_reset_tokens' and column_name = 'email_hash'"
            )
            assert cur.fetchone(), "init_db não pôs email_hash numa password_reset_tokens que JÁ EXISTIA"
    finally:  # nunca deixar o database do worker sem a coluna
        with get_conn() as conn:
            conn.execute("alter table password_reset_tokens add column if not exists email_hash text")
            conn.commit()

    assert consume_password_reset_token(token, "senhanova123") is None
    assert _senha(user_id) == "hash"


# ── Isolamento ────────────────────────────────────────────────────────────────

def test_troca_de_email_de_um_usuario_nao_afeta_o_outro(user_id):
    outro = int(uuid.uuid4().int % 10_000_000_000)
    ensure_user(outro)  # o _auto_cleanup_orphan_users apaga no fim
    email_a, email_b = _conta(user_id), _conta(outro)
    token_a, token_b = create_password_reset_token(email_a), create_password_reset_token(email_b)

    _troca_email(user_id, _email())

    assert consume_password_reset_token(token_b, "senhadob12345") == outro
    assert consume_password_reset_token(token_a, "senhadoinvasor1") is None
    assert _senha(user_id) == "hash"
    assert _senha(outro) not in (None, "hash")
