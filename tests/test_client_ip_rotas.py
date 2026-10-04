"""Grupo C da #766 (PR B): o IP real pelas ROTAS, com o cabeçalho de produção.

Peer do proxy do Railway (100.64.0.9), `X-Forwarded-For` "C, R" como as sondas
#791/#808 mediram, `CLOUDFLARE_ORIGIN_SECRET` válida e o segredo no cabeçalho.
Cada teste fala com a rota real e com o banco real; a sonda do `core.client_ip`
vira uma Thread falsa (não grava nada).

Controles negativos provados no PR (cada um deixa vermelho o teste citado):
- `quiz_signup` de volta ao IP do peer → `test_quiz_dois_visitantes_*`;
- `client_ip` sem a checagem do segredo → `test_quiz_cabecalho_forjado_*`;
- `log_auth_login_event` de volta ao IP do peer → `test_login_logout_login_*`;
- `Limiter` com o key_func antigo (ou `rate_limit_key` sem o lambda: o slowapi
  chama sem argumento e a rota dá 500) → `test_slowapi_*`;
- `_check_auth_rate_limits` de volta ao IP do peer → `test_teto_persistente_*`;
- `Limiter` com `client_ip` (sem o /64) → `test_slowapi_ipv6_*`;
- cada ponto que GRAVA o IP de volta a `request.client.host` → o teste do ponto
  (admin, MFA, export pedido/download, Google callback/cadastro, `/d/`,
  `_ip_prefix` do Pix e do Open Finance, login falho só-Google e senha errada,
  `/auth/refresh`);
- `_ip_prefix` do Open Finance de volta ao `split(":")` → `test_ip_prefix_*_comprimido`.
Positivos: o visitante legítimo passa em todos (V2 200, V1 até o teto) e o IPv4
segue por endereço (`test_slowapi_duas_cf_*`).
"""
from __future__ import annotations

import asyncio
import os
import uuid
from types import SimpleNamespace

import pyotp
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from starlette.requests import Request

