"""Quiz (XQuiz) → webhook → /q → /auth/verify-email: a conta nasce SEM senha.

Rotas e banco reais. O e-mail é capturado por monkeypatch (o código vem de lá).
Controles negativos medidos no PR: desligar o `password=None` (hash de senha
gravado) ou a migração (NOT NULL) deixa `test_conversa_*` vermelho; desligar o
reuso do código vivo deixa `test_reenvio_do_webhook_*` vermelho; responder 200
na falha de envio ou de banco deixa `test_falha_de_envio_ou_banco_*` vermelho.
"""
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import core.services.email_service as email_service
import db
import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.quiz_signup as quiz_signup
from core.crypto import hash_pii_optional
from frontend.routes import shared

TOKEN = "tok-xquiz-teste"
NOME = "Fulana Quiz"


def _email() -> str:
    return f"quiz-signup-{uuid.uuid4().hex[:10]}@example.com"


def _telefone() -> str:
    return f"55659{uuid.uuid4().int % 100_000_000:08d}"


def _limpa_baldes():
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("delete from auth_rate_limits where bucket = 'quiz-webhook' or identifier = 'ip:testclient'")
        conn.commit()


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("XQUIZ_WEBHOOK_TOKEN", TOKEN)
    enviados, avisos, resets, eventos = [], [], [], []

    def _envia(to, code):
        enviados.append((to, code))
        return True

    async def _evento(*a, **kw):
        eventos.append((a, kw))

    for mod in (quiz_signup, email_service):  # webhook/resend e o /auth/register
        monkeypatch.setattr(mod, "send_verification_email", _envia)
    monkeypatch.setattr(quiz_signup, "send_account_exists_notice", lambda *a: avisos.append(a) or True)
    monkeypatch.setattr(email_service, "send_password_reset_email",
                        lambda to, url, *a: resets.append(url) or True)
    monkeypatch.setattr(quiz_signup, "log_system_event", _evento)
    shared.limiter.reset()
    _limpa_baldes()
    yield SimpleNamespace(enviados=enviados, avisos=avisos, resets=resets, eventos=eventos)
    _limpa_baldes()


def _webhook(corpo: dict, headers: dict | None = None):
    # Um cookie qualquer no jar: sem a isenção de CSRF o POST tomaria 403.
    client = TestClient(dashboard.app)
    client.cookies.set("qualquer", "1")
    return client.post("/xquiz/webhook", json=corpo,
                       headers=headers if headers is not None else {"Authorization": f"Bearer {TOKEN}"})


def _navegador(cookie_quiz: str | None = None) -> tuple[TestClient, dict]:
    client = TestClient(dashboard.app)
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, "csrf-quiz")
    if cookie_quiz:
        client.cookies.set("quiz_result", cookie_quiz)
    return client, {dashboard.CSRF_HEADER_NAME: "csrf-quiz"}


def _conta(email: str):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select password_hash, display_name, phone_e164, dashboard_profile, signup_quiz "
                    "from auth_accounts where email_hash = %s", (hash_pii_optional(email, kind="email"),))
        linha = cur.fetchone()
        conn.commit()
    return linha


def _codigos(email: str) -> int:
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) as n from email_verification_codes where email_hash = %s",
                    (hash_pii_optional(email, kind="email"),))
        n = cur.fetchone()["n"]
        conn.commit()
    return n


def _verifica(email: str, code: str, cookie_quiz: str | None = None):
    client, headers = _navegador(cookie_quiz)
    return client, client.post("/auth/verify-email", headers=headers, json={"email": email, "code": code})


# ── A conversa ───────────────────────────────────────────────────────────────

