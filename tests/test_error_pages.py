"""Erro por NAVEGAÇÃO responde HTML; chamada de API continua no JSON de hoje.

Os asserts de `Accept: text/html` são a medição (falham sem a correção); os de
`Accept: */*` são guarda de regressão do contrato JSON (passam antes e depois).
"""

import asyncio
import html as html_lib
import json
import logging
import pathlib
import re
import uuid

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

import core.admin_dashboard as admin_dashboard
import db
import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.shared as shared

HTML = {"Accept": "text/html,application/xhtml+xml"}


def _vary_values(response) -> list[str]:
    """Vary pode vir repetido ou em lista — normaliza pra comparar."""
    headers = response.headers  # httpx.Headers -> get_list; starlette -> getlist
    raw = getattr(headers, "get_list", None) or headers.getlist
    return sorted(v.strip().lower() for item in raw("vary") for v in item.split(",") if v.strip())


def _client() -> TestClient:
    return TestClient(dashboard.app)


def _csrf_headers(client: TestClient) -> dict[str, str]:
    token = "test-csrf-token"
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, token)
    return {dashboard.CSRF_HEADER_NAME: token}


def _reset_rate_limits(*identifiers: str):
    """Zera os DOIS limitadores: o persistente (auth_rate_limits, no Postgres) e
    o in-memory do slowapi — este último é global ao processo e usa a chave
    "testclient", a MESMA de tests/test_auth_cookie.py. Sem o reset no finally,
    este arquivo queimaria a cota do outro na suíte completa."""
    dashboard.limiter.reset()
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "delete from auth_rate_limits where identifier = any(%s)",
                (list(identifiers),),
            )
        conn.commit()


# Comentários que a página serve hoje: NENHUM. Continua sendo allowlist e não
# lista de proibidos — um comentário NOVO reprova seja qual for a redação, que é o
# que a lista de strings ruins de `test_pagina_de_erro_nao_vaza_nota_interna` não
# consegue fazer. O único item que estava aqui (a nota "Placeholders preenchidos
# no servidor…") saiu do error.html e virou comentário em
# `shared.error_page_response`, no ponto que lê e valida o arquivo: nota de dev é
# para quem abre o código, não para quem recebe a resposta de erro.
_COMENTARIOS_SERVIDOS = set()


def _texto_visivel(html_text: str) -> str:
    """O que o usuário LÊ: sem <style>/<script>, sem comentário, sem tag."""
    t = re.sub(r"<(script|style)\b.*?</\1>", " ", html_text, flags=re.S | re.I)
    t = re.sub(r"<!--.*?-->", " ", t, flags=re.S)
    t = re.sub(r"<[^>]+>", " ", t)
    return " ".join(html_lib.unescape(t).split())


def _assert_error_page(response, status_code: int, snippet: str, textos=None):
    assert response.status_code == status_code
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-store"
    assert snippet in response.text
    assert str(status_code) in response.text

    # Invariante no lugar da lista de strings ruins: o texto visível é EXATAMENTE
    # código + título + mensagem + o rótulo do link, e nada mais. Isso vale por
    # todos os `assert "<coisa ruim>" not in text` de uma vez — inclusive por
    # palavra que ninguém pensou em proibir (exc.detail, caminho de fonte, nome de
    # parâmetro interno, a própria nota de desenvolvimento que já foi parar na
    # tela). `textos=` só para o override do kwarg `text=` do error_page_response.
    default = shared._ERROR_DEFAULT_5XX if status_code >= 500 else shared._ERROR_DEFAULT_4XX
    title, message = textos or shared._ERROR_TEXTS.get(status_code, default)
    assert _texto_visivel(response.text) == (
        f"{status_code} — {title} | PigBank {status_code} {title} {message} ← Página inicial"
    )
    # E nada de nota nova escondida em comentário (invisível ao assert acima).
    comentarios = {c.strip() for c in re.findall(r"<!--(.*?)-->", response.text, re.S)}
    assert comentarios <= _COMENTARIOS_SERVIDOS, comentarios - _COMENTARIOS_SERVIDOS


# ─── 404 de rota inexistente ─────────────────────────────────────────────────

def test_rota_inexistente_navegacao_recebe_html():
    _assert_error_page(_client().get("/rota-que-nao-existe", headers=HTML), 404, "não encontrada")


def test_rota_inexistente_api_continua_json():
    response = _client().get("/rota-que-nao-existe")
    assert response.status_code == 404
    assert response.json()["detail"] == "Not Found"


# ─── 404 levantado por rota (HTTPException com detail) ───────────────────────

def test_guia_inexistente_navegacao_recebe_html_generico(monkeypatch):
    monkeypatch.setattr("frontend.routes.static_pages.gate_pro_page", lambda request: None)
    response = _client().get("/blog/guia-que-nao-existe", headers=HTML)
    _assert_error_page(response, 404, "não encontrada")
    assert "Guia não encontrado" not in response.text


