"""O envio de e-mail do /auth/register e do /auth/forgot-password sai do event loop.

O deploy tem um worker só: um SMTP síncrono dentro do handler `async def` congela
todas as requisições enquanto o servidor de e-mail responde. Cada chamada
bloqueante mockada dorme 1 s; um batimento de 0,05 s no mesmo loop cobre o pedido
inteiro e mede o maior intervalo entre batidas. ASGI cru e não TestClient: o
TestClient roda o app em outra thread e não mede o loop do teste.

Controles (medidos no PR, maior intervalo ~1,0 s em cada um, 0,05 s com o
conserto): chamada direta do `send_verification_email` no register → T1[novo]
vermelho, T1[existente] segue verde (esse caminho já usava `to_thread`); chamada
direta do `send_password_reset_email`, do `create_password_reset_token` ou do
`email_has_password` no forgot-password → T3 vermelho.
Positivo: o código enviado no T1[novo] confirma a conta no verify-email.

#681: o bcrypt do /auth/reset-password, do /auth/login e do /admin/auth/login
também sai do loop. Como o conftest força bcrypt de custo 4 (rápido), quem prova o
bloqueio é o mock que dorme 1 s no lugar da função síncrona. Controles: chamada
direta de `consume_password_reset_token`, de `login_auth_user` ou de
`_check_admin_password` → o teste da rota fica vermelho (o maior intervalo entre
batidas encosta no tempo do mock); com `asyncio.to_thread` fica abaixo do teto de
0,5 s. Positivo: `test_reset_e_login_reais`.
"""
import asyncio
import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
from core.services import email_service
from db_support import EMAIL_JA_TEM_CONTA
from _apoio_auth_app import csrf, limpa_rate_limits

SENHA = "senha-forte-123"
CSRF = "csrf-email-fora-do-loop"


async def _post(caminho, dados):  # ASGI cru: o conftest bloqueia o httpx async
    corpo_b = json.dumps(dados).encode()
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
        "scheme": "http", "path": caminho, "raw_path": caminho.encode(), "query_string": b"",
        "root_path": "", "client": ("testclient", 50000), "server": ("testserver", 80),
        "headers": [(b"host", b"testserver"), (b"content-type", b"application/json"),
                    (b"content-length", str(len(corpo_b)).encode()),
                    (b"cookie", f"{dashboard.CSRF_COOKIE_NAME}={CSRF}".encode()),
                    (dashboard.CSRF_HEADER_NAME.lower().encode(), CSRF.encode())],
    }
    msgs = []

    async def receive():
        return {"type": "http.request", "body": corpo_b, "more_body": False}

    async def send(msg):
        msgs.append(msg)

    await dashboard.app(scope, receive, send)
    return msgs[0]["status"], json.loads(b"".join(m.get("body", b"") for m in msgs[1:]))


def _lento(chamadas, devolve=True):
    def _chamada(*args):
        time.sleep(1.0)
        chamadas.append(args)
        return devolve
    return _chamada


def _sem_travar_o_loop(caminho, corpo):
    """Batimento de 0,05 s no mesmo loop durante o pedido inteiro.

    Qualquer chamada lenta (1 s) feita direto no loop vira um intervalo >= 1 s
    entre batidas; em thread, o maior intervalo fica no custo do que ainda roda
    no loop (banco, bcrypt). A duração >= 1 s prova que o mock lento rodou.
    """
    async def _cenario():
        pedido = asyncio.create_task(_post(caminho, corpo))
        inicio = ultima = time.monotonic()
        maior = 0.0
        while not pedido.done():
            await asyncio.sleep(0.05)
            agora = time.monotonic()
            maior, ultima = max(maior, agora - ultima), agora
        return maior, time.monotonic() - inicio, await pedido

    maior, duracao, resposta = asyncio.run(_cenario())
    assert maior < 0.5, f"o loop ficou preso {maior:.2f}s"
    assert duracao >= 1.0, f"o pedido levou {duracao:.2f}s: o mock lento não rodou"
    return resposta


def _email(bucket):
    email = f"email-loop-{uuid.uuid4().hex[:10]}@example.com"
    limpa_rate_limits(bucket, email)
    return email


def _telefone():
    return f"55119{uuid.uuid4().int % 100_000_000:08d}"


