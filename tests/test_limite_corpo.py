"""Teto e prazo do corpo — `core/limite_corpo.py` (plano: docs/plano-limite-corpo.md).

A. Unitários num app mínimo (FastAPI + dois BaseHTTPMiddleware externos, como no
   app real), com chamadas ASGI cruas: o `receive` falso conta o que foi puxado.
B. Ponta a ponta no `dashboard.app` real.

Controle negativo (CLAUDE.md §3): com a linha `app.add_middleware(LimiteCorpoMiddleware)`
do monólito comentada, os testes `test_e2e_*` negativos ficam vermelhos e os
`test_e2e_positivo_*` continuam verdes. Dois grupos têm controle próprio:
`test_app_que_ja_respondeu_nao_recebe_segundo_start` (trocar a guarda
`if not app_comecou:` de `recusar` por `if True:` deixa os dois casos vermelhos, e por
`if not app_comecou or status == 408:` só o `408_prazo`) e
`test_e2e_auth_login_413_passa_pelo_cors` (controle: registrar o middleware como o
mais externo, por fora do CORSMiddleware).
"""
import asyncio
import hashlib
import hmac
import json
import time
import uuid

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

import core.limite_corpo as limite_corpo
from core.limite_corpo import MAX_OFX_BYTES, MiB, LimiteCorpoMiddleware, limites_da_rota

DETALHE_413 = "Corpo da requisição grande demais."
PEDACO = 64 * 1024


# ─── A. unitários ────────────────────────────────────────────────────────────

class _Receive:
    """`receive` falso: entrega as mensagens do gerador assíncrono e conta os
    bytes puxados. Gerador esgotado = cliente em silêncio para sempre."""

    def __init__(self, mensagens):
        self.mensagens = mensagens
        self.puxados = 0
        self.chamadas = 0

    async def __call__(self):
        self.chamadas += 1
        try:
            msg = await anext(self.mensagens)
        except StopAsyncIteration:
            await asyncio.Event().wait()
        self.puxados += len(msg.get("body", b""))
        return msg


async def _corpo(dados: bytes):
    yield {"type": "http.request", "body": dados, "more_body": False}


async def _infinito():
    while True:
        yield {"type": "http.request", "body": b"x" * PEDACO, "more_body": True}


async def _um_pedaco_e_silencio():
    yield {"type": "http.request", "body": b"x" * 10, "more_body": True}


async def _lento(pedacos: int, intervalo: float):
    for i in range(pedacos):
        await asyncio.sleep(intervalo)
        yield {"type": "http.request", "body": b"x" * 10, "more_body": i < pedacos - 1}


async def _corpo_e_disconnect_tardio(espera: float):
    yield {"type": "http.request", "body": b"abc", "more_body": False}
    await asyncio.sleep(espera)
    yield {"type": "http.disconnect"}


def _app_minimo():
    app = FastAPI()
    vistos: list[BaseException] = []

    @app.post("/eco")
    async def eco(request: Request):
        return {"n": len(await request.body())}

    @app.post("/engole")
    async def engole(request: Request):
        try:
            await request.body()
        except Exception:
            return JSONResponse({"detail": "engoli"}, status_code=400)
        return {"ok": True}

    @app.post("/espera")
    async def espera(request: Request):
        n = len(await request.body())
        depois = await request.receive()
        return {"n": n, "depois": depois["type"]}

    app.add_middleware(LimiteCorpoMiddleware)

    @app.middleware("http")
    async def registra(request, call_next):
        try:
            return await call_next(request)
        except Exception as exc:
            vistos.append(exc)
            raise

    @app.middleware("http")
    async def externo(request, call_next):
        resposta = await call_next(request)
        resposta.headers["x-externo"] = "1"
        return resposta

    return app, vistos


def _scope(path: str, headers=()):
    return {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "http", "path": path, "raw_path": path.encode(),
        "query_string": b"", "root_path": "", "client": ("127.0.0.1", 1),
        "server": ("teste", 80), "headers": [(b"host", b"teste"), *headers],
    }


def _chamar(receive: _Receive, path="/eco", headers=()):
    app, vistos = _app_minimo()
    enviados = []

    async def send(msg):
        enviados.append(msg)

    t0 = time.monotonic()
    asyncio.run(asyncio.wait_for(app(_scope(path, headers), receive, send), 2))
    dt = time.monotonic() - t0
    start = next(m for m in enviados if m["type"] == "http.response.start")
    cabecalhos = {k.decode().lower(): v.decode() for k, v in start["headers"]}
    corpo = b"".join(m.get("body", b"") for m in enviados if m["type"] == "http.response.body")
    return start["status"], cabecalhos, corpo, dt, vistos