def test_guia_inexistente_api_continua_json(monkeypatch):
    monkeypatch.setattr("frontend.routes.static_pages.gate_pro_page", lambda request: None)
    response = _client().get("/blog/guia-que-nao-existe")
    assert response.status_code == 404
    assert response.json()["detail"] == "Guia não encontrado."


# ─── 429 (handler do slowapi) ────────────────────────────────────────────────

def _burn_rate_limit(client: TestClient, email: str, extra_headers: dict):
    for _ in range(3):
        response = client.post(
            "/auth/forgot-password",
            headers={**_csrf_headers(client), **extra_headers},
            json={"email": email},
        )
        assert response.status_code == 200
    return client.post(
        "/auth/forgot-password",
        headers={**_csrf_headers(client), **extra_headers},
        json={"email": email},
    )


@pytest.mark.parametrize("accept_html", [True, False])
def test_429_preserva_retry_after_nos_dois_ramos(monkeypatch, accept_html):
    monkeypatch.setattr(db, "create_password_reset_token", lambda email: None)
    client = _client()
    email = f"errpage-{uuid.uuid4().hex}@example.com"
    _reset_rate_limits("ip:testclient", f"email:{email}")
    try:
        response = _burn_rate_limit(client, email, HTML if accept_html else {})
        assert response.status_code == 429
        assert response.headers["Retry-After"] == "60"
        if accept_html:
            _assert_error_page(response, 429, "Muitas tentativas")
        else:
            assert response.json()["detail"] == dashboard.RATE_LIMIT_DETAIL
    finally:
        _reset_rate_limits("ip:testclient", f"email:{email}")


# ─── 500 (middleware de log do admin, fora do ExceptionMiddleware) ───────────

@pytest.fixture
def boom_route():
    path = f"/__boom-{uuid.uuid4().hex}"

    @dashboard.app.get(path)
    async def _boom():
        raise RuntimeError("estouro proposital")

    yield path
    dashboard.app.router.routes = [
        r for r in dashboard.app.router.routes if getattr(r, "path", None) != path
    ]


@pytest.mark.parametrize("accept_html", [True, False])
def test_500_html_na_navegacao_json_na_api_e_log_preservado(monkeypatch, boom_route, accept_html):
    logged = []

    async def _spy(*args, **kwargs):
        logged.append((args, kwargs))

    monkeypatch.setattr(admin_dashboard, "log_system_event", _spy)

    response = TestClient(dashboard.app, raise_server_exceptions=False).get(
        boom_route, headers=HTML if accept_html else {}
    )

    assert response.status_code == 500
    if accept_html:
        _assert_error_page(response, 500, "Algo deu errado")
    else:
        assert response.json() == {"error": "Erro interno do servidor."}
    # O Vary deste ramo é inline no admin_error_logging_middleware (o helper
    # vary_accept vive em frontend/ e este é o caminho onde o import dele pode
    # ter falhado) — a mesma URL devolve HTML ou JSON conforme o Accept.
    assert "accept" in _vary_values(response)
    assert len(logged) == 1
    assert logged[0][0][1] == "http_unhandled_exception"


# ─── 405 (Allow do exc.headers tem que sobreviver ao ramo HTML) ──────────────

def test_405_navegacao_recebe_html_com_allow():
    client = _client()
    response = client.post("/robots.txt", headers={**_csrf_headers(client), **HTML})
    _assert_error_page(response, 405, "não encontrada")
    assert "GET" in response.headers["allow"]


def test_405_api_continua_json():
    client = _client()
    response = client.post("/robots.txt", headers=_csrf_headers(client))
    assert response.status_code == 405
    assert response.json()["detail"] == "Method Not Allowed"


# ─── A. Nota interna não vaza no corpo servido ───────────────────────────────

@pytest.mark.parametrize(
    "vazamento",
    ["shared.py", "error_page_response", "app-mode.js", "PAGES", "CONSTANTES DO SERVIDOR"],
)
def test_pagina_de_erro_nao_vaza_nota_interna(vazamento):
    """Comentário de desenvolvimento (caminho de fonte, nome de helper) não pode
    ir junto no corpo — é disclosure de layout de fonte em toda página de erro.

    O param "CONSTANTES DO SERVIDOR" cobre o segundo defeito da nota antiga: ela
    citava os placeholders literalmente, o .replace() reescrevia a própria linha,
    e o cliente lia '404 ... são CONSTANTES DO SERVIDOR'."""
    assert vazamento not in _client().get("/rota-que-nao-existe", headers=HTML).text


# ─── B. Template ausente/ilegível não pode virar 500 ─────────────────────────

@pytest.mark.parametrize("accept_html", [True, False])
def test_template_ausente_ainda_responde_o_status_certo(monkeypatch, tmp_path, accept_html):
    """Sem guarda, o FileNotFoundError sobe até o middleware de log do admin e
    todo 404 de navegação vira 500 + `http_unhandled_exception` no banco."""
    monkeypatch.setattr(shared, "FRONTEND_DIR", tmp_path)  # tmp_path não tem error.html
    monkeypatch.setattr(shared, "_error_template", None)

    response = TestClient(dashboard.app, raise_server_exceptions=False).get(
        "/rota-que-nao-existe", headers=HTML if accept_html else {}
    )

    assert response.status_code == 404
    if accept_html:
        assert response.headers["content-type"].startswith("text/html")
        assert "404" in response.text and "não encontrada" in response.text
    else:
        assert response.json()["detail"] == "Not Found"