def test_conversa_quiz_cria_conta_sem_senha_e_depois_define_senha(env):
    email, tel = _email(), _telefone()
    r = _webhook({"email": email.upper(), "nome": NOME, "whatsapp": tel, "extra": {"x": 1}})
    assert (r.status_code, r.json()) == (200, {"ok": True})
    assert [to for to, _ in env.enviados] == [email]
    code = env.enviados[0][1]

    client, r = _verifica(email, code, "v1.dividas.acdbd")
    assert r.status_code == 200, r.text
    assert r.json()["dashboard_url"].endswith("/precos?escolha=1")
    assert any(h.startswith(f"{dashboard.AUTH_COOKIE_NAME}=") for h in r.headers.get_list("set-cookie"))
    conta = _conta(email)
    assert conta["password_hash"] is None
    assert (conta["display_name"], conta["phone_e164"], conta["dashboard_profile"]) == (NOME, tel, "dividas")
    assert conta["signup_quiz"]["versao"] == 1

    # Sem senha, o "esqueci a senha" é a saída: reset → login com a senha nova.
    client, headers = _navegador()
    assert client.post("/auth/forgot-password", headers=headers, json={"email": email}).status_code == 200
    token = env.resets[-1].split("#token=", 1)[1]
    r = client.post("/auth/reset-password", headers=headers, json={"token": token, "new_password": "senhanova123"})
    assert r.status_code == 200, r.text
    r = client.post("/auth/login", headers=headers, json={"email": email, "password": "senhanova123"})
    assert r.status_code == 200, r.text


def test_controle_positivo_register_com_senha_continua_com_senha(env):
    email = _email()
    client, headers = _navegador()
    r = client.post("/auth/register", headers=headers,
                    json={"email": email, "password": "senha12345", "phone": _telefone(), "name": NOME})
    assert r.status_code == 200, r.text
    _, r = _verifica(email, env.enviados[-1][1])
    assert r.status_code == 200, r.text
    assert _conta(email)["password_hash"] is not None
    r = client.post("/auth/login", headers=headers, json={"email": email, "password": "senha12345"})
    assert r.status_code == 200, r.text


def test_nome_e_telefone_invalidos_viram_none_e_o_codigo_sai(env):
    email = _email()
    assert _webhook({"email": email, "nome": "A", "whatsapp": "123"}).status_code == 200
    _, r = _verifica(email, env.enviados[-1][1])
    assert r.status_code == 200, r.text
    conta = _conta(email)
    assert (conta["display_name"], conta["phone_e164"], conta["password_hash"]) == (None, None, None)


def test_reenvio_do_webhook_mantem_o_primeiro_codigo_valido(env):
    email, tel = _email(), _telefone()
    for _ in range(2):
        assert _webhook({"email": email, "nome": NOME, "whatsapp": tel}).status_code == 200
    primeiro, segundo = (c for _, c in env.enviados)
    assert primeiro == segundo and _codigos(email) == 1
    _, r = _verifica(email, primeiro)
    assert r.status_code == 200, r.text


def test_reenvio_do_webhook_com_telefone_e_nome_corrigidos_vale_o_corrigido(env):
    email, tel_errado, tel = _email(), _telefone(), _telefone()
    _webhook({"email": email, "nome": "Nome Errado", "whatsapp": tel_errado})
    _webhook({"email": email, "nome": NOME, "whatsapp": tel})
    primeiro, segundo = (c for _, c in env.enviados)
    assert primeiro == segundo and _codigos(email) == 1
    _, r = _verifica(email, primeiro)
    assert r.status_code == 200, r.text
    conta = _conta(email)
    assert (conta["display_name"], conta["phone_e164"]) == (NOME, tel)


@pytest.mark.parametrize("segundo", [
    {"nome": "A", "whatsapp": "123"},  # inválidos viram None
    {},                                 # ausentes
    "disputado",                        # telefone de outra conta: descartado
], ids=["invalidos", "ausentes", "telefone_disputado"])
def test_reenvio_do_webhook_sem_dado_valido_nao_apaga_o_anterior(env, segundo):
    email, tel = _email(), _telefone()
    if segundo == "disputado":
        outro, tel_outro = _email(), _telefone()
        db.confirm_email_verification(outro, db.create_email_verification(outro, "senha12345", tel_outro))
        env.enviados.clear()
        segundo = {"whatsapp": tel_outro}
    _webhook({"email": email, "nome": NOME, "whatsapp": tel})
    _webhook({"email": email, **segundo})
    primeiro, repetido = (c for _, c in env.enviados)
    assert primeiro == repetido and _codigos(email) == 1
    _, r = _verifica(email, primeiro)
    assert r.status_code == 200, r.text
    conta = _conta(email)
    assert (conta["display_name"], conta["phone_e164"]) == (NOME, tel)


