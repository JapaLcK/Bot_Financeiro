"""Testes do funil de prospecção (db/prospects.py + frontend/routes/prospects.py).

Cobrem: gravação idempotente da atribuição (primeiro ganha), rejeição de
código malformado, cookie do /i/{code}, autenticação por chave do
/api/prospect/status (503 sem env, 401 chave errada), o derivado de
active (paying/trial = true, free = false), ausência de PII na resposta,
e o fluxo de cadastro real (/auth/verify-email) com e sem cookie.
"""
import uuid

import pytest
from fastapi.testclient import TestClient

import db
from db import create_email_verification, ensure_user, get_conn
from db.prospects import list_prospect_status, record_prospect_referral
import frontend.finance_bot_websocket_custom as dashboard


def _code(prefix: str = "pcode") -> str:
    return f"{prefix}{uuid.uuid4().hex[:10]}"


def _csrf_headers(client: TestClient) -> dict[str, str]:
    token = "test-csrf-token"
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, token)
    return {dashboard.CSRF_HEADER_NAME: token}


def _mk_account(user_id: int, email: str, *, plan: str = "free",
                pay: str = "inactive") -> None:
    """auth_accounts com plan/last_payment_status controlados (mesmo formato
    de tests/test_admin_users_panel.py::_mk_account)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into auth_accounts
                    (user_id, email, password_hash, plan, last_payment_status)
                values (%s, %s, 'x', %s, %s)
                """,
                (user_id, email, plan, pay),
            )
        conn.commit()


def _cleanup_codes(*codes: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("delete from prospect_referrals where code = any(%s)", (list(codes),))
        conn.commit()


# ─── db/prospects.py ──────────────────────────────────────────────────────────

def test_record_idempotente_e_primeiro_ganha(user_id):
    code1, code2 = _code(), _code()
    try:
        assert record_prospect_referral(code1, user_id) is True
        # retry do mesmo code não duplica
        assert record_prospect_referral(code1, user_id) is False
        # segundo code pro mesmo user não sobrescreve (primeiro ganha)
        assert record_prospect_referral(code2, user_id) is False
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "select code from prospect_referrals where referred_user_id = %s",
                    (user_id,),
                )
                rows = cur.fetchall()
        assert [r["code"] for r in rows] == [code1]
    finally:
        _cleanup_codes(code1, code2)


def test_record_rejeita_codigo_malformado(user_id):
    for bad in ("", "abc", "a" * 21, "tem espaco", "inje'cao--", "até-com-acento",
                "abc12345\n"):  # `$` do re aceitaria o \n final; fullmatch não
        assert record_prospect_referral(bad, user_id) is False
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select 1 from prospect_referrals where referred_user_id = %s",
                (user_id,),
            )
            assert cur.fetchone() is None


def test_list_prospect_status_active_por_status_da_conta(user_id):
    """paying e trial → active; free → inactive; sem cadastro → fora."""
    codes = {label: _code(label) for label in ("paying", "trial", "free")}
    uids = {"paying": user_id}
    for label in ("trial", "free"):
        uid = int(uuid.uuid4().int % 10_000_000_000)
        ensure_user(uid)
        uids[label] = uid

    tag = uuid.uuid4().hex[:8]
    _mk_account(uids["paying"], f"prospect-{tag}-paying@test.local", plan="pro", pay="active")
    _mk_account(uids["trial"], f"prospect-{tag}-trial@test.local", plan="pro", pay="trialing")
    _mk_account(uids["free"], f"prospect-{tag}-free@test.local")

    try:
        for label, code in codes.items():
            assert record_prospect_referral(code, uids[label]) is True

        rows = list_prospect_status(list(codes.values()) + [_code("semcadastro")])
        by_code = {r["code"]: r for r in rows}

        assert set(by_code) == set(codes.values())  # código sem cadastro fora
        assert by_code[codes["paying"]]["active"] is True
        assert by_code[codes["trial"]]["active"] is True
        assert by_code[codes["free"]]["active"] is False
        for r in rows:
            # contrato de privacidade: nada além de code/registered_at/active
            assert set(r) == {"code", "registered_at", "active"}
    finally:
        _cleanup_codes(*codes.values())