def test_template_ausente_nao_envenena_o_cache(monkeypatch, tmp_path):
    """O fallback não pode ficar cacheado: se o arquivo voltar, a próxima
    requisição tem que servir a página de verdade, não degradar até o restart."""
    monkeypatch.setattr(shared, "_error_template", None)
    monkeypatch.setattr(shared, "FRONTEND_DIR", tmp_path)
    assert shared.error_page_response(404).status_code == 404
    assert shared._error_template is None

    monkeypatch.undo()
    monkeypatch.setattr(shared, "_error_template", None)
    assert "safe-area.js" in shared.error_page_response(404).body.decode()


# ─── C. Allowlist de headers do exc.headers ──────────────────────────────────

@pytest.mark.parametrize(
    "header,value",
    [("Allow", "GET"), ("WWW-Authenticate", "Bearer"), ("Retry-After", "60")],
)
def test_headers_da_allowlist_passam(header, value):
    assert shared.error_page_response(400, headers={header: value}).headers[header] == value


def test_allowlist_e_case_insensitive():
    assert shared.error_page_response(405, headers={"allow": "GET, HEAD"}).headers["allow"] == "GET, HEAD"


@pytest.mark.parametrize(
    "header,value",
    [
        # Mata a conexão com uvicorn/h11: "Too much data for declared Content-Length".
        ("Content-Length", "3"),
        # Rotularia ~1.3 KB de HTML como JSON.
        ("Content-Type", "application/json"),
        ("Set-Cookie", "x=1"),
        ("X-Qualquer-Coisa", "1"),
        # Todo 3xx do repo é RedirectResponse (não passa por aqui); repassar isto
        # só fazia um 404 sair com Location.
        ("Location", "/x"),
    ],
)
def test_header_fora_da_allowlist_e_descartado(header, value):
    response = shared.error_page_response(400, headers={header: value})
    assert response.headers.get(header) != value
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    assert int(response.headers["content-length"]) == len(response.body)


def test_header_fora_da_allowlist_e_descartado_ponta_a_ponta():
    """O mesmo, mas pela pilha real: HTTPException com headers hostis."""
    path = f"/__hdrinj-{uuid.uuid4().hex}"

    @dashboard.app.get(path)
    async def _hdrinj():
        raise HTTPException(
            status_code=400,
            detail="x",
            headers={"Content-Length": "3", "Content-Type": "application/json", "Allow": "GET"},
        )

    try:
        response = _client().get(path, headers=HTML)
        assert response.status_code == 400
        assert response.headers["content-type"] == "text/html; charset=utf-8"
        assert response.headers["allow"] == "GET"  # da allowlist, sobrevive
        assert int(response.headers["content-length"]) == len(response.content)
        assert len(response.content) > 3
    finally:
        dashboard.app.router.routes = [
            r for r in dashboard.app.router.routes if getattr(r, "path", None) != path
        ]


# ─── D. Vary: Accept nos dois ramos ──────────────────────────────────────────

@pytest.mark.parametrize("accept_html", [True, False])
def test_404_declara_vary_accept_nos_dois_ramos(accept_html):
    """A mesma URL devolve HTML ou JSON conforme o Accept; sem o Vary um CDN
    pode servir um pelo outro."""
    response = _client().get("/rota-que-nao-existe", headers=HTML if accept_html else {})
    assert "accept" in _vary_values(response)


def test_vary_accept_soma_ao_existente_em_vez_de_sobrescrever():
    response = shared.error_page_response(404, headers={"Allow": "GET"})
    response.headers.add_vary_header("Origin")  # é o que o CORSMiddleware faz depois
    assert _vary_values(response) == ["accept", "origin"]


# ─── E. Accept é case-insensitive (RFC 7231 §3.1.1.1) ────────────────────────

@pytest.mark.parametrize("accept", ["TEXT/HTML", "Text/Html,application/xhtml+xml", "text/html"])
def test_accept_em_qualquer_caixa_cai_no_ramo_html(accept):
    response = _client().get("/rota-que-nao-existe", headers={"Accept": accept})
    assert response.headers["content-type"].startswith("text/html"), accept


def test_accept_sem_text_html_continua_json():
    response = _client().get("/rota-que-nao-existe", headers={"Accept": "application/json"})
    assert response.json()["detail"] == "Not Found"


# ─── F. 403 do CSRF (middleware, sem exception handler nenhum) ───────────────

def test_csrf_403_navegacao_recebe_html():
    """Chega por navegação de verdade: aba antiga com cookie expirado, SameSite
    perdendo o cookie na volta do OAuth."""
    response = _client().post("/auth/forgot-password", headers=HTML, json={"email": "x@example.com"})
    _assert_error_page(response, 403, "Acesso negado")
    assert "CSRF" not in response.text  # nada de vocabulário interno na tela