def _cl(n) -> tuple:
    return ((b"content-length", str(n).encode()),)


def test_content_length_acima_do_teto_413_sem_puxar_nada():
    teto = limites_da_rota("/eco")[0]
    rcv = _Receive(_infinito())
    status, cab, corpo, _, vistos = _chamar(rcv, headers=_cl(teto + 1))
    assert status == 413
    assert cab["connection"] == "close"
    assert cab["cache-control"] == "no-store"
    assert json.loads(corpo) == {"detail": DETALHE_413}
    assert cab["x-externo"] == "1"  # passou pelos BaseHTTPMiddleware de fora
    assert rcv.chamadas == 0 and rcv.puxados == 0
    assert vistos == []


def test_chunked_infinito_413_rapido():
    teto = limites_da_rota("/eco")[0]
    rcv = _Receive(_infinito())
    status, _, _, dt, vistos = _chamar(rcv)
    assert status == 413
    assert rcv.puxados <= teto + PEDACO
    assert dt < 1
    assert vistos == []


def test_content_length_mentindo_413():
    rcv = _Receive(_infinito())
    status, _, _, _, vistos = _chamar(rcv, headers=_cl(10))
    assert status == 413
    assert vistos == []


def test_exatamente_no_teto_passa_e_um_byte_a_mais_413():
    teto = limites_da_rota("/eco")[0]
    status, _, corpo, _, _ = _chamar(_Receive(_corpo(b"x" * teto)))
    assert (status, json.loads(corpo)) == (200, {"n": teto})
    status, _, _, _, vistos = _chamar(_Receive(_corpo(b"x" * (teto + 1))))
    assert status == 413
    assert vistos == []


def test_um_pedaco_e_silencio_408(monkeypatch):
    monkeypatch.setattr(limite_corpo, "PADRAO", (1 * MiB, 0.05))
    status, cab, _, dt, vistos = _chamar(_Receive(_um_pedaco_e_silencio()))
    assert status == 408
    assert cab["connection"] == "close"
    assert dt < 1
    assert vistos == []


def test_pedacos_lentos_dentro_do_prazo_passam(monkeypatch):
    """Controle positivo do 408: o prazo é do corpo inteiro, não de cada pedaço."""
    monkeypatch.setattr(limite_corpo, "PADRAO", (1 * MiB, 0.5))
    status, _, corpo, _, _ = _chamar(_Receive(_lento(3, 0.02)))
    assert (status, json.loads(corpo)) == (200, {"n": 30})


def test_sem_prazo_depois_do_corpo_completo(monkeypatch):
    """O `receive` depois de more_body=False espera o disconnect além do prazo."""
    monkeypatch.setattr(limite_corpo, "PADRAO", (1 * MiB, 0.05))
    status, _, corpo, _, _ = _chamar(_Receive(_corpo_e_disconnect_tardio(0.2)), path="/espera")
    assert (status, json.loads(corpo)) == (200, {"n": 3, "depois": "http.disconnect"})


def test_handler_com_except_exception_nao_troca_o_413():
    teto = limites_da_rota("/engole")[0]
    status, _, corpo, _, vistos = _chamar(_Receive(_corpo(b"x" * (teto + 1))), path="/engole")
    assert status == 413
    assert json.loads(corpo) == {"detail": DETALHE_413}
    assert vistos == []


def test_websocket_e_lifespan_passam_intactos():
    for tipo in ("websocket", "lifespan"):
        visto = {}

        async def interno(scope, receive, send):
            visto.update(scope=scope, receive=receive, send=send)

        async def receive():
            return {}

        async def send(_):
            pass

        scope = {"type": tipo, "path": "/ws/1", "headers": [(b"content-length", b"99999999999")]}
        asyncio.run(LimiteCorpoMiddleware(interno)(scope, receive, send))
        assert visto == {"scope": scope, "receive": receive, "send": send}


