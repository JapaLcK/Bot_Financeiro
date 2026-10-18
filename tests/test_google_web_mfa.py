"""MFA no login pelo Google no site e no magic link do bot (#605).

Antes: `/auth/google/callback` (web) e `GET /d/{code}` emitiam a sessão assim que
provavam a identidade (Google / posse do WhatsApp), sem pedir o código TOTP.
Agora, com MFA ligado, respondem 302 para `/login?next=…#mfa=<desafio>`, SEM
nenhum cookie e SEM linha em `auth_sessions`; a sessão só nasce no
`POST /auth/mfa/verify-login`.

Cada caso negativo prova a rejeição ANTES da sessão: nenhum cookie de sessão e
a contagem de `auth_sessions` do usuário não mudou. Banco e rotas são os de
verdade; só o Google é dublê (`_apoio_auth_app.google_de_mentira`).

Controle negativo (§3; resultado no relato): trocar `desafio = await _desafio_mfa(...)`
por `desafio = None` no callback → os casos "callback com MFA" ficam vermelhos;
o mesmo no `/d/` → os casos "/d/ com MFA" ficam vermelhos (cada um separado).
Controle positivo: os casos sem MFA (callback e /d/) provam que o caminho
legítimo continua, com os mesmos destinos e cookies de antes.
"""
import os
import re
import uuid
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import pyotp
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

# Mesmo motivo do `tests/test_mfa.py`: o módulo de MFA cacheia o Fernet.
os.environ.setdefault("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())

import db
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import csrf, google_de_mentira, login_google

SENHA = "senha-forte-123"
COOKIES_DE_SESSAO = ("auth_token", "dashboard_token", "refresh_token")
LOGIN_HTML = Path(__file__).resolve().parent.parent / "frontend" / "login.html"


def _conta(mfa: bool) -> tuple[int, str, str | None]:
    email = f"web-mfa-{uuid.uuid4().hex[:10]}@example.com"
    uid = int(db.register_auth_user(email, SENHA)["user_id"])
    segredo = None
    if mfa:
        segredo = db.mfa_setup_secret(uid, email)["secret"]
        db.mfa_verify_and_enable(uid, pyotp.TOTP(segredo).now())
    return uid, email, segredo


def _sessoes(uid: int) -> int:
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) as n from auth_sessions where user_id = %s", (uid,))
        return int(cur.fetchone()["n"])


def _logins_ok(uid: int) -> int:
    """Eventos de login CONCLUÍDO (sucesso, sem `mfa_pending`)."""
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select count(*) as n from auth_login_events"
            " where user_id = %s and success and failure_reason is null",
            (uid,),
        )
        return int(cur.fetchone()["n"])


def _eventos(uid: int, motivo: str) -> int:
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select count(*) as n from auth_login_events where user_id = %s and failure_reason = %s",
            (uid, motivo),
        )
        return int(cur.fetchone()["n"])


def _set_cookies(r) -> list[str]:
    return [c.split("=", 1)[0] for c in r.headers.get_list("set-cookie")]


def _sem_sessao(r, uid: int, antes: int) -> None:
    for cookie in COOKIES_DE_SESSAO:
        assert cookie not in _set_cookies(r), f"{cookie} saiu antes do código"
    assert _sessoes(uid) == antes, "nasceu auth_sessions antes do código"


def _desafio(r) -> str:
    """O desafio do `Location` (`/login[?next=…]#mfa=<desafio>`)."""
    assert r.status_code == 302, r.text
    loc = r.headers["location"]
    assert re.fullmatch(r"/login(\?next=[^#]*)?#mfa=[\w-]+", loc), loc
    assert r.headers["cache-control"] == "no-store"
    return loc.split("#mfa=", 1)[1]


def _next(r) -> str | None:
    return parse_qs(urlparse(r.headers["location"]).query).get("next", [None])[0]