# ── Token ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("headers,no_corpo", [
    ({"Authorization": f"Bearer {TOKEN}"}, None),
    ({"Authorization": f"bearer {TOKEN}"}, None),
    ({"Authorization": TOKEN}, None),
    ({"Token": TOKEN}, None),
    ({"X-Token": TOKEN}, None),
    ({}, TOKEN),
], ids=["bearer", "bearer_minusculo", "authorization_cru", "token", "x_token", "corpo"])
def test_token_aceito_nos_lugares_combinados(env, headers, no_corpo):
    email = _email()
    corpo = {"email": email, **({"token": no_corpo} if no_corpo else {})}
    assert _webhook(corpo, headers).status_code == 200
    assert [to for to, _ in env.enviados] == [email]


@pytest.mark.parametrize("headers,no_corpo", [
    ({"Authorization": "Bearer errado"}, None),
    ({"Token": TOKEN + "x"}, None),
    ({}, "errado"),
    ({}, None),
    ({"Authorization": "Bearer "}, ""),
], ids=["bearer_errado", "token_errado", "corpo_errado", "sem_token", "vazio"])
def test_token_errado_401_sem_linha_nem_envio(env, headers, no_corpo):
    email = _email()
    corpo = {"email": email, **({"token": no_corpo} if no_corpo is not None else {})}
    assert _webhook(corpo, headers).status_code == 401
    assert _codigos(email) == 0 and env.enviados == []


def test_sem_env_503(env, monkeypatch):
    monkeypatch.delenv("XQUIZ_WEBHOOK_TOKEN")
    email = _email()
    assert _webhook({"email": email}).status_code == 503
    assert _codigos(email) == 0


# ── Anti-enumeração, limites e log ───────────────────────────────────────────

def test_email_existente_mesma_resposta_sem_linha_e_avisa_o_dono(env):
    email = _email()
    db.confirm_email_verification(email, db.create_email_verification(email, "senha12345", _telefone()))
    antes = _codigos(email)
    r = _webhook({"email": email, "nome": NOME})
    assert (r.status_code, r.json()) == (200, {"ok": True})
    assert _codigos(email) == antes and env.enviados == []
    assert len(env.avisos) == 1 and env.avisos[0][0] == email
    assert env.avisos[0][2].endswith("/recuperar-senha")


def test_teto_por_email(env):
    email = _email()
    assert [_webhook({"email": email}).status_code for _ in range(4)] == [200, 200, 200, 429]


def test_teto_global_independe_de_ip_e_de_email(env):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("insert into auth_rate_limits (bucket, identifier, window_started_at, attempts, updated_at) "
                    "values ('quiz-webhook', 'global', now(), %s, now())", (quiz_signup.TETO_GLOBAL_WEBHOOK[0],))
        conn.commit()
    email = _email()
    assert _webhook({"email": email}).status_code == 429
    assert _codigos(email) == 0
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) as n from auth_rate_limits where bucket in ('register', 'quiz-webhook') "
                    "and identifier like 'ip:%%'")
        assert cur.fetchone()["n"] == 0, "o webhook não pode contar por IP: todo pedido vem do XQuiz"
        conn.commit()


def test_falha_de_envio_ou_banco_503_sem_pii_e_o_retry_reaproveita_o_codigo(env, monkeypatch, capsys, caplog):
    """200 na falha = o XQuiz não reenvia e o cadastro some."""
    email, tel = _email(), _telefone()
    corpo, smtp_no_ar = {"email": email, "nome": NOME, "whatsapp": tel}, []
    criar = quiz_signup.create_email_verification
    monkeypatch.setattr(quiz_signup, "send_verification_email",
                        lambda to, code: env.enviados.append((to, code)) or bool(smtp_no_ar))
    respostas = [_webhook(corpo)]  # o envio falha: a linha fica
    assert _codigos(email) == 1

    def explode(*_a, **_kw):
        raise Exception(f"Failing row contains ({email}, {tel}, {NOME})")

    monkeypatch.setattr(quiz_signup, "create_email_verification", explode)
    respostas.append(_webhook(corpo))  # o banco cai
    assert [(r.status_code, r.json()) for r in respostas] == [(503, {"ok": False})] * 2
    assert len(env.eventos) == 2
    texto = repr(env.eventos) + "".join(r.text for r in respostas) + capsys.readouterr().out + caplog.text
    assert [s for s in (email, tel, NOME, env.enviados[0][1]) if s in texto] == [], texto

    monkeypatch.setattr(quiz_signup, "create_email_verification", criar)
    smtp_no_ar.append(1)
    r = _webhook(corpo)  # o retry do XQuiz
    assert (r.status_code, r.json()) == (200, {"ok": True})
    primeiro, segundo = (c for _, c in env.enviados)
    assert primeiro == segundo and _codigos(email) == 1
    _, r = _verifica(email, segundo)
    assert r.status_code == 200, r.text