@pytest.mark.parametrize("mensagens, prazo", [
    (_infinito, limite_corpo.PADRAO[1]), (_um_pedaco_e_silencio, 0.05),
], ids=["413_tamanho", "408_prazo"])
def test_app_que_ja_respondeu_nao_recebe_segundo_start(mensagens, prazo, monkeypatch):
    """App que manda o start antes de ler o corpo: estourar o teto ou o prazo
    depois não pode gerar um segundo `http.response.start` (protocolo ASGI quebrado)."""
    monkeypatch.setattr(limite_corpo, "PADRAO", (limite_corpo.PADRAO[0], prazo))

    async def responde_antes_de_ler(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        while (await receive())["type"] == "http.request":
            pass
        await send({"type": "http.response.body", "body": b"fim"})

    enviados = []

    async def send(msg):
        enviados.append(msg)

    asyncio.run(asyncio.wait_for(
        LimiteCorpoMiddleware(responde_antes_de_ler)(_scope("/eco"), _Receive(mensagens()), send), 2))
    assert [m.get("status") for m in enviados if m["type"] == "http.response.start"] == [200]


@pytest.mark.parametrize("valor", [b"abc", b"-1", b"9" * 5000], ids=["abc", "-1", "5000_digitos"])
def test_content_length_invalido_nao_da_500(valor):
    teto = limites_da_rota("/eco")[0]
    status, _, _, _, _ = _chamar(_Receive(_corpo(b"x" * 5)), headers=((b"content-length", valor),))
    assert status == 200
    status, _, _, _, vistos = _chamar(_Receive(_corpo(b"x" * (teto + 1))),
                                      headers=((b"content-length", valor),))
    assert status == 413
    assert vistos == []


@pytest.mark.parametrize("path, faixa", [
    ("/wa/webhook", "WEBHOOK"), ("/webhook", "WEBHOOK"),
    ("/open-finance/pluggy/webhook", "WEBHOOK"), ("/billing/webhook", "WEBHOOK"),
    ("/billing/asaas/webhook", "WEBHOOK"), ("/xquiz/webhook", "WEBHOOK"),
    ("/ofx/import/7", "OFX"),
    ("/ofx/importx", "PADRAO"), ("/auth/login", "PADRAO"), ("/wa/webhook/x", "PADRAO"),
])
def test_limites_da_rota(path, faixa):
    assert limites_da_rota(path) == getattr(limite_corpo, faixa)


def test_agent_chat_body_no_pior_caso_cabe_no_teto_padrao():
    """6 B por caractere (`\\u00XX`, o pior que um JSON.stringify emite) nos dois campos."""
    from frontend.routes.agents import AgentChatBody

    def maximo(campo):
        return next(m.max_length for m in AgentChatBody.model_fields[campo].metadata
                    if hasattr(m, "max_length"))

    chaves = len(json.dumps({"message": "", "context": ""}).encode())
    pior = chaves + 6 * (maximo("message") + maximo("context"))
    assert pior <= limite_corpo.PADRAO[0]


def test_todo_caminho_de_webhook_e_rota_post_do_app():
    # Reuso: a descida pelos `_IncludedRouter` do FastAPI 0.141 já existe lá.
    from test_rotas_senha_obrigatoria import _rotas

    posts = {path for metodo, path, _ in _rotas() if metodo == "POST"}
    assert limite_corpo.CAMINHOS_WEBHOOK <= posts, limite_corpo.CAMINHOS_WEBHOOK - posts
    assert any(p.startswith(limite_corpo.PREFIXO_OFX) for p in posts)


def test_importadores_de_ofx_usam_o_teto_daqui():
    """§0.7: `is`, não `==` — um literal reintroduzido é outro objeto int mesmo
    com o mesmo valor, então este teste pega a cópia antes de ela divergir."""
    import ofx_credit_import
    import ofx_import

    assert ofx_import.MAX_OFX_BYTES is limite_corpo.MAX_OFX_BYTES
    assert ofx_credit_import.MAX_OFX_BYTES is limite_corpo.MAX_OFX_BYTES


# ─── B. ponta a ponta no dashboard.app real ──────────────────────────────────

import frontend.finance_bot_websocket_custom as dashboard  # noqa: E402

TETO_PADRAO = limite_corpo.PADRAO[0]
TETO_WEBHOOK = limite_corpo.WEBHOOK[0]
JSON_CT = {"Content-Type": "application/json"}


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Mesmo padrão de tests/test_ofx_import_route.py: storage do slowapi em memória."""
    try:
        dashboard.limiter._storage.reset()
    except Exception:
        pass
    yield


def _json_com(n: int, **campos) -> bytes:
    """JSON de exatamente `n` bytes; o campo `pad` completa."""
    vazio = len(json.dumps({**campos, "pad": ""}).encode())
    corpo = json.dumps({**campos, "pad": "x" * (n - vazio)}).encode()
    assert len(corpo) == n
    return corpo


@pytest.fixture
def wa(monkeypatch):
    """Webhook do WhatsApp isolado como em tests/test_wa_webhook_signature.py."""
    import adapters.whatsapp.wa_app as wa_app
    from test_wa_webhook_signature import SEGREDO

    monkeypatch.setattr(wa_app, "APP_SECRET", SEGREDO)
    monkeypatch.setattr(wa_app, "log_system_event_sync", lambda *a, **k: None)
    fila = asyncio.Queue(maxsize=500)
    monkeypatch.setattr(wa_app, "_queue", fila)

    def assinar(corpo: bytes) -> dict:
        digest = hmac.new(SEGREDO.encode(), corpo, hashlib.sha256).hexdigest()
        return {"x-hub-signature-256": f"sha256={digest}", **JSON_CT}

    return fila, assinar


def test_e2e_auth_login_acima_do_teto_413_com_cabecalhos_de_seguranca():
    """Sem o middleware: 422/401 depois de ler o corpo inteiro."""
    r = TestClient(dashboard.app).post(
        "/auth/login", content=_json_com(TETO_PADRAO + 1, email="a@b.com", password="x"),
        headers=JSON_CT)
    assert r.status_code == 413, r.text
    assert r.json() == {"detail": DETALHE_413}
    assert r.headers["x-frame-options"]  # passou pelo security_headers_middleware de fora
    assert r.headers["connection"] == "close"


def test_e2e_auth_login_chunked_413():
    def gerador():
        corpo = _json_com(TETO_PADRAO + 1, email="a@b.com", password="x")
        for i in range(0, len(corpo), PEDACO):
            yield corpo[i:i + PEDACO]

    r = TestClient(dashboard.app).post("/auth/login", content=gerador(), headers=JSON_CT)
    assert r.status_code == 413, r.text


@pytest.mark.parametrize("origem, permitida", [
    ("https://pigbankai.com", True),  # allow_origins do CORSMiddleware do monólito
    ("https://malicioso.example", False),
])
def test_e2e_auth_login_413_passa_pelo_cors(origem, permitida):
    """O middleware é o mais interno: o 413 dele atravessa o CORSMiddleware,
    senão o navegador da origem permitida não consegue ler o erro."""
    r = TestClient(dashboard.app).post(
        "/auth/login", content=_json_com(TETO_PADRAO + 1, email="a@b.com", password="x"),
        headers={"Origin": origem, **JSON_CT})
    assert r.status_code == 413, r.text
    assert r.headers.get("access-control-allow-origin") == (origem if permitida else None)


def test_e2e_push_register_anonimo_413_antes_da_autenticacao():
    """Corpo válido para o modelo: sem o middleware o FastAPI o lê inteiro e só
    então o handler responde 401."""
    r = TestClient(dashboard.app).post(
        "/api/push/register", content=_json_com(TETO_PADRAO + 1, token="t"), headers=JSON_CT)
    assert r.status_code == 413, r.text


def test_e2e_wa_webhook_acima_do_teto_413(wa):
    fila, assinar = wa
    corpo = _json_com(TETO_WEBHOOK + 1, entry=[])
    r = TestClient(dashboard.app).post("/wa/webhook", content=corpo, headers=assinar(corpo))
    assert r.status_code == 413, r.text
    assert fila.qsize() == 0


def test_e2e_asaas_acima_do_teto_413_mesmo_com_except_exception(monkeypatch):
    """O handler do Asaas envolve o `request.json()` num `except Exception` → 400."""
    from test_pix_rotas_billing import TOKEN

    monkeypatch.setenv("ASAAS_WEBHOOK_TOKEN", TOKEN)
    r = TestClient(dashboard.app).post(
        "/billing/asaas/webhook", content=_json_com(TETO_WEBHOOK + 1, event="PAYMENT_RECEIVED"),
        headers={"asaas-access-token": TOKEN, **JSON_CT})
    assert r.status_code == 413, r.text
    assert r.json() == {"detail": DETALHE_413}


def test_e2e_ofx_acima_do_teto_413_do_middleware(pro_user_id):
    """Sem o middleware sai 413 também, mas o do handler ("max 8 MB"), depois de
    ler tudo: o `detail` é o que discrimina."""
    from test_ofx_import_route import _upload

    teto = limites_da_rota(f"/ofx/import/{pro_user_id}")[0]
    r = _upload(pro_user_id, arquivo=("extrato.ofx", b"x" * teto, "application/x-ofx"))
    assert r.status_code == 413, r.text
    assert r.json() == {"detail": DETALHE_413}


def test_e2e_wa_webhook_um_pedaco_e_silencio_408(wa, monkeypatch):
    """Sem o middleware o `wait_for` estoura: o app espera o corpo para sempre."""
    monkeypatch.setattr(limite_corpo, "WEBHOOK", (TETO_WEBHOOK, 0.05))
    enviados = []

    async def send(msg):
        enviados.append(msg)

    t0 = time.monotonic()
    asyncio.run(asyncio.wait_for(
        dashboard.app(_scope("/wa/webhook", [(b"content-type", b"application/json")]),
                      _Receive(_um_pedaco_e_silencio()), send), 2))
    assert time.monotonic() - t0 < 1
    start = next(m for m in enviados if m["type"] == "http.response.start")
    assert start["status"] == 408
    assert (b"connection", b"close") in start["headers"]


def test_e2e_positivo_wa_webhook_assinado_exatamente_no_teto_e_acima_do_padrao(wa):
    """3 MiB é o máximo documentado pela Meta; 1,5 MiB passaria do teto padrão,
    então prova também que o caminho cai na faixa webhook."""
    from test_wa_webhook_signature import PAYLOAD_META

    fila, assinar = wa
    for tamanho in (TETO_WEBHOOK, TETO_WEBHOOK // 2):
        corpo = _json_com(tamanho, **PAYLOAD_META)
        r = TestClient(dashboard.app).post("/wa/webhook", content=corpo, headers=assinar(corpo))
        assert r.status_code == 200, r.text
        assert fila.get_nowait()["entry"] == PAYLOAD_META["entry"]


def test_e2e_positivo_pluggy_transactions_deleted_50_mil_ids(monkeypatch):
    monkeypatch.setenv("PLUGGY_WEBHOOK_SECRET", "test-webhook-secret")
    monkeypatch.setattr("frontend.routes.open_finance._schedule_pluggy_sync", lambda i: None)
    corpo = json.dumps({"event": "transactions/deleted", "itemId": "item-limite-corpo",
                        "transactionIds": [str(uuid.uuid4()) for _ in range(50_000)]}).encode()
    assert TETO_PADRAO < len(corpo) <= TETO_WEBHOOK
    r = TestClient(dashboard.app).post(
        "/open-finance/pluggy/webhook?token=test-webhook-secret", content=corpo, headers=JSON_CT)
    assert r.status_code == 200, r.text


def test_e2e_positivo_stripe_assinatura_ruim_continua_400(monkeypatch):
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_xxx")
    monkeypatch.setattr(dashboard, "STRIPE_WEBHOOK_SECRET", "whsec_test")
    r = TestClient(dashboard.app).post(
        "/billing/webhook", content=_json_com(TETO_WEBHOOK // 2, id="evt"),
        headers={"stripe-signature": "bad", **JSON_CT})
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "Assinatura inválida."


def test_e2e_positivo_asaas_acima_do_padrao_nao_413(monkeypatch):
    """Sem `id` de evento de propósito: o handler lê o corpo inteiro e responde
    400 sem gravar nem drenar nada."""
    from test_pix_rotas_billing import TOKEN

    monkeypatch.setenv("ASAAS_WEBHOOK_TOKEN", TOKEN)
    r = TestClient(dashboard.app).post(
        "/billing/asaas/webhook", content=_json_com(TETO_WEBHOOK // 2, event="PAYMENT_RECEIVED"),
        headers={"asaas-access-token": TOKEN, **JSON_CT})
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "Webhook sem id de evento."


def test_e2e_positivo_ofx_exatamente_max_ofx_bytes_chega_ao_handler(pro_user_id):
    from test_category_normalization import _OFX_BANCO
    from test_ofx_import_route import _upload

    ofx = _OFX_BANCO.format(acct=pro_user_id % 100000).encode()
    arquivo = ofx + b"\n" * (MAX_OFX_BYTES - len(ofx))
    multipart = httpx.Request("POST", "http://t/ofx/import/1",
                              files={"file": ("extrato.ofx", arquivo, "application/x-ofx")})
    overhead = len(multipart.read()) - len(arquivo)
    assert 0 < overhead < 64 * 1024, overhead  # a folga do teto do OFX

    r = _upload(pro_user_id, arquivo=("extrato.ofx", arquivo, "application/x-ofx"))
    assert r.status_code == 200, r.text  # passou também pela checagem pós-leitura do handler
    assert r.json()["type"] == "bank"


def test_e2e_positivo_auth_login_normal_mesmo_status():
    r = TestClient(dashboard.app).post(
        "/auth/login", json={"email": "ninguem-limite-corpo@t.com", "password": "errada"})
    assert r.status_code == 401, r.text