os.environ.setdefault("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())

import core.admin_dashboard as admin_dashboard
import core.audit as audit
import core.client_ip as cip
import core.services.email_service as email_service
import core.services.meta_capi as meta_capi
import db
import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.billing_pix as billing_pix
import frontend.routes.open_finance as open_finance
import frontend.routes.quiz_signup as quiz_signup
from _apoio_auth_app import google_de_mentira, login_google
from frontend.routes import shared
from tests.test_quiz_conta import CSRF, _conta_quiz, _email

PEER = ("100.64.0.9", 1234)
C, R = "172.64.0.1", "203.0.113.9"   # borda da Cloudflare, IP de infra (última do XFF)
V1, V2 = "45.160.10.1", "45.160.10.2"
V6A, V6B, V6_OUTRO = "2804:14c:1:2::5", "2804:14c:1:2::6", "2804:14c:1:3::5"   # A e B: mesmo /64
SEGREDO = "segredo-de-teste-" + "x" * 40


@pytest.fixture(autouse=True)
def ambiente(monkeypatch):
    monkeypatch.setenv(cip.SEGREDO_ENV, SEGREDO)
    monkeypatch.setattr(cip, "_SONDA_VISTAS", set())
    monkeypatch.setattr(cip, "Thread", lambda **kw: SimpleNamespace(start=lambda: None))
    monkeypatch.setattr(audit, "_dispatch_new_login_email", lambda *a, **k: None)
    monkeypatch.setattr(email_service, "send_welcome_email", lambda *a, **k: True)
    monkeypatch.setattr(meta_capi, "capi_configured", lambda: False)
    monkeypatch.setattr(quiz_signup, "log_system_event", _nada)
    shared.limiter.reset()


async def _nada(*a, **k):
    return None


def _cliente(cf: str, segredo: str | None = SEGREDO) -> TestClient:
    cabecalhos = {"x-forwarded-for": f"{C}, {R}", "cf-connecting-ip": cf}
    if segredo is not None:
        cabecalhos["x-pigbank-cf-secret"] = segredo
    cliente = TestClient(dashboard.app, client=PEER, headers=cabecalhos)
    cliente.cookies.set(dashboard.CSRF_COOKIE_NAME, CSRF)
    return cliente


def _quiz(cf: str, segredo: str | None = SEGREDO) -> int:
    return _conta_quiz(_cliente(cf, segredo), _email()).status_code


# ---- quiz (/auth/quiz/conta, 10 contas/hora por IP) -------------------------

def test_quiz_dois_visitantes_atras_do_mesmo_proxy_tem_baldes_proprios():
    assert [_quiz(V1) for _ in range(10)] == [200] * 10
    assert _quiz(V2) == 200          # positivo: o balde de V1 cheio não alcança V2
    assert _quiz(V1) == 429


@pytest.mark.parametrize("segredo", ["y" * 57, None], ids=["errado", "ausente"])
def test_quiz_cabecalho_forjado_cai_no_balde_da_conexao(segredo):
    """CF trocando a cada pedido sem o segredo certo: tudo no balde de R."""
    estados = [_quiz(f"45.160.20.{i}", segredo) for i in range(11)]
    assert estados == [200] * 10 + [429]


# ---- login: auth_login_events e login_from_new_ip ---------------------------

def _usuario() -> tuple[int, str]:
    email = f"ip-real-{uuid.uuid4().hex[:10]}@example.com"
    return int(db.register_auth_user(email, "secret123")["user_id"]), email


def _login(cliente: TestClient, email: str):
    r = cliente.post("/auth/login", headers={dashboard.CSRF_HEADER_NAME: CSRF},
                     json={"email": email, "password": "secret123"})
    assert r.status_code == 200, r.text
    return r


def test_login_logout_login_do_mesmo_visitante_gera_um_evento_de_ip_novo():
    uid, email = _usuario()
    cliente = _cliente(V1)
    _login(cliente, email)
    assert cliente.post("/auth/logout", headers={dashboard.CSRF_HEADER_NAME: CSRF}).status_code == 200
    _login(_cliente(V1), email)

    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select ip_address from auth_login_events where user_id = %s", (uid,))
        login_ips = [r["ip_address"] for r in cur.fetchall()]
        cur.execute("select ip from audit_events where user_id = %s and event = %s",
                    (uid, audit.AuditEvent.LOGIN_FROM_NEW_IP))
        novos = [r["ip"] for r in cur.fetchall()]
        cur.execute("select ip from auth_sessions where user_id = %s", (uid,))
        sessoes = {r["ip"] for r in cur.fetchall()}
        conn.commit()
    assert novos == [V1]             # um evento só: o 2º login já é de IP conhecido
    assert login_ips == [V1, V1]
    assert sessoes == {V1}


# ---- /auth/forgot-password: slowapi (3/hour) e o teto persistente (3/hora) --

def _esqueci(cf: str) -> int:
    return _cliente(cf).post("/auth/forgot-password", headers={dashboard.CSRF_HEADER_NAME: CSRF},
                             json={"email": f"ninguem-{uuid.uuid4().hex[:8]}@example.com"}).status_code


def test_slowapi_duas_cf_diferentes_nao_dividem_o_balde(monkeypatch):
    monkeypatch.setattr(dashboard, "_check_auth_rate_limits", _nada)
    assert [_esqueci(V1) for _ in range(3)] == [200] * 3
    assert _esqueci(V2) == 200
    assert _esqueci(V1) == 429


def test_teto_persistente_por_ip_separa_os_visitantes(monkeypatch):
    monkeypatch.setattr(shared.limiter, "enabled", False)
    assert [_esqueci(V1) for _ in range(3)] == [200] * 3
    assert _esqueci(V2) == 200
    assert _esqueci(V1) == 429


def test_slowapi_ipv6_do_mesmo_64_divide_o_balde(monkeypatch):
    """Um aparelho troca de endereço dentro do /64 que recebe: o balde é do /64."""
    monkeypatch.setattr(dashboard, "_check_auth_rate_limits", _nada)
    assert [_esqueci(V6A) for _ in range(3)] == [200] * 3
    assert _esqueci(V6B) == 429      # mesmo /64, outro endereço: balde cheio
    assert _esqueci(V6_OUTRO) == 200  # /64 vizinho: balde próprio


# ---- pontos que GRAVAM ou EXIBEM o IP ---------------------------------------

def _coluna(sql: str, *args) -> list:
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, args)
        valores = [next(iter(r.values())) for r in cur.fetchall()]
        conn.commit()
    return valores


