"""
Copy de "definir senha" — conta criada só via Google tem
`auth_accounts.password_hash IS NULL` e NUNCA teve senha. O e-mail e a página de
destino não podem dizer "redefinir" para ela.

Espia `core.services.email_service.send_email` (a ORIGEM), não
`send_password_reset_email`: os dois chamadores importam a função dentro do corpo,
então o patch na origem pega os dois *e* o texto real é construído pelo código sob
teste.

`"redefin"` é usado como raiz nas asserções — cobre redefinir/redefinição/redefinida
de uma vez. Atenção: `"definir" in "redefinir"` é **True**, então `"definir" in ...`
sozinho NÃO discrimina — quem discrimina é sempre o `"redefin" not in ...` ao lado.

CONTROLES NEGATIVOS DO GRUPO (os cinco MEDIDOS — 1-3 na rodada 1, 4-5 na rodada 2):
1. `has_password = True` no topo de `send_password_reset_email` → 3 vermelhos:
   `test_email_sem_senha_diz_definir` (no html e no texto puro), `test_password_reset_route_conta_google`
   e `test_forgot_password_conta_google` (nos dois, no assunto).
2. `settings.py` voltando à string única de `message` → 1 vermelho:
   `test_password_reset_route_conta_google` (na asserção do `message`).
3. `git checkout origin/main -- frontend/reset-password.html` → 1 vermelho:
   `test_pagina_reset_nao_promete_redefinicao` (no `<title>`; e, mutando SÓ o `<h1>`
   de sucesso com o `<title>` já correto, no `h1` — a asserção não para no primeiro
   elemento).
4. `email_has_password(body.email)` → `False` fixo no monólito (todo mundo passa a
   receber "definir") → 1 vermelho: `test_forgot_password_conta_com_senha`. Esta
   mutação passava VERDE na rodada 1 — era o buraco do Grupo C.
5. `git checkout origin/main -- frontend/login.html` → 1 vermelho:
   `test_pagina_login_nao_promete_redefinicao` (o toast é a ÚNICA linha diferente
   dentro do corpo de `doForgot`, que é o trecho que a asserção lê).
"""
from __future__ import annotations

import re
import uuid

import pytest
from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.shared as shared
from core.services.email_service import send_password_reset_email
from db.connection import get_conn
from tests._helpers_pii import insert_auth_account_pii

RESET_URL = "https://pigbankai.com/reset-password#token=abc123"

# A invariante anti-enumeração do /auth/forgot-password: mesmo corpo, exista ou não
# a conta. Escrita como IGUALDADE, não como `in`.
CORPO_ANONIMO = {"message": "Se este e-mail estiver cadastrado, você receberá as instruções em breve."}


@pytest.fixture
def spy_email(monkeypatch):
    """Captura o e-mail em vez de enviar. Devolve True de propósito: com False a
    rota do settings responde 500 (RESEND_API_KEY ausente aqui — CLAUDE.md §6)."""
    import core.services.email_service as es

    captured: dict = {}

    def fake_send(to, subject, html_body, text_body=None, from_addr=None, headers=None, attachments=None):
        captured.update(to=to, subject=subject, html=html_body, text=text_body)
        return True

    monkeypatch.setattr(es, "send_email", fake_send)
    return captured


@pytest.fixture
def sem_rate_limit(monkeypatch):
    """3/minute na rota do settings, 3/hour no forgot-password."""
    monkeypatch.setattr(shared.limiter, "enabled", False)
    monkeypatch.setattr(dashboard.limiter, "enabled", False)


def _cria_conta(user_id: int, *, com_senha: bool) -> str:
    """Conta via helper PII — insert SQL cru sem `email_hash` vira órfão invisível
    para os dois lookups (`email_has_password` e `create_password_reset_token`)."""
    email = f"copy-senha-{uuid.uuid4().hex[:10]}@test.local"
    with get_conn() as conn, conn.cursor() as cur:
        insert_auth_account_pii(cur, user_id, email, password_hash="hash" if com_senha else None)
        conn.commit()
    return email


