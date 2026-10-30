"""Grupo 7, PR-5b: um só construtor do deep link do WhatsApp, e ele leva `vincular CODIGO`.

Antes eram três (`_build_whatsapp_onboarding_link`, `_whatsapp_link` do e-mail,
`_link_whatsapp` das páginas) e o do site abria o chat com "Olá": quem tocava em
Conectar WhatsApp mandava uma saudação, não o código. Só o código liga o número
(PR-5a), então o texto que o site pré-preenche TEM de ser o que o parser do bot
entende. Grupos:

  · as 5 rotas que devolvem `whatsapp_link` (`link-code`, `login`, `verify-email`,
    `mfa/verify-login`, `google/complete-signup`), pelo TestClient real: com número,
    `text` decodificado == `vincular <link_code>`; SEM número, `""` (falha fechada:
    o `wa.me/?text=` abre o seletor de contatos e o código, de uso único, iria para
    quem a pessoa escolhesse). Negativo: devolver "" apesar de calcular, ou calcular
    o certo e descartar por um "Olá", deixa a rota vermelha;
  · round trip — o texto do site, passado ao `classify` e ao bot de verdade
    (`process_message`), liga o número novo à conta dona do código;
  · fonte única — `api.whatsapp.com`/`wa.me/` só existem em `core/dashboard_links.py`.

CEGUEIRA DECLARADA: o WhatsApp real abrindo com o texto preenchido (iOS, Android,
WhatsApp Web) e o e-mail renderizado não são exercitados aqui.
"""
import os
import re
import secrets
import subprocess
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pyotp
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