def _verifica(client: TestClient, desafio: str, segredo: str, codigo: str | None = None):
    return client.post(
        "/auth/mfa/verify-login",
        headers=csrf(client),
        json={"challenge": desafio, "code": codigo or pyotp.TOTP(segredo).now()},
    )


def _web(monkeypatch, email: str, *, next_url: str | None = None):
    google_de_mentira(monkeypatch, email)
    client = TestClient(dashboard.app)
    if next_url:
        # `login_google` não repassa `next`; o start grava o cookie que o callback lê.
        inicio = client.get(f"/auth/google/start?next={quote(next_url)}", follow_redirects=False)
        state = parse_qs(urlparse(inicio.headers["location"]).query)["state"][0]
        return client, client.get(
            "/auth/google/callback", params={"code": "c", "state": state}, follow_redirects=False
        )
    return login_google(0, client)


def _allowlist_do_login() -> list[str]:
    """A allowlist do `nextParam()` do login.html: uma só fonte de verdade (§0.7)."""
    m = re.search(r"var ok=\[([^\]]*)\]", LOGIN_HTML.read_text())
    return re.findall(r"'([^']+)'", m.group(1))


# ── callback web ─────────────────────────────────────────────────────────────

def test_c1_callback_web_sem_mfa_entra_como_antes(monkeypatch):
    """Controle positivo: conta sem MFA recebe os 3 cookies e uma sessão."""
    uid, email, _ = _conta(False)
    antes = _sessoes(uid)
    _, r = _web(monkeypatch, email)
    assert r.status_code == 302, r.text
    assert "#mfa=" not in r.headers["location"]
    for cookie in COOKIES_DE_SESSAO:
        assert cookie in _set_cookies(r)
    assert _sessoes(uid) == antes + 1
    assert _logins_ok(uid) == 1


def test_c2_callback_web_com_mfa_pede_codigo_sem_cookie_e_o_verify_login_conclui(monkeypatch):
    uid, email, segredo = _conta(True)
    antes = _sessoes(uid)
    client, r = _web(monkeypatch, email, next_url="/continuar-compra")
    desafio = _desafio(r)
    assert _next(r) == "/continuar-compra"          # o destino da compra sobrevive
    _sem_sessao(r, uid, antes)
    assert _logins_ok(uid) == 0                      # login ainda não houve

    fim = _verifica(client, desafio, segredo)
    assert fim.status_code == 200, fim.text
    assert fim.json()["user_id"] == uid
    assert "auth_token" in _set_cookies(fim)
    assert _sessoes(uid) == antes + 1
    assert _logins_ok(uid) == 1


def test_c2b_callback_web_com_mfa_sem_next_nao_leva_query(monkeypatch):
    uid, email, _ = _conta(True)
    _, r = _web(monkeypatch, email)
    _desafio(r)
    assert r.headers["location"].startswith("/login#mfa=")


def test_c3_auto_vinculo_por_email_com_mfa_nao_emite_sessao(monkeypatch):
    """Conta existente (com senha e MFA), sem identidade Google prévia: o
    e-mail do Google a deixa chegar ao código, sem sessão e sem vínculo (#988)."""
    uid, email, _ = _conta(True)
    assert db.find_user_by_google_sub(f"sub-{email}") is None
    antes = _sessoes(uid)
    _, r = _web(monkeypatch, email)
    _desafio(r)
    _sem_sessao(r, uid, antes)
    assert db.find_user_by_google_sub(f"sub-{email}") is None


def test_c4_callback_web_limpa_os_cookies_do_oauth_no_desvio_para_o_mfa(monkeypatch):
    uid, email, _ = _conta(True)
    _, r = _web(monkeypatch, email, next_url="/continuar-compra")
    apagados = [c for c in r.headers.get_list("set-cookie") if "Max-Age=0" in c]
    for nome in (dashboard.GOOGLE_OAUTH_STATE_COOKIE, dashboard.GOOGLE_OAUTH_NEXT_COOKIE):
        assert any(c.startswith(nome + "=") for c in apagados), nome


# ── verify-login: o que não pode emitir sessão ───────────────────────────────