def _client_for(user_id: int, email: str) -> tuple[TestClient, dict]:
    client = TestClient(dashboard.app)
    client.cookies.set(dashboard.AUTH_COOKIE_NAME, dashboard._make_jwt(user_id, email))
    client.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, dashboard.make_dashboard_token(user_id, hours=1))
    csrf = "test-csrf-token"
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, csrf)
    return client, {dashboard.CSRF_HEADER_NAME: csrf}


def _limpa_rate_limits(*identifiers: str) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("delete from auth_rate_limits where identifier = any(%s)", (list(identifiers),))
        conn.commit()


# ── Grupo A — a função, até o send_email ──────────────────────────────────────

def test_email_sem_senha_diz_definir(spy_email):
    assert send_password_reset_email("alguem@test.local", RESET_URL, False) is True

    assert "definir" in spy_email["subject"].lower()
    assert "redefin" not in spy_email["html"].lower()
    assert "redefin" not in spy_email["text"].lower()  # preview do cliente e notificação do celular
    assert "redefin" not in spy_email["subject"].lower()
    assert RESET_URL in spy_email["html"]


def test_email_com_senha_mantem_redefinir(spy_email):
    """POSITIVO: o caminho legítimo não mudou — nem explícito, nem por default.
    Assunto, html E texto puro: só o assunto deixava o CORPO do caso comum (a
    maioria dos usuários) virar a copy de conta Google sem uma linha vermelha."""
    send_password_reset_email("alguem@test.local", RESET_URL, True)
    explicito = (spy_email["subject"], spy_email["html"], spy_email["text"])

    send_password_reset_email("alguem@test.local", RESET_URL)  # argumento omitido
    default = (spy_email["subject"], spy_email["html"], spy_email["text"])

    assert all("redefin" in parte.lower() for parte in explicito), explicito
    assert default == explicito


# ── Grupo B — rota autenticada, ponta a ponta com banco ───────────────────────

def test_password_reset_route_conta_google(user_id, spy_email, sem_rate_limit):
    email = _cria_conta(user_id, com_senha=False)
    client, headers = _client_for(user_id, email)

    resp = client.post(f"/settings/{user_id}/password-reset", headers=headers)

    assert resp.status_code == 200, resp.text
    assert "definir" in resp.json()["message"].lower()
    assert "redefin" not in resp.json()["message"].lower()
    assert "redefin" not in spy_email["subject"].lower()


def test_password_reset_route_conta_com_senha(user_id, spy_email, sem_rate_limit):
    """POSITIVO: quem TEM senha continua lendo "redefinir" nos dois pontos."""
    email = _cria_conta(user_id, com_senha=True)
    client, headers = _client_for(user_id, email)

    resp = client.post(f"/settings/{user_id}/password-reset", headers=headers)

    assert resp.status_code == 200, resp.text
    assert "redefin" in resp.json()["message"].lower()
    assert "redefin" in spy_email["subject"].lower()


# ── Grupo C — anônimo. A resposta é invariante; só o e-mail muda ──────────────

def test_forgot_password_conta_google(user_id, spy_email, sem_rate_limit):
    email = _cria_conta(user_id, com_senha=False)
    client = TestClient(dashboard.app)
    csrf = "test-csrf-token"
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, csrf)
    _limpa_rate_limits("ip:testclient", f"email:{email}")

    try:
        resp = client.post(
            "/auth/forgot-password",
            headers={dashboard.CSRF_HEADER_NAME: csrf},
            json={"email": email},
        )

        assert resp.status_code == 200
        assert resp.json() == CORPO_ANONIMO  # igualdade: nada vaza "não tem senha"
        assert "definir" in spy_email["subject"].lower()
        assert "redefin" not in spy_email["subject"].lower()
    finally:
        _limpa_rate_limits("ip:testclient", f"email:{email}")