def test_csrf_403_api_continua_json():
    response = _client().post("/auth/forgot-password", json={"email": "x@example.com"})
    assert response.status_code == 403
    assert response.json() == {"detail": "Token CSRF inválido ou ausente."}
    assert response.headers["cache-control"] == "no-store"
    assert "accept" in _vary_values(response)


# ─── G. Exceção em middleware externo não cai no PlainTextResponse ───────────

def test_handler_de_exception_esta_ligado_ao_server_error_middleware():
    """`add_exception_handler(Exception, ...)` vira o handler do
    ServerErrorMiddleware (starlette/applications.py:85-95) — o mais externo de
    todos. Sem ele, exceção no CORS/security_headers/csrf sai como
    PlainTextResponse('Internal Server Error')."""
    stack = dashboard.app.build_middleware_stack()
    assert type(stack).__name__ == "ServerErrorMiddleware"
    assert stack.handler is dashboard.unhandled_exception_page_handler


@pytest.mark.parametrize("accept_html", [True, False])
def test_handler_de_exception_responde_html_ou_json(accept_html):
    scope = {
        "type": "http", "method": "GET", "path": "/x", "headers": (
            [(b"accept", b"text/html")] if accept_html else [(b"accept", b"*/*")]
        ),
    }
    response = asyncio.run(
        dashboard.unhandled_exception_page_handler(Request(scope), RuntimeError("boom"))
    )
    assert response.status_code == 500
    assert "accept" in _vary_values(response)
    # Este handler roda FORA de todo middleware de usuário: o
    # security_headers_middleware nunca vê esta resposta.
    _assert_security_headers(response)
    if accept_html:
        assert response.headers["content-type"] == "text/html; charset=utf-8"
        assert b"Algo deu errado" in response.body
    else:
        # Mesmo formato do middleware do admin — nada de um terceiro corpo de erro.
        assert json.loads(response.body) == {"error": "Erro interno do servidor."}


# ─── H. Template vazio/truncado é tão inútil quanto ausente ──────────────────