# ── Reenvio pela /q ──────────────────────────────────────────────────────────

def test_resend_com_codigo_vencido_gera_novo_com_mesmo_nome_e_telefone(env):
    email, tel = _email(), _telefone()
    _webhook({"email": email, "nome": NOME, "whatsapp": tel})
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("update email_verification_codes set expires_at = now() - interval '1 minute' "
                    "where email_hash = %s", (hash_pii_optional(email, kind="email"),))
        conn.commit()
    client, headers = _navegador()
    r = client.post("/auth/quiz/resend", headers=headers, json={"email": email})
    assert (r.status_code, r.json()) == (200, {"ok": True})
    assert len(env.enviados) == 2 and _codigos(email) == 2
    _, r = _verifica(email, env.enviados[-1][1])
    assert r.status_code == 200, r.text
    conta = _conta(email)
    assert (conta["display_name"], conta["phone_e164"], conta["password_hash"]) == (NOME, tel, None)


def test_resend_sem_quiz_mesma_resposta_e_nada_enviado(env):
    client, headers = _navegador()
    r = client.post("/auth/quiz/resend", headers=headers, json={"email": _email()})
    assert (r.status_code, r.json()) == (200, {"ok": True})
    assert env.enviados == []


def test_resend_exige_csrf(env):
    client, _ = _navegador()
    assert client.post("/auth/quiz/resend", json={"email": _email()}).status_code == 403


@pytest.mark.parametrize("com_quiz", [True, False], ids=["com_quiz", "sem_quiz"])
def test_resend_responde_sem_esperar_o_envio(env, monkeypatch, com_quiz):
    """Oráculo de tempo: se a resposta esperasse o SMTP só de quem fez o quiz, a
    latência revelaria a conta. Mede no ASGI cru o instante do último byte da
    resposta — o TestClient só devolve depois das background tasks."""
    import asyncio
    import time
    email = _email()
    if com_quiz:
        _webhook({"email": email, "nome": NOME})
    inicio_envio = []

    def _envio_lento(to, code):
        inicio_envio.append(time.monotonic())
        time.sleep(1)
        env.enviados.append((to, code))
        return True

    monkeypatch.setattr(quiz_signup, "send_verification_email", _envio_lento)
    corpo = f'{{"email": "{email}"}}'.encode()
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
        "scheme": "http", "path": "/auth/quiz/resend", "raw_path": b"/auth/quiz/resend",
        "query_string": b"", "root_path": "", "client": ("testclient", 50000), "server": ("testserver", 80),
        "headers": [(b"host", b"testserver"), (b"content-type", b"application/json"),
                    (b"content-length", str(len(corpo)).encode()),
                    (b"cookie", f"{dashboard.CSRF_COOKIE_NAME}=csrf-quiz".encode()),
                    (dashboard.CSRF_HEADER_NAME.lower().encode(), b"csrf-quiz")],
    }
    mensagens, respondido = [], []

    async def receive():
        return {"type": "http.request", "body": corpo, "more_body": False}

    async def send(msg):
        mensagens.append(msg)
        if msg["type"] == "http.response.body" and not msg.get("more_body"):
            respondido.append(time.monotonic())

    inicio = time.monotonic()
    asyncio.run(dashboard.app(scope, receive, send))
    assert mensagens[0]["status"] == 200 and b"".join(m.get("body", b"") for m in mensagens[1:]) == b'{"ok":true}'
    assert respondido[0] - inicio < 0.5, f"a resposta esperou {respondido[0] - inicio:.2f}s"
    if com_quiz:  # e o envio aconteceu, depois (o sleep de 1s não entrou na resposta)
        assert len(env.enviados) == 2 and len(inicio_envio) == 1
    else:
        assert env.enviados == []