@pytest.mark.parametrize("conta", ["novo", "existente"])
def test_register_envia_o_codigo_fora_do_event_loop(monkeypatch, conta):
    verificacoes, avisos = [], []
    monkeypatch.setattr(email_service, "send_verification_email", _lento(verificacoes))
    monkeypatch.setattr(email_service, "send_account_exists_notice", _lento(avisos))
    monkeypatch.setattr(email_service, "send_welcome_email", lambda *a, **k: True)
    email = _email("register")
    if conta == "existente":
        db.register_auth_user(email, SENHA)

    status, corpo = _sem_travar_o_loop(
        "/auth/register", {"email": email, "password": SENHA, "phone": _telefone()}
    )

    if conta == "existente":
        assert (status, corpo) == (409, {"detail": EMAIL_JA_TEM_CONTA})
        assert (len(avisos), verificacoes) == (1, [])
        return
    assert (status, corpo) == (200, {"status": "verification_sent", "email": email})
    assert avisos == [] and [to for to, _ in verificacoes] == [email]
    limpa_rate_limits("verify-email", email)
    client = TestClient(dashboard.app)
    r = client.post("/auth/verify-email", headers=csrf(client),
                    json={"email": email, "code": verificacoes[0][1]})
    assert r.status_code == 200, r.text


def test_register_falha_no_envio_continua_500(monkeypatch):
    monkeypatch.setattr(email_service, "send_verification_email", lambda to, code: False)
    email = _email("register")
    status, corpo = asyncio.run(
        _post("/auth/register", {"email": email, "password": SENHA, "phone": _telefone()})
    )
    assert (status, corpo) == (500, {
        "detail": "Não foi possível enviar o e-mail de verificação. Tente novamente."
    })


def test_forgot_password_envia_fora_do_event_loop(monkeypatch):
    enviados = []
    monkeypatch.setattr(db, "create_password_reset_token", _lento([], "tok-x"))
    monkeypatch.setattr(db, "email_has_password", _lento([], False))
    monkeypatch.setattr(email_service, "send_password_reset_email", _lento(enviados))
    email = _email("forgot-password")

    status, corpo = _sem_travar_o_loop("/auth/forgot-password", {"email": email})

    assert (status, corpo) == (200, {
        "message": "Se este e-mail estiver cadastrado, você receberá as instruções em breve."
    })
    assert enviados == [(email, f"{dashboard.DASHBOARD_URL}/reset-password#token=tok-x", False)]


_FRASE_401 = {"detail": "E-mail ou senha incorretos."}


def test_reset_password_consome_o_token_fora_do_event_loop(monkeypatch):
    consumos = []
    monkeypatch.setattr(db, "consume_password_reset_token", _lento(consumos, None))

    status, corpo = _sem_travar_o_loop(
        "/auth/reset-password", {"token": "tok-x", "new_password": "senha-nova-123"}
    )

    assert (status, corpo) == (400, {"detail": "Link inválido ou expirado. Solicite um novo."})
    assert consumos == [("tok-x", "senha-nova-123")]


def test_login_confere_a_senha_fora_do_event_loop(monkeypatch):
    conferidas = []
    monkeypatch.setattr(db, "login_auth_user", _lento(conferidas, None))
    email = _email("login")

    status, corpo = _sem_travar_o_loop("/auth/login", {"email": email, "password": SENHA})

    assert (status, corpo) == (401, _FRASE_401)
    assert conferidas == [(email, SENHA)]


def test_admin_login_confere_a_senha_fora_do_event_loop(monkeypatch):
    import core.admin_dashboard as admin_dashboard

    async def _sem_log(*args, **kwargs):
        return None

    conferidas = []
    monkeypatch.setattr(admin_dashboard, "ADMIN_DASHBOARD_PASSWORD", "senha-admin")
    monkeypatch.setattr(admin_dashboard, "log_system_event", _sem_log)
    monkeypatch.setattr(admin_dashboard, "_check_admin_password", _lento(conferidas, False))

    status, corpo = _sem_travar_o_loop(
        "/admin/auth/login",
        {"username": admin_dashboard.ADMIN_DASHBOARD_USERNAME, "password": "qualquer"},
    )

    assert (status, corpo) == (401, {"detail": "Credenciais inválidas."})
    assert conferidas == [("qualquer",)]


def test_reset_e_login_reais():
    """Positivo: o caminho legítimo segue igual com o bcrypt em thread."""
    email = _email("login")
    db.register_auth_user(email, SENHA)
    token = db.create_password_reset_token(email)
    client = TestClient(dashboard.app)
    headers = csrf(client)

    r = client.post("/auth/reset-password", headers=headers,
                    json={"token": token, "new_password": "senha-nova-456"})
    assert r.status_code == 200, r.text

    r = client.post("/auth/login", headers=headers, json={"email": email, "password": "senha-nova-456"})
    assert r.status_code == 200, r.text
    r = client.post("/auth/login", headers=headers, json={"email": email, "password": SENHA})
    assert (r.status_code, r.json()) == (401, _FRASE_401)