@pytest.mark.parametrize(
    "mutila",
    [
        pytest.param(lambda t: "", id="vazio"),
        pytest.param(lambda t: t[: len(t) // 2], id="truncado_no_meio"),
        # Perde o </html> mas mantém {{MESSAGE}} — só a checagem do fim pega.
        pytest.param(lambda t: t[:-12], id="truncado_no_fim"),
        # Corrupção NO MEIO: prefixo e sufixo intactos, {{CODE}}/{{TITLE}} vão
        # embora. Só a checagem dos três placeholders pega estes.
        pytest.param(
            lambda t: t.replace("{{CODE}}", "").replace("{{TITLE}}", ""), id="sem_code_e_title"
        ),
        pytest.param(lambda t: "\x00\x01\x02 {{MESSAGE}} \xff</html>", id="lixo_binario"),
        pytest.param(lambda t: "{{MESSAGE}}</html>", id="so_o_message"),
    ],
)
def test_template_incompleto_cai_no_fallback_e_nao_envenena_o_cache(monkeypatch, tmp_path, mutila):
    """Arquivo que ABRE mas veio pela metade (deploy interrompido, disco cheio)
    não levanta exceção: sem validação de conteúdo ele era aceito, cacheado em
    _error_template e servido até o restart, mesmo depois do arquivo voltar."""
    real = (shared.FRONTEND_DIR / "error.html").read_text(encoding="utf-8")
    (tmp_path / "error.html").write_text(mutila(real), encoding="utf-8")
    monkeypatch.setattr(shared, "FRONTEND_DIR", tmp_path)
    monkeypatch.setattr(shared, "_error_template", None)

    response = shared.error_page_response(404)
    body = response.body.decode()
    assert response.status_code == 404
    assert "404" in body and "não encontrada" in body
    assert body.rstrip().endswith("</html>")
    assert "safe-area.js" not in body        # é o fallback embutido, não o arquivo cru
    assert shared._error_template is None    # nada de cache do que não passou

    # Arquivo restaurado: a requisição seguinte serve a página de verdade.
    (tmp_path / "error.html").write_text(real, encoding="utf-8")
    assert "safe-area.js" in shared.error_page_response(404).body.decode()


# ─── I. 410 do link de exportação (clicado de dentro do e-mail) ──────────────

@pytest.mark.parametrize("accept_html", [True, False])
def test_410_do_link_de_exportacao(accept_html):
    """Navegação pura: o default 4xx tirava a única instrução acionável da tela."""
    response = _client().get(
        f"/auth/account/export/download/{uuid.uuid4().hex}",
        headers=HTML if accept_html else {},
    )
    assert response.status_code == 410
    if accept_html:
        _assert_error_page(response, 410, "Link expirado")
        assert "Configurações" in response.text          # diz o que fazer
        assert "Algo nesse pedido não está certo" not in response.text  # não é o default
    else:
        assert "Solicite uma nova exportação" in response.json()["detail"]


# ─── J. Status que não pode ter corpo não recebe página ──────────────────────

def test_status_sem_corpo_nao_recebe_pagina():
    """204/304 com corpo é resposta malformada. `shared.error_page_response(204)`
    devolve status 204 com ~1,4 KB de HTML — a guarda no handler é o que impede."""
    path = f"/__nobody-{uuid.uuid4().hex}"

    @dashboard.app.get(path)
    async def _nobody():
        raise HTTPException(status_code=204)

    try:
        response = _client().get(path, headers=HTML)
        assert response.status_code == 204
        assert response.content == b""
        assert "content-type" not in response.headers
    finally:
        dashboard.app.router.routes = [
            r for r in dashboard.app.router.routes if getattr(r, "path", None) != path
        ]


# ─── K. Security headers no HTML servido fora do middleware ──────────────────

def _assert_security_headers(response):
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_csrf_403_sai_com_security_headers():
    """csrf_middleware é registrado DEPOIS do security_headers_middleware, logo é
    o mais externo dos dois: o short-circuit nunca passa por lá. Uma página HTML
    nossa sem frame-ancestors é enquadrável."""
    _assert_security_headers(
        _client().post("/auth/forgot-password", headers=HTML, json={"email": "x@example.com"})
    )


# ─── L. Estado degradado loga uma vez por transição, não por requisição ──────

def _degraded_logs(caplog):
    return [r for r in caplog.records if "error.html indisponível" in r.getMessage()]


def test_degradado_loga_uma_vez_por_transicao(monkeypatch, tmp_path, caplog):
    """Este teste NÃO mede tempo: ele conta registros de log — 2 transições ao
    longo de 35 páginas de erro degradadas, em vez de 35.

    O motivo de contar é o custo por registro: o warning propaga pro root, que no
    processo web carrega o _DashboardHandler (core/observability.py:23) →
    psycopg.connect() + INSERT BLOQUEANTE no event loop, um connect por registro.
    A medição está em core/observability.py e em shared.py:186 (logging.warning()
    em série, com e sem o handler, contra Postgres em localhost: 11,3 ms na 1ª
    chamada, 2,1–3,7 ms/chamada em série, contra 0,02 ms sem o handler). O número
    que já esteve aqui — "539 ms em 10 páginas contra 5,7 ms em repouso" — não se
    sustenta e foi removido: a remedição deu 36,71 ms em n=10 e 52,41 ms em n=25.
    Sem o gate, um bot varrendo URL vira um INSERT síncrono por 404."""
    real = (shared.FRONTEND_DIR / "error.html").read_text(encoding="utf-8")
    (tmp_path / "error.html").write_text("{{MESSAGE}}</html>", encoding="utf-8")
    monkeypatch.setattr(shared, "FRONTEND_DIR", tmp_path)
    monkeypatch.setattr(shared, "_error_template", None)
    monkeypatch.setattr(shared, "_error_degraded", False)

    with caplog.at_level(logging.WARNING, logger=shared.__name__):
        for _ in range(25):
            assert shared.error_page_response(404).status_code == 404
        assert len(_degraded_logs(caplog)) == 1

        # Arquivo volta: serve a página de verdade e sai do estado degradado.
        (tmp_path / "error.html").write_text(real, encoding="utf-8")
        assert "safe-area.js" in shared.error_page_response(404).body.decode()
        assert len(_degraded_logs(caplog)) == 1

        # Quebra de novo (cache derrubado, como num restart): loga a nova queda.
        (tmp_path / "error.html").write_text("", encoding="utf-8")
        monkeypatch.setattr(shared, "_error_template", None)
        for _ in range(10):
            shared.error_page_response(404)
        assert len(_degraded_logs(caplog)) == 2


# ─── M. A página de erro entra no cache-buster, e sem o Meta Pixel junto ─────

def test_pagina_de_erro_passa_pelo_stamp_asset_versions(monkeypatch):
    """O `?v=` do safe-area.js sai com o hash do conteúdo, como TODA saída de HTML
    (stamp_asset_versions, #119). Antes ia o `?v=1` literal do error.html — a única
    URL de asset do produto que nunca invalidava, e já cacheada nos navegadores
    exatamente com esse valor, de quando `?v=1` era o padrão de todo mundo."""
    monkeypatch.setattr(shared, "_error_template", None)  # o stamp é feito no cache
    body = shared.error_page_response(404).body.decode()

    mtime_ns = (shared.FRONTEND_DIR / "safe-area.js").stat().st_mtime_ns
    esperado = shared._asset_hash("safe-area.js", mtime_ns)
    assert f"/safe-area.js?v={esperado}" in body
    assert "?v=1" not in body


def test_pagina_de_erro_nao_leva_o_meta_pixel(monkeypatch):
    """Decisão explícita do dono do produto: a página de erro fica FORA do rastreio
    de marketing. O atalho pra ganhar o stamp seria servi-la pelo `html_file`, que
    injeta o pixel quando META_PIXEL_ID está setado — este assert é o que impede
    isso de entrar calado junto com a próxima refatoração."""
    monkeypatch.setattr(shared, "META_PIXEL_ID", "000000000000000")
    monkeypatch.setattr(shared, "GA4_MEASUREMENT_ID", "G-000000000")
    monkeypatch.setattr(shared, "_error_template", None)
    assert shared.meta_pixel_snippet()  # o pixel ESTÁ ligado nesta requisição
    assert shared.ga4_snippet()         # e o GA4 também

    body = shared.error_page_response(404).body.decode()
    assert "fbq" not in body
    assert "connect.facebook.net" not in body
    assert "facebook.com/tr" not in body
    # O GA4 entrou pelo MESMO funil (inject_tracking), então herda a mesma
    # decisão: a página de erro fica fora do rastreio.
    assert "gtag" not in body
    assert "googletagmanager" not in body


# ─── N. 422 de validação — o handler que estava no diff sem um teste sequer ───
#
# `/unsubscribe?uid=&token=` não é caso de laboratório: é o link do rodapé de todo
# e-mail de engajamento (core/services/email_service.py:323), com `uid: int` e
# `token: str` OBRIGATÓRIOS na query. Cliente de e-mail que corta a URL entrega
# exatamente isto, e antes deste PR o usuário via na tela o array cru do FastAPI
# com `loc`/`msg`/`input` e o nome do parâmetro interno.

_URLS_422 = [
    pytest.param("/unsubscribe", id="parametros_ausentes"),
    pytest.param("/unsubscribe?uid=&token=", id="link_truncado"),   # uid='' → int_parsing
]


@pytest.mark.parametrize("url", _URLS_422)
@pytest.mark.parametrize("accept_html", [True, False])
def test_422_de_validacao_nos_dois_ramos(url, accept_html):
    """Apagar `add_exception_handler(RequestValidationError, ...)` mantinha a suíte
    verde: o handler inteiro não tinha nada que o prendesse."""
    response = _client().get(url, headers=HTML if accept_html else {})
    assert response.status_code == 422
    if accept_html:
        _assert_error_page(response, 422, "Requisição inválida")
    else:
        # Contrato de API preservado: o array do FastAPI, intacto.
        assert isinstance(response.json()["detail"], list)
        assert response.json()["detail"][0]["loc"][0] == "query"
        assert "accept" in _vary_values(response)


@pytest.mark.parametrize("url", _URLS_422)
def test_422_nao_vaza_o_detail_na_pagina(url):
    """O `detail` do 422 é uma LISTA com nome de campo interno (`uid`), o tipo do
    erro (`int_parsing`) e o valor recebido (`input`) — nada disso pode chegar à
    tela. As strings proibidas são DERIVADAS da resposta JSON da mesma URL, não
    listadas à mão: se o FastAPI mudar o formato, o teste acompanha."""
    json_body = _client().get(url).json()
    html_text = _client().get(url, headers=HTML).text
    visivel = _texto_visivel(html_text)

    proibidas = set()
    def _coleta(node):
        if isinstance(node, str):
            proibidas.add(node)
        elif isinstance(node, dict):
            for k, v in node.items():
                proibidas.add(k)
                _coleta(v)
        elif isinstance(node, list):
            for v in node:
                _coleta(v)
    _coleta(json_body)

    assert "uid" in proibidas and "detail" in proibidas   # o teste está olhando algo
    for termo in proibidas:
        if termo:
            assert termo not in visivel, termo
    # E o texto longo do pydantic não pode estar nem escondido no HTML cru.
    assert json_body["detail"][0]["msg"] not in html_text


# ─── O. 401 ponta a ponta (até aqui só havia teste unitário do header) ────────

@pytest.mark.parametrize("accept_html", [True, False])
def test_401_de_rota_protegida_nos_dois_ramos(accept_html):
    """`GET /auth/me` sem token. O ramo HTML existe porque isto chega por
    navegação: link de e-mail para área logada, deep link do app, aba velha."""
    response = _client().get("/auth/me", headers=HTML if accept_html else {})
    assert response.status_code == 401
    if accept_html:
        _assert_error_page(response, 401, "Acesso negado")
        assert "Token não fornecido" not in response.text  # vocabulário interno
    else:
        assert response.json()["detail"] == "Token não fornecido."
        assert "accept" in _vary_values(response)


def test_401_www_authenticate_sobrevive_ao_ramo_html():
    """A allowlist já tem teste unitário (`test_headers_da_allowlist_passam`), mas
    ninguém provava que o header sai vivo do outro lado da pilha — do `exc.headers`
    do `raise` até a resposta. Rota sintética porque HOJE nenhum 401 do produto
    manda WWW-Authenticate (`git grep -i www-authenticate` só acha o comentário do
    handler, a allowlist e o teste): quem adicionar um vai depender deste caminho."""
    path = f"/__401-{uuid.uuid4().hex}"

    @dashboard.app.get(path)
    async def _naoautorizado():
        raise HTTPException(
            status_code=401,
            detail="Token inválido ou expirado.",
            headers={"WWW-Authenticate": 'Bearer realm="pigbank", error="invalid_token"'},
        )

    try:
        response = _client().get(path, headers=HTML)
        _assert_error_page(response, 401, "Acesso negado")
        assert response.headers["www-authenticate"] == 'Bearer realm="pigbank", error="invalid_token"'
        assert "invalid_token" not in response.text  # no header sim, no corpo não
    finally:
        dashboard.app.router.routes = [
            r for r in dashboard.app.router.routes if getattr(r, "path", None) != path
        ]


# ─── P. 503: o ramo is_timeout do middleware do admin ────────────────────────

@pytest.fixture
def timeout_route():
    """Exceção cuja mensagem casa com os `("timeout", "connection", "could not
    connect")` de admin_dashboard.py — é como uma queda de Postgres chega aqui."""
    path = f"/__timeout-{uuid.uuid4().hex}"

    @dashboard.app.get(path)
    async def _timeout():
        raise RuntimeError("connection timeout expired")

    yield path
    dashboard.app.router.routes = [
        r for r in dashboard.app.router.routes if getattr(r, "path", None) != path
    ]


@pytest.mark.parametrize("accept_html", [True, False])
def test_503_de_timeout_nos_dois_ramos(monkeypatch, timeout_route, accept_html):
    """O teste do 500 só exercitava o outro ramo do mesmo `if`: trocar o 503 por
    500 (ou apagar a detecção) passava despercebido. Aqui o status, o texto de
    indisponibilidade e o `status_code` que vai pro log ficam presos."""
    logged = []

    async def _spy(*args, **kwargs):
        logged.append((args, kwargs))

    monkeypatch.setattr(admin_dashboard, "log_system_event", _spy)

    response = TestClient(dashboard.app, raise_server_exceptions=False).get(
        timeout_route, headers=HTML if accept_html else {}
    )

    assert response.status_code == 503
    if accept_html:
        _assert_error_page(response, 503, "Serviço indisponível")
        # Não pode cair no texto genérico de 5xx: quem vê isto tem que saber que
        # é instabilidade passageira, não erro permanente.
        assert "Algo deu errado do nosso lado" not in response.text
    else:
        assert response.json() == {
            "error": "Serviço temporariamente indisponível. Tente novamente em instantes."
        }
    assert "accept" in _vary_values(response)
    assert logged[0][1]["details"]["status_code"] == 503


# ─── Q. /unsubscribe com token inválido — o irmão do 410 e o kwarg `text=` ────

# Cópia literal de frontend/finance_bot_websocket_custom.py:4908-4912 (as aspas
# curvas são as do código). Mudou lá, muda aqui — é o ponto do teste.
_UNSUB_TEXTO = (
    "Link inválido",
    "Este link de descadastro não vale mais. Você pode cancelar os e-mails "
    "em Configurações → Notificações, ou mandar “parar emails” pro bot.",
)


@pytest.mark.parametrize("accept_html", [True, False])
def test_unsubscribe_token_invalido_recebe_pagina_com_texto_proprio(accept_html):
    """Era o último `<h2>Link inválido ou expirado.</h2>` pelado do produto, e
    chega por navegação pura (link do rodapé do e-mail, sem retry possível).

    Os DOIS Accepts respondem HTML aqui, de propósito e diferente do resto do
    mapa: o GET só existe para o clique humano — o caminho de máquina do RFC 8058
    é o POST, coberto pelo teste seguinte."""
    response = _client().get(
        f"/unsubscribe?uid=999999999&token=lixo-{uuid.uuid4().hex}",
        headers=HTML if accept_html else {},
    )
    _assert_error_page(response, 400, "Link inválido", textos=_UNSUB_TEXTO)
    # O default do 400 é o texto dos 422 de validação e não diz o que fazer.
    assert "Não consegui entender esse pedido" not in response.text
    assert "Configurações" in response.text          # a instrução acionável
    assert "<h2>Link inválido ou expirado.</h2>" not in response.text  # o corpo antigo


def test_unsubscribe_one_click_do_gmail_continua_texto_plano():
    """RFC 8058: o POST é server-to-server (Gmail/Yahoo), sem interação. Trocar
    este corpo por HTML manda 1,4 KB de página para um robô que só lê o status —
    e o `text=` do GET fica a uma linha de distância deste return."""
    response = _client().post(f"/unsubscribe?uid=999999999&token=lixo-{uuid.uuid4().hex}")
    assert response.status_code == 400
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text == "invalid"


def test_unsubscribe_one_click_ignora_o_accept_do_cliente():
    """Mesmo com Accept: text/html — servidor de e-mail que manda `Accept: */*` ou
    qualquer coisa não pode arrastar o one-click pro ramo HTML."""
    response = _client().post(
        f"/unsubscribe?uid=999999999&token=lixo-{uuid.uuid4().hex}", headers=HTML
    )
    assert response.status_code == 400
    assert response.headers["content-type"].startswith("text/plain")


def test_kwarg_text_sobrepoe_o_mapa_sem_afetar_o_resto():
    """O override é por chamada: o `text=` de uma requisição não pode vazar para a
    página de erro da próxima (o template é cacheado em global de módulo — se o
    texto fosse cacheado junto, o 400 seguinte sairia com 'Link inválido')."""
    proprio = shared.error_page_response(400, text=("Título próprio", "Mensagem própria"))
    seguinte = shared.error_page_response(400)

    assert "Título próprio" in proprio.body.decode()
    assert "Título próprio" not in seguinte.body.decode()
    assert "Não consegui entender esse pedido" in seguinte.body.decode()
    # E o override não mexe no status nem no mapa.
    assert proprio.status_code == 400
    assert shared._ERROR_TEXTS[400] == ("Requisição inválida", "Não consegui entender esse pedido.")


def test_todo_text_de_chamada_e_constante_literal():
    """O `text=` já sai escapado e sem reexpansão desde
    `test_text_sai_escapado_e_sem_reexpansao_de_placeholder` (antes disso, medido:
    `error_page_response(400, text=("{{MESSAGE}}", "<script>alert(1)</script>"))`
    renderizava o `<script>` intacto e o título com `{{MESSAGE}}` dentro era
    reescrito pelo `.replace()` seguinte). Este teste continua valendo uma camada
    acima: escape protege o RENDER, não impede que dado de usuário vire texto de
    página de erro (eco de query param já é disclosure mesmo escapado). A regra
    escrita do docstring vira verificação — todo call site passa tupla de literais
    de string —
    f-string, variável, `str(exc)` ou `request.query_params[...]` reprovam aqui,
    que é onde o dado do usuário entraria."""
    import ast

    raiz = pathlib.Path(shared.__file__).resolve().parents[2]
    ignorar = {".venv", ".claude", "node_modules", ".git", "mobile"}
    call_sites = 0

    for arquivo in raiz.rglob("*.py"):
        # relative_to(raiz): o próprio caminho da raiz contém ".claude" quando o
        # repo está num worktree — comparar os parts absolutos ignoraria TUDO.
        if ignorar & set(arquivo.relative_to(raiz).parts) or arquivo.name.startswith("test_"):
            continue
        arvore = ast.parse(arquivo.read_text(encoding="utf-8"), filename=str(arquivo))
        for node in ast.walk(arvore):
            if not (isinstance(node, ast.Call)
                    and getattr(node.func, "id", getattr(node.func, "attr", None))
                    == "error_page_response"):
                continue
            for kw in node.keywords:
                if kw.arg != "text":
                    continue
                call_sites += 1
                onde = f"{arquivo}:{node.lineno}"
                assert isinstance(kw.value, ast.Tuple), f"{onde}: text= não é tupla literal"
                for elt in kw.value.elts:
                    # ast.Constant(str) cobre a concatenação implícita de literais
                    # ("a" "b"); JoinedStr (f-string), Name e Call reprovam.
                    assert isinstance(elt, ast.Constant) and isinstance(elt.value, str), \
                        f"{onde}: text= carrega {ast.dump(elt)[:60]} — não é constante"

    assert call_sites == 1, f"call sites com text=: {call_sites} (era 1: o /unsubscribe)"


# ─── Escape e desempacotamento do `text=` (fechados por construção) ──────────

def test_text_sai_escapado_e_sem_reexpansao_de_placeholder():
    """O que o AST guard acima cobre por regra, isto cobre por construção: mesmo
    entrando HTML no `text=`, ele sai escapado; e um valor que CONTÉM um
    placeholder não é reexpandido pela troca seguinte (era o efeito da ordem
    CODE→TITLE→MESSAGE em `.replace()` encadeado — hoje é uma passagem só)."""
    corpo = shared.error_page_response(
        400, text=("{{MESSAGE}}", "<script>alert(1)</script>")
    ).body.decode()

    assert "<script>alert(1)</script>" not in corpo
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in corpo
    # O título `{{MESSAGE}}` fica literal nos dois pontos onde {{TITLE}} aparece
    # (<title> e <h2>); com a reexpansão ele virava a mensagem nos dois.
    assert corpo.count("{{MESSAGE}}") == 2


def test_text_malformado_nao_levanta_e_cai_no_texto_do_mapa():
    """`error_page_response` promete não ter caminho de falha — é a resposta de
    último recurso, e uma exceção aqui vira 500 + `http_unhandled_exception` no
    log a cada 404 de bot varrendo URL. Medido antes da guarda:
    `text=("so-um-item",)` → `ValueError: not enough values to unpack`."""
    for ruim in [("so-um-item",), ("a", "b", "c"), (), "ab", 5, None, ("x", None)]:
        response = shared.error_page_response(400, text=ruim)
        assert response.status_code == 400, ruim
        assert "Não consegui entender esse pedido" in response.body.decode(), ruim