def test_c5_codigo_errado_nao_emite_sessao_e_nao_queima_o_desafio(monkeypatch):
    uid, email, segredo = _conta(True)
    antes = _sessoes(uid)
    client, r = _web(monkeypatch, email)
    desafio = _desafio(r)

    errado = _verifica(client, desafio, segredo, "000000")
    assert errado.status_code == 400 and errado.json()["code"] == "mfa_code_invalid"
    _sem_sessao(errado, uid, antes)

    assert _verifica(client, desafio, segredo).status_code == 200   # ainda vivo
    assert _sessoes(uid) == antes + 1


def test_c6_desafio_esgotado_expirado_ou_reaproveitado_nao_emite_sessao(monkeypatch):
    uid, email, segredo = _conta(True)
    antes = _sessoes(uid)
    client, r = _web(monkeypatch, email)
    esgotado = _desafio(r)
    for _ in range(5):                      # o teto de tentativas do desafio
        assert db.mfa_reserve_login_challenge_attempt(esgotado)
    r1 = _verifica(client, esgotado, segredo)
    assert r1.status_code == 400 and r1.json()["code"] == "mfa_challenge_expired"
    _sem_sessao(r1, uid, antes)

    _, r = _web(monkeypatch, email)
    expirado = _desafio(r)
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "update mfa_login_challenges set expires_at = now() - interval '1 minute'"
            " where token = %s", (expirado,))
        conn.commit()
    r2 = _verifica(client, expirado, segredo)
    assert r2.status_code == 400 and r2.json()["code"] == "mfa_challenge_expired"
    _sem_sessao(r2, uid, antes)

    _, r = _web(monkeypatch, email)
    usado = _desafio(r)
    assert _verifica(client, usado, segredo).status_code == 200
    assert _sessoes(uid) == antes + 1
    r3 = _verifica(client, usado, segredo, "123456")
    assert r3.status_code == 400 and r3.json()["code"] == "mfa_challenge_expired"
    assert _sessoes(uid) == antes + 1        # o segundo uso não somou


# ── app antigo (pigbankai://) ────────────────────────────────────────────────

def test_c7_app_antigo_com_mfa_o_d_pede_codigo_e_so_o_verify_login_registra(monkeypatch):
    uid, email, segredo = _conta(True)
    antes = _sessoes(uid)
    google_de_mentira(monkeypatch, email)
    client, r = login_google(1)
    assert r.headers["location"].startswith("pigbankai://auth?code=")
    assert _logins_ok(uid) == 0              # o callback não registra login que não houve
    codigo = parse_qs(urlparse(r.headers["location"]).query)["code"][0]

    d = client.get(f"/d/{codigo}", follow_redirects=False)
    desafio = _desafio(d)
    _sem_sessao(d, uid, antes)
    assert _logins_ok(uid) == 0

    assert _verifica(client, desafio, segredo).status_code == 200
    assert _sessoes(uid) == antes + 1
    assert _logins_ok(uid) == 1              # um só, depois do código


def test_c8_app_antigo_sem_mfa_registra_um_login_como_antes(monkeypatch):
    """Controle positivo da mudança no callback `app-`: sem MFA nada muda."""
    uid, email, _ = _conta(False)
    google_de_mentira(monkeypatch, email)
    client, r = login_google(1)
    assert _logins_ok(uid) == 1
    d = client.get(f"/d/{parse_qs(urlparse(r.headers['location']).query)['code'][0]}",
                   follow_redirects=False)
    assert d.status_code == 302 and "auth_token" in _set_cookies(d)
    assert _logins_ok(uid) == 1              # o /d/ não duplica


# ── GET /d/{code} (magic link do bot) ────────────────────────────────────────