def _ips_de_login(uid: int) -> list:
    return _coluna("select ip_address from auth_login_events where user_id = %s order by id", uid)


CSRF_HDR = {dashboard.CSRF_HEADER_NAME: CSRF}


@pytest.mark.parametrize("so_google", [False, True], ids=["senha-errada", "so-google"])
def test_login_falho_grava_o_ip_do_visitante(so_google):
    uid, email = _usuario()
    if so_google:
        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute("update auth_accounts set password_hash = null where user_id = %s", (uid,))
            conn.commit()
    r = _cliente(V1).post("/auth/login", headers=CSRF_HDR, json={"email": email, "password": "errada99"})
    assert r.status_code == 401, r.text
    motivo = "google_only_account" if so_google else "invalid_credentials"
    assert _coluna("select ip_address from auth_login_events where email = %s and failure_reason = %s",
                   email, motivo) == [V1]


def test_refresh_grava_o_ip_do_visitante_do_refresh():
    uid, email = _usuario()
    login = _cliente(V1)
    _login(login, email)
    refresh = _cliente(V2)
    refresh.cookies.set(dashboard.REFRESH_COOKIE_NAME, login.cookies.get(dashboard.REFRESH_COOKIE_NAME))
    r = refresh.post("/auth/refresh", headers=CSRF_HDR)
    assert r.status_code == 200, r.text
    # o do login foi consumido (used_at); o vivo é o que o refresh emitiu
    assert _coluna("select ip from auth_refresh_tokens where user_id = %s and used_at is null", uid) == [V2]
    assert _coluna("select ip from auth_refresh_tokens where user_id = %s and used_at is not null", uid) == [V1]


def test_admin_login_grava_o_ip_do_visitante(monkeypatch):
    ips = []

    async def _log(*a, **k):
        ips.append(k["details"]["ip"])

    monkeypatch.setattr(admin_dashboard, "ADMIN_DASHBOARD_USERNAME", "admin")
    monkeypatch.setattr(admin_dashboard, "ADMIN_DASHBOARD_PASSWORD", "secret-admin")
    monkeypatch.setattr(admin_dashboard, "ADMIN_DASHBOARD_PASSWORD_HASH", "")
    monkeypatch.setattr(admin_dashboard, "log_system_event", _log)
    cliente = _cliente(V1)
    estados = [cliente.post("/admin/auth/login", headers=CSRF_HDR,
                            json={"username": u, "password": p}).status_code
               for u, p in (("outro", "x"), ("admin", "errada"), ("admin", "secret-admin"))]
    assert estados == [401, 401, 200]
    assert ips == [V1] * 3   # usuário errado, senha errada, sucesso


def test_mfa_verify_login_grava_o_ip_do_visitante():
    uid, email = _usuario()
    segredo = db.mfa_setup_secret(uid, email)["secret"]
    db.mfa_verify_and_enable(uid, pyotp.TOTP(segredo).now())
    cliente = _cliente(V1)
    desafio = _login(cliente, email).json()["mfa_challenge"]
    r = cliente.post("/auth/mfa/verify-login", headers=CSRF_HDR, json={
        "challenge": desafio, "code": pyotp.TOTP(segredo).now(), "use_backup": False})
    assert r.status_code == 200, r.text
    assert _ips_de_login(uid) == [V1, V1]   # mfa_pending e o sucesso