os.environ.setdefault("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())

import db
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import EMAIL, UID, csrf, limpa_rate_limits, login_http, req
from _paywall_gate_helpers import com_plano
from core.dashboard_links import link_whatsapp
from core.intent_classifier import classify
from core.services import email_service
from frontend.routes.shared import limiter
from test_senha_obrigatoria_whatsapp import _dono_do_numero, _gastos, _manda, enviadas  # noqa: F401

RAIZ = Path(__file__).resolve().parent.parent
NUMERO = "5511999990000"
SENHA = "senha-forte-123"


def _texto(url: str) -> str:
    return parse_qs(urlparse(url).query)["text"][0]


def _telefone() -> str:
    return f"55119{uuid.uuid4().int % 10**8:08d}"


@pytest.fixture(autouse=True)
def _sem_envio_de_email(monkeypatch):
    monkeypatch.setattr(email_service, "send_welcome_email", lambda *a, **k: True)


# ── as 5 rotas: cada uma devolve o corpo da resposta real ────────────────────

def _link_code(monkeypatch):
    db.ensure_user(UID)
    access, _jti, _refresh = dashboard._issue_session_token(UID, EMAIL, req("/auth/login"))
    client = TestClient(dashboard.app)
    r = client.post("/auth/link-code", headers={**csrf(client), "Authorization": f"Bearer {access}"})
    assert r.status_code == 200, r.text
    return r.json()


def _login(monkeypatch):
    corpo, _ = login_http(monkeypatch, como_app=False)
    assert corpo["link_code"] == ""  # só /auth/link-code gera código
    return corpo


def _verify_email(monkeypatch):
    enviados = {}
    monkeypatch.setattr(email_service, "send_verification_email",
                        lambda to, code: enviados.setdefault(to, code) or True)
    email = f"l5b-{uuid.uuid4().hex[:10]}@example.com"
    limpa_rate_limits("register", email)
    limpa_rate_limits("verify-email", email)
    c = TestClient(dashboard.app)
    r = c.post("/auth/register", headers=csrf(c), json={
        "email": email, "password": SENHA, "phone": _telefone(), "name": "Fulana"})
    assert r.status_code == 200, r.text
    r = c.post("/auth/verify-email", headers=csrf(c), json={"email": email, "code": enviados[email]})
    assert r.status_code == 200, r.text
    return r.json()


def _mfa_verify_login(monkeypatch):
    email = f"l5b-mfa-{uuid.uuid4().hex[:10]}@test.com"
    uid = int(db.register_auth_user(email, SENHA)["user_id"])
    secret = db.mfa_setup_secret(uid, email)["secret"]
    db.mfa_verify_and_enable(uid, pyotp.TOTP(secret).now())
    c = TestClient(dashboard.app)
    limpa_rate_limits("login", email)
    r = c.post("/auth/login", json={"email": email, "password": SENHA}, headers=csrf(c))
    assert r.status_code == 200, r.text
    limiter._storage.reset()
    r = c.post("/auth/mfa/verify-login", headers=csrf(c), json={
        "challenge": r.json()["mfa_challenge"], "code": pyotp.TOTP(secret).now(), "use_backup": False})
    assert r.status_code == 200, r.text
    return r.json()


def _google_complete_signup(monkeypatch):
    email = f"l5b-g-{uuid.uuid4().hex[:10]}@example.com"
    token = db.create_pending_google_signup(f"sub-{email}", email, "Fulana")
    c = TestClient(dashboard.app)
    r = c.post("/auth/google/complete-signup", headers=csrf(c), json={
        "token": token, "name": "Fulana", "phone": _telefone(), "accepted_terms": True})
    assert r.status_code == 200, r.text
    return r.json()


ROTAS = [_link_code, _login, _verify_email, _mfa_verify_login, _google_complete_signup]
ids = lambda f: f.__name__.lstrip("_")  # noqa: E731


@pytest.mark.parametrize("rota", ROTAS, ids=ids)
def test_com_numero_o_link_leva_vincular_e_o_codigo(rota, monkeypatch):
    monkeypatch.setenv("WHATSAPP_NUMBER", NUMERO)
    corpo = rota(monkeypatch)
    if rota is not _link_code:  # as outras rotas devolvem as chaves vazias, sem criar código
        assert corpo["link_code"] == "" and corpo["whatsapp_link"] == ""
        return
    url = corpo["whatsapp_link"]
    assert url.startswith(f"https://api.whatsapp.com/send?phone={NUMERO}&text="), url
    assert re.fullmatch(r"vincular pbl[0-9a-f]{32}", _texto(url))


@pytest.mark.parametrize("rota", ROTAS, ids=ids)
def test_sem_numero_o_link_vem_vazio_e_nunca_o_seletor_com_o_codigo(rota, monkeypatch):
    monkeypatch.delenv("WHATSAPP_NUMBER", raising=False)
    corpo = rota(monkeypatch)
    assert corpo["whatsapp_link"] == "", corpo["whatsapp_link"]


def test_link_code_isolamento_o_codigo_do_link_e_do_dono_dele(monkeypatch):
    """O código dentro do link de cada usuário consome para ELE, e só uma vez."""
    monkeypatch.setenv("WHATSAPP_NUMBER", NUMERO)
    donos = []
    for i in range(2):
        email = f"l5b-iso{i}-{uuid.uuid4().hex[:8]}@test.com"
        uid = int(db.register_auth_user(email, SENHA)["user_id"])
        access, _j, _r = dashboard._issue_session_token(uid, email, req("/auth/login"))
        c = TestClient(dashboard.app)
        r = c.post("/auth/link-code", headers={**csrf(c), "Authorization": f"Bearer {access}"})
        assert r.status_code == 200, r.text
        donos.append((uid, _texto(r.json()["whatsapp_link"]).split()[1]))
    for uid, codigo in donos:
        assert db.consume_link_code(codigo) == uid
        assert db.consume_link_code(codigo) is None  # uso único


# ── round trip site × parser × bot ───────────────────────────────────────────

@pytest.mark.parametrize("codigo", ["pbl" + "0123456789abcdef" * 2, "pbl" + "f" * 32])
def test_round_trip_site_x_parser(codigo, monkeypatch):
    monkeypatch.setenv("WHATSAPP_NUMBER", NUMERO)
    r = classify(_texto(link_whatsapp(f"vincular {codigo}")), allow_ai=False)
    assert r.intent == "account.vincular" and r.entities["code"] == codigo


def test_controle_negativo_o_ola_antigo_nao_vincula():
    """Mede que o round trip discrimina: a saudação antiga NÃO é `account.vincular`."""
    assert classify("Olá", allow_ai=False).intent != "account.vincular"


def test_link_do_site_liga_o_numero_novo_pelo_bot(enviadas, monkeypatch):
    """O texto que o site entrega, decodificado, mandado por um número novo pelo
    `process_message` real, liga esse número à conta dona do código."""
    monkeypatch.setenv("WHATSAPP_NUMBER", NUMERO)
    uid = com_plano()
    code = db.create_link_code(uid)
    texto = _texto(link_whatsapp(f"vincular {code}"))
    n = f"5511{secrets.randbelow(10**9):09d}"
    _manda(n, texto)
    assert _dono_do_numero(n) == uid, enviadas
    assert enviadas[0].startswith("✅ WhatsApp vinculado"), enviadas
    enviadas.clear()
    _manda(n, "gastei 50 mercado")
    assert _gastos(uid) == 1


# ── o construtor ─────────────────────────────────────────────────────────────

def test_link_whatsapp_le_o_numero_a_cada_chamada_e_so_o_seletor_leva_saudacao(monkeypatch):
    monkeypatch.setenv("WHATSAPP_NUMBER", "+55 (11) 98888-7777")
    assert link_whatsapp("oi").startswith("https://api.whatsapp.com/send?phone=5511988887777&text=")
    monkeypatch.setenv("WHATSAPP_NUMBER", "")
    assert link_whatsapp("vincular pbl") == ""  # falha fechada
    assert link_whatsapp("oi", seletor=True) == "https://wa.me/?text=oi"  # saudação, sem segredo


@pytest.mark.parametrize("numero", ["٥٥١١٩٩٩٩٩٩٩٩٩", "５５１１９９９９９９９９", "⁵⁵¹¹", "abc"])
def test_numero_sem_digito_ascii_nao_gera_phone(numero, monkeypatch):
    """Dígitos Unicode (árabe-índico, largura total, sobrescrito) passam em `str.isdigit`
    e gerariam `phone=` não-ASCII: valem como número ausente."""
    monkeypatch.setenv("WHATSAPP_NUMBER", numero)
    assert link_whatsapp("vincular pbl") == ""
    assert link_whatsapp("oi", seletor=True) == "https://wa.me/?text=oi"


def test_wa_sem_numero_segue_funcionando_pelo_seletor(monkeypatch):
    monkeypatch.delenv("WHATSAPP_NUMBER", raising=False)
    r = TestClient(dashboard.app).get("/wa", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith("https://wa.me/?text="), r.headers


def test_so_um_construtor_do_deep_link():
    """`api.whatsapp.com`/`wa.me/` só aparecem em core/dashboard_links.py (o construtor),
    em QUALQUER arquivo rastreado (py, js, ts/tsx, html, mjs, md, json: app/ e webapp/
    inclusos). Exceção única: app/src/features/conta/contato.ts, que só VALIDA o host do
    link que o servidor monta antes do Linking.openURL, sem construir link nenhum.
    Ficam de fora `tests/` e `app/__tests__` (que assertam a URL), `docs/` e
    `.time-dev/` (histórico e planos, que citam o construtor antigo por nome)."""
    achados = subprocess.run(
        ["git", "grep", "-lIE", r"api\.whatsapp\.com|wa\.me/", "--", ".",
         ":!tests", ":!app/__tests__", ":!docs", ":!.time-dev"],
        cwd=RAIZ, capture_output=True, text=True,
    ).stdout.split()
    assert achados == ["app/src/features/conta/contato.ts", "core/dashboard_links.py"], achados