@pytest.mark.parametrize("query, destino", [
    ("", "/app"),
    ("?view=investments", "/app?view=investments"),
    ("?view=open-finance", "/settings?view=open-finance"),
    ("?next=/precos", "/precos"),
])
def test_d1_sem_mfa_entra_como_antes(query, destino):
    uid, _, _ = _conta(False)
    antes = _sessoes(uid)
    r = TestClient(dashboard.app).get(
        f"/d/{db.create_dashboard_session(uid)}{query}", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == destino
    for cookie in COOKIES_DE_SESSAO:
        assert cookie in _set_cookies(r)
    assert _sessoes(uid) == antes + 1


@pytest.mark.parametrize("query, destino", [
    ("", "/app"),
    ("?view=investments", "/app?view=investments"),
    ("?view=open-finance", "/settings?view=open-finance"),
    ("?next=/precos", "/precos"),
    ("?next=/conta", "/conta"),
])
def test_d2_com_mfa_pede_codigo_e_aterrissa_no_destino_original(query, destino):
    uid, _, segredo = _conta(True)
    antes = _sessoes(uid)
    client = TestClient(dashboard.app)
    r = client.get(f"/d/{db.create_dashboard_session(uid)}{query}", follow_redirects=False)
    desafio = _desafio(r)
    _sem_sessao(r, uid, antes)
    assert _next(r) == destino
    # O login.html só segue um `next` da sua allowlist: todo destino do bot tem de passar.
    assert any(destino.startswith(p) for p in _allowlist_do_login()), destino

    fim = _verifica(client, desafio, segredo)
    assert fim.status_code == 200 and _sessoes(uid) == antes + 1


def test_d3_com_mfa_codigo_ja_consumido_nao_emite_sessao():
    uid, _, _ = _conta(True)
    antes = _sessoes(uid)
    codigo = db.create_dashboard_session(uid)
    client = TestClient(dashboard.app)
    _desafio(client.get(f"/d/{codigo}", follow_redirects=False))
    r = client.get(f"/d/{codigo}", follow_redirects=False)
    assert r.status_code == 401
    _sem_sessao(r, uid, antes)


@pytest.mark.parametrize("mfa", [True, False], ids=["com_mfa", "sem_mfa"])
@pytest.mark.parametrize("hostil", ["//evil.com", "https://evil.com", "/\\evil.com", "evil.com", "/evil"])
def test_d4_next_hostil_nao_vira_destino(mfa, hostil):
    uid, _, _ = _conta(mfa)
    r = TestClient(dashboard.app).get(
        f"/d/{db.create_dashboard_session(uid)}", params={"next": hostil}, follow_redirects=False)
    assert r.status_code == 302
    assert "evil" not in r.headers["location"]
    assert (_next(r) if mfa else r.headers["location"]) == "/app"


def test_d5_codigo_de_pkce_continua_recusado_no_d_mesmo_com_mfa():
    uid, _, _ = _conta(True)
    antes = _sessoes(uid)
    codigo = db.create_dashboard_session(uid, 5 / 60, "c" * 43)
    r = TestClient(dashboard.app).get(f"/d/{codigo}", follow_redirects=False)
    assert r.status_code == 401
    _sem_sessao(r, uid, antes)


# ── Falha fechada, efeitos colaterais e quoting ──────────────────────────────

def _quebra_mfa(monkeypatch):
    def _boom(_uid):
        raise RuntimeError("banco fora")
    monkeypatch.setattr(db, "get_mfa_status", _boom)


def test_e1_get_mfa_status_com_erro_nao_emite_sessao_no_d(monkeypatch):
    """Fail-open em `_mfa_ligado` (erro = "sem MFA") emitiria sessão sem o código."""
    uid, _, _ = _conta(True)
    antes = _sessoes(uid)
    codigo = db.create_dashboard_session(uid)
    _quebra_mfa(monkeypatch)
    r = TestClient(dashboard.app, raise_server_exceptions=False).get(
        f"/d/{codigo}", follow_redirects=False)
    assert r.status_code == 500
    _sem_sessao(r, uid, antes)


@pytest.mark.parametrize("app", [0, 1], ids=["web", "app_antigo"])
def test_e2_get_mfa_status_com_erro_nao_emite_sessao_no_callback(monkeypatch, app):
    uid, email, _ = _conta(True)
    antes = _sessoes(uid)
    google_de_mentira(monkeypatch, email)
    _quebra_mfa(monkeypatch)
    _, r = login_google(app)
    assert r.status_code == 302 and r.headers["location"].startswith("/?google_error="), r.headers["location"]
    _sem_sessao(r, uid, antes)


def test_e3_new_ip_so_depois_do_codigo(monkeypatch):
    """`maybe_record_login_from_new_ip` é efeito de LOGIN: com MFA, 0 chamadas no
    callback e 1 no verify-login."""
    chamadas = []
    monkeypatch.setattr(dashboard, "maybe_record_login_from_new_ip",
                        lambda user_id, **kw: chamadas.append(user_id))
    uid, email, segredo = _conta(True)
    client, r = _web(monkeypatch, email)
    desafio = _desafio(r)
    assert chamadas == []
    assert _verifica(client, desafio, segredo).status_code == 200
    assert chamadas == [uid]


def test_e4_d_com_mfa_grava_um_mfa_pending_e_nenhum_login():
    uid, _, _ = _conta(True)
    r = TestClient(dashboard.app).get(f"/d/{db.create_dashboard_session(uid)}", follow_redirects=False)
    _desafio(r)
    assert _eventos(uid, "mfa_pending") == 1
    assert _logins_ok(uid) == 0


def test_e5_next_com_query_sobrevive_ao_302_com_quote():
    uid, _, _ = _conta(True)
    r = TestClient(dashboard.app).get(
        f"/d/{db.create_dashboard_session(uid)}", params={"next": "/precos?a=1&b=2"},
        follow_redirects=False)
    _desafio(r)
    assert r.headers["location"].startswith("/login?next=%2Fprecos%3Fa%3D1%26b%3D2#mfa=")
    assert _next(r) == "/precos?a=1&b=2"


# ── O desafio não pode chegar ao GA4 / Meta Pixel ────────────────────────────

def test_e6_login_tira_o_hash_antes_de_qualquer_snippet_de_tracking(monkeypatch):
    """O GA4 lê `location.href` no `config`, no FIM do <head> (`inject_tracking`).
    O script que tira o `#mfa=` da URL tem de vir antes dele no HTML servido."""
    from frontend.routes import shared

    monkeypatch.setattr(shared, "GA4_MEASUREMENT_ID", "G-TESTE12345")
    monkeypatch.setattr(shared, "META_PIXEL_ID", "123456789012345")
    html = TestClient(dashboard.app).get("/login").text
    limpa = html.index("history.replaceState")
    for marca in ("Meta Pixel Code", "Google Analytics (GA4)"):
        assert marca in html, f"{marca} não foi injetado: o teste não mediria nada"
        assert limpa < html.index(marca), f"o hash só sai depois de {marca}"


def test_e7_o_snippet_do_teste_de_navegador_e_o_que_o_servidor_injeta(monkeypatch):
    """`tests/frontend/login_mfa_tracking.test.mjs` não roda Python (o CI dos shards de
    frontend não tem fastapi) e lê o trecho de tracking de um arquivo. Aqui ele é
    comparado com `inject_tracking` de verdade, para não envelhecer (§0.7).
    Regenerar: `PYTHONPATH=. .venv/bin/python` com GA4 `G-TESTE12345`, Meta
    `123456789012345`, gravando `inject_tracking("</head>")` sem o `</head>`."""
    from frontend.routes import shared

    monkeypatch.setattr(shared, "GA4_MEASUREMENT_ID", "G-TESTE12345")
    monkeypatch.setattr(shared, "META_PIXEL_ID", "123456789012345")
    esperado = shared.inject_tracking("</head>")
    assert esperado.endswith("</head>")
    arquivo = Path(__file__).resolve().parent / "frontend" / "tracking_snippet_login.html"
    assert arquivo.read_text(encoding="utf-8") == esperado[: -len("</head>")]