def test_export_pedido_e_download_gravam_o_ip_do_visitante(monkeypatch):
    uid, _ = _usuario()
    emails, logs, notificacoes = [], [], []
    monkeypatch.setattr(email_service, "send_data_export_link_email",
                        lambda to, url, minutos, ip, ua: emails.append(ip) or True)
    monkeypatch.setattr(email_service, "send_data_export_completed_email",
                        lambda to, quando, ip, ua: emails.append(ip) or True)

    async def _log(*a, **k):
        logs.append((a[1], k["details"]["ip"]))

    monkeypatch.setattr(dashboard, "log_system_event", _log)
    # O aviso do download sai num create_task que o TestClient não espera:
    # guarda a corrotina e roda no fim.
    criar = asyncio.create_task
    monkeypatch.setattr(dashboard.asyncio, "create_task", lambda c, **k: (
        notificacoes.append(c) if c.__name__ == "_notify_completed" else criar(c, **k)))

    cliente = _cliente(V1)
    cliente.cookies.set("dashboard_token", dashboard.make_dashboard_token(uid, hours=1))
    r = cliente.post("/auth/account/export", headers=CSRF_HDR, json={"password": "secret123"})
    assert r.status_code == 200, r.text
    assert _coluna("select request_ip from data_export_tokens where user_id = %s", uid) == [V1]

    token, _ = db.create_data_export_token(uid)
    assert _cliente(V1).get(f"/auth/account/export/download/{token}").status_code == 200
    assert _cliente(V1).get("/auth/account/export/download/token-que-nao-existe").status_code == 410
    asyncio.run(notificacoes.pop())

    assert emails == [V1, V1]   # link do pedido, aviso do download
    assert logs == [("data_export_requested", V1), ("data_export_token_invalid", V1),
                    ("data_export_completed", V1)]


@pytest.mark.parametrize("app", [0, 1], ids=["web", "app-antigo"])
def test_google_callback_grava_o_ip_do_visitante(monkeypatch, app):
    uid, email = _usuario()
    google_de_mentira(monkeypatch, email)
    _, r = login_google(app, _cliente(V1))
    assert r.status_code == 302, r.text
    assert "erro" not in r.headers["location"], r.headers["location"]
    assert _ips_de_login(uid) == [V1]


def test_cadastro_social_grava_o_ip_do_visitante():
    email = f"ip-real-{uuid.uuid4().hex[:10]}@example.com"
    token = db.create_pending_google_signup(f"sub-{email}", email, "Fulana")
    r = _cliente(V1).post("/auth/google/complete-signup", headers=CSRF_HDR, json={
        "token": token, "name": "Fulana", "accepted_terms": True,
        "phone": f"55119{uuid.uuid4().int % 100_000_000:08d}"})
    assert r.status_code == 200, r.text
    assert _ips_de_login(int(r.json()["user_id"])) == [V1]


def test_link_curto_do_dashboard_grava_o_ip_na_sessao():
    uid, _ = _usuario()
    r = _cliente(V1).get(f"/d/{db.create_dashboard_session(uid)}", follow_redirects=False)
    assert r.status_code == 302, r.text
    assert _coluna("select ip from auth_sessions where user_id = %s", uid) == [V1]


# ---- _ip_prefix (rastro truncado do Pix e do Open Finance) ------------------

def _pedido(cf: str) -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "query_string": b"",
                    "client": PEER, "headers": [
                        (b"x-forwarded-for", f"{C}, {R}".encode()),
                        (b"cf-connecting-ip", cf.encode()),
                        (b"x-pigbank-cf-secret", SEGREDO.encode())]})


def test_ip_prefix_do_pix_e_do_open_finance_usam_o_ip_do_visitante():
    assert billing_pix._ip_prefix(_pedido(V1)) == "45.160"
    assert billing_pix._ip_prefix(_pedido(V6A)) == ""   # o Pix só rastreia IPv4
    assert open_finance._ip_prefix(_pedido(V1)) == "45.160.10.0/24"
    assert open_finance._ip_prefix(_pedido(V6A)) == "2804:14c:1::/48"


@pytest.mark.parametrize("peer, esperado", [
    ("2804:14c::5", "2804:14c::/48"),        # o `split(":")` dava "2804:14c:::/48"
    ("2001:db8::1", "2001:db8::/48"),
    ("::1", "::/48"),
    ("fe80::1", "fe80::/48"),
    ("2804:014c:0001:0002:0003:0004:0005:0006", "2804:14c:1::/48"),
    ("fe80::1%eth0", "fe80::/48"),           # a zona cai no prefixo
    ("host:porta", "desconhecido"),          # peer que não é IP volta cru do client_ip
    ("testclient", "desconhecido"),
    ("45.160.10.1", "45.160.10.0/24"),
])
def test_ip_prefix_do_open_finance_com_ipv6_comprimido(peer, esperado):
    pedido = Request({"type": "http", "method": "GET", "path": "/", "query_string": b"",
                      "client": (peer, 1234), "headers": []})
    assert open_finance._ip_prefix(pedido) == esperado