def test_list_prospect_status_code_repetido_vale_o_primeiro(user_id):
    """2 cadastros com o mesmo code → 1 entrada, com o registered_at do PRIMEIRO."""
    code = _code("dup")
    uid2 = int(uuid.uuid4().int % 10_000_000_000)
    ensure_user(uid2)
    try:
        assert record_prospect_referral(code, user_id) is True
        assert record_prospect_referral(code, uid2) is True  # outro user, mesmo code
        # separa os created_at pra ordem ser observável
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    update prospect_referrals
                       set created_at = created_at - interval '1 hour'
                     where code = %s and referred_user_id = %s
                    """,
                    (code, user_id),
                )
            conn.commit()
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "select created_at from prospect_referrals where code = %s and referred_user_id = %s",
                    (code, user_id),
                )
                first_at = cur.fetchone()["created_at"]

        rows = list_prospect_status([code])
        assert len(rows) == 1
        assert rows[0]["registered_at"] == first_at.isoformat()
    finally:
        _cleanup_codes(code)


def test_list_prospect_status_empate_de_created_at_desempata_por_id(user_id):
    """created_at idêntico nas duas linhas do mesmo code → vale o menor id
    (pino de contrato: sem o `p.id` no ORDER BY o Postgres fica livre pra
    escolher qualquer linha e o active pode oscilar entre chamadas)."""
    code = _code("tie")
    uid2 = int(uuid.uuid4().int % 10_000_000_000)
    ensure_user(uid2)
    tag = uuid.uuid4().hex[:8]
    # active divergente entre as linhas: se a escolha oscilar, o teste vê.
    _mk_account(user_id, f"prospect-{tag}-tie1@test.local", plan="pro", pay="active")
    _mk_account(uid2, f"prospect-{tag}-tie2@test.local")  # free
    try:
        assert record_prospect_referral(code, user_id) is True
        assert record_prospect_referral(code, uid2) is True
        with get_conn() as conn:
            with conn.cursor() as cur:
                # empata os created_at no mesmo instante exato
                cur.execute(
                    """
                    update prospect_referrals
                       set created_at = (select min(created_at)
                                           from prospect_referrals where code = %s)
                     where code = %s
                    """,
                    (code, code),
                )
            conn.commit()
            with conn.cursor() as cur:
                cur.execute(
                    "select referred_user_id from prospect_referrals where code = %s order by id asc limit 1",
                    (code,),
                )
                min_id_user = cur.fetchone()["referred_user_id"]

        assert int(min_id_user) == int(user_id)  # bigserial: 1º insert = menor id
        first = list_prospect_status([code])
        second = list_prospect_status([code])
        assert len(first) == 1
        assert first[0]["active"] is True   # a linha de menor id é a paying
        assert first == second              # estável entre chamadas
    finally:
        _cleanup_codes(code)


# ─── GET /i/{code} ────────────────────────────────────────────────────────────

def test_link_valido_seta_cookie():
    client = TestClient(dashboard.app)
    resp = client.get("/i/abc12345", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/"
    cookie = next(
        h for h in resp.headers.get_list("set-cookie") if h.startswith("prospect_code=")
    )
    assert "abc12345" in cookie
    assert "httponly" in cookie.lower()
    assert "samesite=lax" in cookie.lower()
    assert f"max-age={30 * 24 * 3600}" in cookie.lower()


def test_link_honra_cookie_secure_do_app(monkeypatch):
    """O cookie usa o COOKIE_SECURE do app, que inclui a blindagem APP_ENV=prod
    (Secure mesmo com DASHBOARD_URL http por engano). Mesmo padrão de
    test_auth_cookie.py: força a constante e confere o atributo."""
    monkeypatch.setattr(dashboard, "COOKIE_SECURE", True)
    client = TestClient(dashboard.app)
    resp = client.get("/i/abc12345", follow_redirects=False)
    cookie = next(
        h for h in resp.headers.get_list("set-cookie") if h.startswith("prospect_code=")
    )
    assert "secure" in cookie.lower()


def test_link_invalido_nao_seta_cookie():
    client = TestClient(dashboard.app)
    for bad in ("injecao'--%22", "abc", "a" * 21, "abc12345%0A"):
        resp = client.get(f"/i/{bad}", follow_redirects=False)
        assert resp.status_code == 302
        assert not any(
            h.startswith("prospect_code=") for h in resp.headers.get_list("set-cookie")
        ), f"cookie setado para código inválido {bad!r}"


# ─── POST /api/prospect/status ────────────────────────────────────────────────

def test_status_sem_env_503(monkeypatch):
    monkeypatch.delenv("PROSPECT_API_KEY", raising=False)
    client = TestClient(dashboard.app)
    resp = client.post("/api/prospect/status", json={"codes": ["abc12345"]})
    assert resp.status_code == 503


def test_status_chave_errada_401(monkeypatch):
    monkeypatch.setenv("PROSPECT_API_KEY", "chave-certa")
    client = TestClient(dashboard.app)
    resp = client.post(
        "/api/prospect/status",
        json={"codes": ["abc12345"]},
        headers={"X-Prospect-Key": "chave-errada"},
    )
    assert resp.status_code == 401
    # sem header nenhum também é 401
    resp = client.post("/api/prospect/status", json={"codes": ["abc12345"]})
    assert resp.status_code == 401


def test_status_chave_nao_ascii_401_nao_500(monkeypatch):
    """compare_digest com str não-ASCII levanta TypeError → seria 500 sem
    autenticação; o compare em bytes devolve o 401 normal."""
    monkeypatch.setenv("PROSPECT_API_KEY", "chave-certa")
    client = TestClient(dashboard.app)
    resp = client.post(
        "/api/prospect/status",
        json={"codes": ["abc12345"]},
        headers={"X-Prospect-Key": "café".encode("latin-1")},
    )
    assert resp.status_code == 401


def test_status_resposta_sem_pii(monkeypatch, user_id):
    monkeypatch.setenv("PROSPECT_API_KEY", "chave-certa")
    code = _code()
    email = f"prospect-pii-{uuid.uuid4().hex[:8]}@test.local"
    _mk_account(user_id, email, plan="pro", pay="active")
    try:
        assert record_prospect_referral(code, user_id) is True
        client = TestClient(dashboard.app)
        resp = client.post(
            "/api/prospect/status",
            json={"codes": [code]},
            headers={"X-Prospect-Key": "chave-certa"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["referrals"][0]["code"] == code
        assert data["referrals"][0]["active"] is True
        # isolamento: a resposta NUNCA contém e-mail nem user_id
        assert email not in resp.text
        assert str(user_id) not in resp.text
        assert set(data["referrals"][0]) == {"code", "registered_at", "active"}
    finally:
        _cleanup_codes(code)


# ─── Ponta a ponta: cadastro real via /auth/verify-email ─────────────────────

def _signup(client: TestClient, *, cookie_code: str | None) -> dict:
    email = f"prospect-e2e-{uuid.uuid4().hex[:8]}@example.com"
    phone = f"55659{uuid.uuid4().int % 100_000_000:08d}"
    code6 = create_email_verification(email, "senha12345", phone)
    if cookie_code is not None:
        client.cookies.set("prospect_code", cookie_code)
    resp = client.post(
        "/auth/verify-email",
        headers=_csrf_headers(client),
        json={"email": email, "code": code6},
    )
    assert resp.status_code == 200, resp.text
    return {"email": email, "response": resp}


def _referral_row(code: str) -> dict | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select * from prospect_referrals where code = %s", (code,)
            )
            return cur.fetchone()


def test_cadastro_com_cookie_grava_atribuicao_e_consome_cookie():
    code = _code("e2e")
    client = TestClient(dashboard.app)
    try:
        result = _signup(client, cookie_code=code)
        row = _referral_row(code)
        assert row is not None, "cadastro com cookie prospect_code não gravou a atribuição"
        # o cookie é consumido no signup (delete_cookie)
        deleted = [
            h for h in result["response"].headers.get_list("set-cookie")
            if h.startswith("prospect_code=") and 'max-age=0' in h.lower()
        ]
        assert deleted, "cookie prospect_code não foi consumido no cadastro"
    finally:
        _cleanup_codes(code)


def test_cadastro_sem_cookie_funciona_e_nao_grava_linha():
    """Controle positivo: o caminho legítimo sem cookie segue intacto."""
    client = TestClient(dashboard.app)
    result = _signup(client, cookie_code=None)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select 1 from prospect_referrals p
                  join auth_accounts a on a.user_id = p.referred_user_id
                 where a.email = %s
                """,
                (result["email"],),
            )
            assert cur.fetchone() is None