def test_forgot_password_conta_com_senha(user_id, spy_email, sem_rate_limit):
    """POSITIVO: quem TEM senha continua recebendo "redefinir" pelo fluxo anônimo.
    Sem este caso o Grupo C fica verde mesmo se o endpoint mandar "definir" para
    TODO mundo. O corpo continua sendo o mesmo, por igualdade."""
    email = _cria_conta(user_id, com_senha=True)
    client = TestClient(dashboard.app)
    csrf = "test-csrf-token"
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, csrf)
    _limpa_rate_limits("ip:testclient", f"email:{email}")

    try:
        resp = client.post(
            "/auth/forgot-password",
            headers={dashboard.CSRF_HEADER_NAME: csrf},
            json={"email": email},
        )

        assert resp.status_code == 200
        assert resp.json() == CORPO_ANONIMO  # a invariante vale em TODOS os ramos
        assert "redefin" in spy_email["subject"].lower()
    finally:
        _limpa_rate_limits("ip:testclient", f"email:{email}")


def test_forgot_password_email_inexistente(spy_email, sem_rate_limit):
    """POSITIVO/anti-enumeração: e-mail que não existe devolve o MESMO corpo, e
    nenhum e-mail sai."""
    email = f"nao-existe-{uuid.uuid4().hex}@test.local"
    client = TestClient(dashboard.app)
    csrf = "test-csrf-token"
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, csrf)
    _limpa_rate_limits("ip:testclient", f"email:{email}")

    try:
        resp = client.post(
            "/auth/forgot-password",
            headers={dashboard.CSRF_HEADER_NAME: csrf},
            json={"email": email},
        )

        assert resp.status_code == 200
        assert resp.json() == CORPO_ANONIMO
        assert spy_email == {}  # nenhum envio capturado
    finally:
        _limpa_rate_limits("ip:testclient", f"email:{email}")


# ── Grupo D — a página, servida pela rota ─────────────────────────────────────
#
# A asserção mira os ELEMENTOS que o usuário lê, não o documento inteiro:
# `"redefin" not in resp.text` proibia a palavra em qualquer id, comentário ou
# handler do arquivo e travava correção futura que precisasse dela no markup.
# Extração por regex porque é o que o repo já faz com HTML (tests/test_error_pages.py:96,
# tests/test_phosphor_subset.py:27) — nenhum parser novo.

# nome -> (regex de captura, mínimo de ocorrências)
ELEMENTOS_RESET = {
    "title": (r"<title>(.*?)</title>", 1),
    "h1": (r"<h1>(.*?)</h1>", 2),                        # formulário + sucesso
    "subtitulo": (r'<p class="auth-sub">(.*?)</p>', 2),  # formulário + sucesso
    "label": (r"<label[^>]*>(.*?)</label>", 2),
    "botao": (r'id="submit-btn"[^>]*>(.*?)</button>', 1),
    "botao_enviando": (r'submitBtn\.textContent = "(.*?)"', 1),
}


def test_pagina_reset_nao_promete_redefinicao():
    resp = TestClient(dashboard.app).get("/reset-password")
    assert resp.status_code == 200

    for nome, (regex, minimo) in ELEMENTOS_RESET.items():
        textos = re.findall(regex, resp.text, re.S)
        # sem isto, um regex que deixou de casar passa verde sem medir nada
        assert len(textos) >= minimo, f"{nome}: {len(textos)} ocorrência(s), esperado >= {minimo}"
        for texto in textos:
            assert "redefin" not in texto.lower(), f"{nome} promete redefinição: {texto!r}"


def test_pagina_login_nao_promete_redefinicao():
    """A primeira coisa que o usuário só-Google lê ao clicar "Esqueci minha senha"
    é o toast de `login.html`, ANTES de qualquer resposta do servidor. Ele não pode
    saber se a conta tem senha (seria enumeração), então tem de ser genérico.

    Mira o corpo de `doForgot` — as mensagens desse fluxo — e não o arquivo inteiro."""
    resp = TestClient(dashboard.app).get("/login")
    assert resp.status_code == 200

    corpo = re.search(r"async function doForgot\(.*?\n  \}", resp.text, re.S)
    assert corpo, "doForgot não encontrado — o teste deixou de medir o toast"
    assert "err('login-error'" in corpo.group(0)  # o toast está mesmo dentro do trecho
    assert "redefin" not in corpo.group(0).lower(), corpo.group(0)
