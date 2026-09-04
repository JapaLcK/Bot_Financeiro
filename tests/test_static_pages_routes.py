"""Rotas de frontend/routes/static_pages.py (refactor Fase 1, Etapa 1).

Rede de segurança da extração: cada rota movida do monólito continua
registrada no app e respondendo com o mesmo status/content-type/headers.
Nenhuma toca banco — TestClient sem lifespan basta.
"""

import re

from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard
from frontend.routes.shared import FRONTEND_DIR, _asset_hash, stamp_asset_versions

client = TestClient(dashboard.app)

HTML_PAGES = [
    "/",
    "/app",
    "/home",
    "/settings",
    "/reset-password",
    "/onboarding",
    "/completar-cadastro",
    "/privacy",
    "/termos",
    "/whatsapp",
    "/funcionalidades",
    "/comandos",
    "/comandos-app",
    "/como-funciona",
    "/precos",
    "/suporte",
]


def test_html_pages_respondem_com_no_store():
    for path in HTML_PAGES:
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert resp.headers["content-type"].startswith("text/html"), path
        assert resp.headers["cache-control"] == "no-store", path


def test_whatsapp_nao_garante_quando_a_primeira_cobranca_vem():
    """A trilha de passos da /whatsapp é NOSSA (1ca0c44) e não pode prometer a data.

    Mesmo defeito que o `tests/test_welcome_email_copy.py` já proíbe no e-mail:
    quem recria a conta com um telefone que já está em `plan_trials` é
    inelegível (`db.plans.is_trial_eligible_for_user`), o checkout sai com
    `trial_days=0` e o Stripe cobra na hora. Achado do Codex no PR #239 — o
    e-mail foi corrigido e esta página ficou.

    Controle positivo junto: a oferta dos 15 dias continua na página (senão o
    caso passaria numa /whatsapp que apagou o trial, que é pior que a garantia)
    e a ressalva "um por número" da /precos aparece.

    Só a /whatsapp: na /precos as mesmas frases existem em copy PREEXISTENTE
    (linhas 220, 319 e 773, anteriores ao PR), então a asserção de página
    inteira ficaria vermelha por código que não é deste PR. A copy do gate de
    lá, que é nossa, é medida em tests/frontend/precos_sem_plano_gratis.test.mjs.
    """
    html = " ".join(client.get("/whatsapp").text.split())

    for garantia in (
        "Nada é cobrado agora",
        "primeira cobrança só vem depois",
        "cancelar antes sem pagar nada",
    ):
        assert garantia not in html, (
            f"a /whatsapp garante a data da cobrança, que a página não conhece: {garantia!r}"
        )

    assert "15 dias grátis" in html, "a /whatsapp deixou de oferecer o teste"
    assert "um por número de telefone" in html, (
        "a /whatsapp perdeu a ressalva de que o teste é um por número"
    )
    assert "checkout" in html.lower(), (
        "a /whatsapp não defere ao checkout quem confirma a cobrança"
    )


def test_index_nao_inverte_o_fluxo_do_trial():
    """O CTA do "como funciona" da / é NOSSO (1ca0c44) e dizia o fluxo ao contrário.

    O texto era "você testa 15 dias grátis antes de escolher um plano", mas o
    gate deste PR faz o oposto: escolher o plano é o que ATIVA o trial
    (`needs_plan_selection` bloqueia o app até a assinatura). O mesmo commit
    escreveu a ordem certa na /whatsapp e na /como-funciona e a inversa aqui.

    A asserção é presa ao bloco `.hiw-cta` de propósito: as garantias de data
    ("primeira cobrança", "sem pagar nada") também existem nas linhas 504 e
    536 desta página, mas são PREEXISTENTES (d5e299f, anterior à merge-base
    c779837) e não são deste PR. Guarda de página inteira, como o da
    /whatsapp, é impossível aqui — ficaria vermelho por código de terceiro.

    Controle positivo dentro do próprio bloco: ele continua oferecendo o teste
    e deferindo ao checkout, senão o caso passaria num CTA que apagou a oferta.
    """
    html = " ".join(client.get("/").text.split())
    bloco = re.search(r'class="hiw-cta".*?</div>', html)
    assert bloco, "o bloco .hiw-cta sumiu da / — a asserção abaixo ficou cega"
    cta = bloco.group(0)

    assert not re.search(
        r"test\w*[^.]{0,60}antes de (?:escolher|assinar|pegar|pagar)[^.]{0,25}plano",
        cta,
        re.I,
    ), f"o CTA da / diz que se testa antes de escolher o plano: {cta!r}"

    for garantia in ("primeira cobrança", "sem pagar nada", "não paga nada"):
        assert garantia not in cta, (
            f"o CTA da / garante a data/ausência da cobrança: {garantia!r}"
        )

    assert "15 dias grátis" in cta, "o CTA da / deixou de oferecer o teste"
    assert "checkout" in cta.lower(), (
        "o CTA da / não defere ao checkout quem confirma a cobrança"
    )


def test_stamp_asset_versions_usa_hash_de_conteudo():
    # Número hardcoded no HTML é ignorado e trocado pelo hash do arquivo real.
    mtime = (FRONTEND_DIR / "app-mode.css").stat().st_mtime_ns
    esperado = _asset_hash("app-mode.css", mtime)
    out = stamp_asset_versions('<link href="/app-mode.css?v=29">')
    assert f"/app-mode.css?v={esperado}" in out
    assert "?v=29" not in out
    # Assets distintos → hashes distintos (não é um número global compartilhado).
    js = re.search(r"/app-mode\.js\?v=(\w+)", stamp_asset_versions('"/app-mode.js?v=1"'))
    assert js and js.group(1) != esperado
    # Asset que não existe em frontend/ fica intacto.
    assert stamp_asset_versions('"/naoexiste.js?v=7"') == '"/naoexiste.js?v=7"'


def test_home_serve_app_mode_com_cache_buster_de_hash():
    # A página logada real: o ?v= servido tem que ser hash, nunca o v=29 do arquivo.
    html = client.get("/home").text
    m = re.search(r"/app-mode\.css\?v=([0-9a-f]+)", html)
    assert m, "app-mode.css deveria aparecer com ?v=<hash>"
    assert m.group(1) != "29"
    assert m.group(1) == _asset_hash(
        "app-mode.css", (FRONTEND_DIR / "app-mode.css").stat().st_mtime_ns
    )


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    # Resposta pública inalterada: o test_health_endpoint_does_not_expose_
    # infrastructure (tests/test_auth_cookie.py) afirma o mesmo por outro
    # ângulo, e foi ele que pegou a versão que vazava o commit para qualquer um.
    assert resp.json() == {"status": "ok"}
    # Sem no-store, uma borda que cacheie /health devolve o SHA do deploy
    # anterior e o gate do smoke abre em cima do código velho.
    assert resp.headers["cache-control"] == "no-store"


def test_health_entrega_o_commit_so_com_o_token(monkeypatch):
    """O gate do smoke (scripts/smoke_prod.py) precisa do SHA; mais ninguém.

    Os três casos importam: com token certo o campo aparece (senão o gate nunca
    abre e o smoke morre no timeout), com token errado e sem token ele não
    aparece (senão está vazando infraestrutura em endpoint público).
    """
    monkeypatch.setenv("SMOKE_HEALTH_TOKEN", "token-de-teste-32-bytes-ou-mais!!")
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "abc123def456")

    com_token = client.get("/health", headers={"X-Smoke-Token": "token-de-teste-32-bytes-ou-mais!!"})
    assert com_token.json() == {"status": "ok", "commit": "abc123def456"}

    errado = client.get("/health", headers={"X-Smoke-Token": "token-errado"})
    assert errado.json() == {"status": "ok"}

    assert client.get("/health").json() == {"status": "ok"}


def test_health_sem_token_configurado_nao_aceita_header_vazio(monkeypatch):
    """Sem SMOKE_HEALTH_TOKEN no ambiente, NADA abre o campo — nem o header vazio.

    `compare_digest("", "")` é True: se a guarda fosse só a comparação, um
    ambiente sem a variável entregaria o commit a quem mandasse o header em
    branco, que é todo mundo.
    """
    monkeypatch.delenv("SMOKE_HEALTH_TOKEN", raising=False)
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "abc123def456")

    assert client.get("/health", headers={"X-Smoke-Token": ""}).json() == {"status": "ok"}
    assert client.get("/health").json() == {"status": "ok"}


def test_robots_txt():
    resp = client.get("/robots.txt")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "Disallow: /app" in resp.text
    assert "Sitemap:" in resp.text


def test_sitemap_xml():
    resp = client.get("/sitemap.xml")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/xml")
    assert "<urlset" in resp.text
    assert "/precos" in resp.text


def test_commands_catalog():
    resp = client.get("/api/commands-catalog")
    assert resp.status_code == 200
    assert "catalog" in resp.json()


def test_auth_refresh_js_revalida_a_cada_load():
    """`no-cache` no header E `?v=<hash>` na URL — os dois, e por motivos
    diferentes.

    O header protege dali para a frente. Ele NÃO invalida o que já está no
    cache do cliente: quem buscou o arquivo pouco antes do deploy carrega a
    entrada com o `max-age=300` antigo e roda a versão anterior por até 5
    minutos. Aqui isso tem consequência de segurança — este arquivo carrega a
    limpeza de estado no fim de sessão (Codex, #170).

    Quem invalida na hora é a URL versionada: `?v=<hash>` é outra chave de
    cache, então a entrada velha deixa de ser consultada.
    """
    resp = client.get("/static/auth-refresh.js")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/javascript")
    assert resp.headers["cache-control"] == "no-cache"


def test_auth_refresh_js_sai_versionado_nas_paginas_autenticadas():
    """As páginas que o carregam pedem `?v=<hash>`, não a URL nua.

    Três delas referenciavam sem `?v=` nenhum, e o `?v=1` da quarta era um
    cache-buster MORTO: o `_ASSET_VER_RE` não aceitava `/` no meio do caminho,
    então `/static/auth-refresh.js` nem casava a forma. O arquivo mudou para
    `frontend/static/` para o caminho da URL bater com o do disco e o lookup do
    `stamp_asset_versions` resolver sem caso especial.
    """
    hash_esperado = _asset_hash(
        "static/auth-refresh.js",
        (FRONTEND_DIR / "static" / "auth-refresh.js").stat().st_mtime_ns,
    )
    for page in ["/app", "/home", "/settings", "/onboarding", "/precos"]:
        html = client.get(page).text
        assert "/static/auth-refresh.js" in html, page
        m = re.search(r"/static/auth-refresh\.js\?v=([0-9a-f]+)", html)
        assert m, f"{page} pede a URL nua — a entrada velha do cache continua valendo"
        assert m.group(1) == hash_esperado, page


def test_pb_nav_js_com_cache_publico():
    resp = client.get("/pb-nav.js")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/javascript")
    assert resp.headers["cache-control"] == "public, max-age=300"
    # o motor exige o contrato: sem PBNav.boot as páginas convertidas não iniciam
    assert "PBNav" in resp.text and "boot" in resp.text


def test_service_worker_headers():
    resp = client.get("/service-worker.js")
    assert resp.status_code == 200
    assert resp.headers["service-worker-allowed"] == "/"
    assert resp.headers["cache-control"] == "no-cache"


def test_assets_estaticos():
    for path, content_type in [
        ("/modals.js", "application/javascript"),
        ("/favicon.png", "image/png"),
        ("/manifest.json", "application/manifest+json"),
        ("/dashboard.js", "application/javascript"),
        ("/dashboard-chat.js", "application/javascript"),
    ]:
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert resp.headers["content-type"].startswith(content_type), path


def test_rotas_que_encerram_sessao_estao_no_auth_refresh():
    """§0.7: o JS não importa Python, então um teste compara as duas listas.

    A limpeza do Cache Storage no fim de sessão (`auth-refresh.js`) precisa
    conhecer TODA rota cujo backend chama `_clear_session_cookies` — não só o
    logout. Foi assim que a exclusão de conta ficou de fora: eu consertei a
    instância e não a classe (Codex, #170).

    A varredura é por `ast`, não por texto: procura as funções de rota que
    contêm uma chamada a `_clear_session_cookies` e devolve o caminho do
    decorador. Uma quarta rota que limpe cookie e não esteja no JS deixa isto
    vermelho.
    """
    import ast
    import pathlib
    import re as _re

    fonte = (pathlib.Path(__file__).resolve().parents[1]
             / "frontend" / "finance_bot_websocket_custom.py").read_text(encoding="utf-8")
    arvore = ast.parse(fonte)

    do_python: set[str] = set()
    for no in ast.walk(arvore):
        if not isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        limpa = any(
            isinstance(c, ast.Call)
            and getattr(c.func, "id", None) == "_clear_session_cookies"
            for c in ast.walk(no)
        )
        if not limpa:
            continue
        for dec in no.decorator_list:
            # Só o decorador de ROTA (`@app.post("/x")`). Sem este recorte
            # entram os `@limiter.limit("30/minute")`, que também são Call com
            # string no primeiro argumento — medido.
            if not (isinstance(dec, ast.Call)
                    and isinstance(dec.func, ast.Attribute)
                    and getattr(dec.func.value, "id", None) == "app"
                    and dec.func.attr in {"get", "post", "put", "patch", "delete"}):
                continue
            if (dec.args and isinstance(dec.args[0], ast.Constant)
                    and isinstance(dec.args[0].value, str)):
                do_python.add(dec.args[0].value)

    assert do_python, "a varredura não achou rota nenhuma — o walk quebrou"

    js = (pathlib.Path(__file__).resolve().parents[1]
          / "frontend" / "static" / "auth-refresh.js").read_text(encoding="utf-8")
    bloco = js[js.index("_SESSAO_ENCERRADA = {"):]
    bloco = bloco[:bloco.index("};")]
    do_js = set(_re.findall(r'"(/[^"]+)":', bloco))

    assert do_python == do_js, (
        "rota que encerra sessão fora da limpeza de cache do auth-refresh.js — "
        "o cache privado sobrevive nesse fluxo. "
        f"só no Python: {sorted(do_python - do_js)}; só no JS: {sorted(do_js - do_python)}"
    )


# Classificação EXPLÍCITA de cada `raise HTTPException(status_code=401, ...)` de
# `frontend/finance_bot_websocket_custom.py` + `frontend/routes/*.py`, por
# `(arquivo, detail)`. `True` = falha de access/dashboard token, leva o
# `WWW_AUTHENTICATE_401` e o interceptor renova; `False` = 401 de aplicação, não
# leva e não renova.
#
# `core/admin_dashboard.py` fica FORA por caminho: é outra árvore de auth (sessão
# de admin, cookie próprio) e nenhuma página dela carrega o `auth-refresh.js`.
_401_RENOVAVEL = {
    # ── família A: o access/dashboard token falhou → renovar resolve ──────────
    ("frontend/finance_bot_websocket_custom.py", "Token não fornecido."): True,
    ("frontend/finance_bot_websocket_custom.py", "Token inválido ou expirado."): True,
    ("frontend/finance_bot_websocket_custom.py", "Sessão encerrada. Faça login novamente."): True,
    ("frontend/finance_bot_websocket_custom.py", "Sessão expirada. Faça login novamente."): True,
    ("frontend/finance_bot_websocket_custom.py", "Token inválido"): True,
    ("frontend/routes/shared.py", "Token de dashboard inválido ou expirado."): True,
    ("frontend/routes/shared.py", "Sessão encerrada. Faça login novamente."): True,
    # ── família B: 401 de aplicação, o token está ótimo ───────────────────────
    # Senha incorreta em MFA setup/disable, regenerar backup codes e export.
    ("frontend/finance_bot_websocket_custom.py", "Senha incorreta."): False,
    # Login: nem chega ao interceptor — login.html não carrega o auth-refresh.js.
    ("frontend/finance_bot_websocket_custom.py", "E-mail ou senha incorretos."): False,
    # Magic link consumido/expirado. É a pegadinha da lista: parece autenticação
    # e NÃO é renovável — access token novo não ressuscita um link expirado.
    ("frontend/finance_bot_websocket_custom.py", "Link de dashboard inválido ou expirado."): False,
    # `detail=str(exc)` de um PermissionError: senha errada no DELETE /auth/account
    # (monólito) e no reset de dados (settings.py). Dinâmico, daí o marcador.
    ("frontend/finance_bot_websocket_custom.py", "<str(exc)>"): False,
    ("frontend/routes/settings.py", "<str(exc)>"): False,
    # Chave de API do lead engine e webhook server-to-server: sem sessão nenhuma.
    ("frontend/routes/prospects.py", "Chave inválida."): False,
    ("frontend/routes/open_finance.py", "Não autorizado."): False,
    # ── família C: 401 que o INTERCEPTOR nem alcança ──────────────────────────
    # Os dois do `POST /auth/refresh` (montados como `JSONResponse` porque o
    # `raise` descarta o Set-Cookie da limpeza, #175). O interceptor sai antes
    # deles, no `_isRefreshEndpoint`: renovar o refresh com o refresh é recursão.
    ("frontend/finance_bot_websocket_custom.py", "missing_refresh_token"): False,
    ("frontend/finance_bot_websocket_custom.py", "invalid_refresh_token"): False,
    # Magic link expirado no `GET /d/{code}`: página HTML de NAVEGAÇÃO, não
    # resposta de `fetch`. O `window.fetch` do auth-refresh.js não vê navegação.
    ("frontend/finance_bot_websocket_custom.py", "<HTMLResponse>"): False,
}


def _e_401(no):
    """`401`, `status.HTTP_401_UNAUTHORIZED` ou `HTTP_401_UNAUTHORIZED` importado.

    A forma com a constante do FastAPI é a IDIOMÁTICA — a que um contribuidor
    novo escreve —, e o gate era cego para ela.
    """
    import ast

    if isinstance(no, ast.Constant):
        return no.value == 401
    return getattr(no, "attr", None) == "HTTP_401_UNAUTHORIZED" or \
        getattr(no, "id", None) == "HTTP_401_UNAUTHORIZED"


def _forma_do_header(no):
    """`"constante"`, `"literal"` ou `None` — as três formas do `headers=`.

    O literal é separado do ausente de propósito: o código está CORRETO no
    comportamento e errado na §0.7 (duplica o valor da constante), e a mensagem
    que o autor recebe tem que ser essa, não "sem header nunca renova".
    """
    import ast

    if getattr(no, "id", None) == "WWW_AUTHENTICATE_401":
        return "constante"
    if isinstance(no, ast.Dict) and any(
        isinstance(k, ast.Constant) and str(k.value).lower() == "www-authenticate"
        for k in no.keys
    ):
        return "literal"
    return None


def _varre_401(caminho, fonte):
    """Devolve (chave, forma_do_header) de cada resposta 401 do arquivo.

    Quatro formas, todas medidas (o gate anterior só via a primeira):

        raise HTTPException(status_code=401, ...)
        raise HTTPException(401, ...)                       # posicional
        return JSONResponse(status_code=401, ...)           # e HTMLResponse etc
        status_code=status.HTTP_401_UNAUTHORIZED            # constante do FastAPI

    `chave` é `(arquivo, detail)`. O `detail` sai do kwarg (HTTPException) ou do
    `content={"detail": ...}` (JSONResponse); quando não é string constante vira
    `<str(exc)>` no HTTPException e `<NomeDaClasse>` na resposta montada à mão.
    """
    import ast

    achados = []
    for chamada in ast.walk(ast.parse(fonte)):
        if not isinstance(chamada, ast.Call):
            continue
        nome = getattr(chamada.func, "id", None) or getattr(chamada.func, "attr", None) or ""
        if nome != "HTTPException" and not nome.endswith("Response"):
            continue
        kw = {k.arg: k.value for k in chamada.keywords}
        # posicional: `HTTPException(401, ...)` e `JSONResponse(content, 401)`
        pos = 0 if nome == "HTTPException" else 1
        status = kw.get("status_code")
        if status is None and len(chamada.args) > pos:
            status = chamada.args[pos]
        if not _e_401(status):
            continue
        detail = kw.get("detail")
        if detail is None and isinstance(kw.get("content"), ast.Dict):
            detail = next(
                (v for k, v in zip(kw["content"].keys, kw["content"].values)
                 if isinstance(k, ast.Constant) and k.value == "detail"),
                None,
            )
        chave = (
            detail.value if isinstance(detail, ast.Constant) and isinstance(detail.value, str)
            else "<str(exc)>" if nome == "HTTPException" else f"<{nome}>"
        )
        achados.append(((caminho, chave), _forma_do_header(kw.get("headers"))))
    return achados


def test_401_de_autenticacao_declara_familia():
    """§0.7: o `auth-refresh.js` não importa Python, então um teste prende as listas.

    Desde a #176 o interceptor só renova o 401 que traz `WWW-Authenticate` — ele
    falha FECHADO, e é por isso que este gate é obrigatório e não opcional: um 401
    de autenticação que alguém esqueça de marcar deixa de ser renovado e joga o
    usuário para o login, regressão PIOR que o bug original (uma request extra).

    Direções, todas vermelhas:
      - 401 novo sem entrada em `_401_RENOVAVEL` → o autor é obrigado a decidir a
        família, em vez de herdar um default errado em silêncio;
      - família declarada que não bate com o `headers=` do código, nas duas
        direções (renovável sem header × de aplicação com header);
      - `WWW-Authenticate` escrito como dict literal em vez da constante (§0.7).

    A varredura cobre as QUATRO formas de responder 401 aqui — `status_code=401`,
    `HTTPException(401, ...)` posicional, `JSONResponse/HTMLResponse(status_code=401)`
    e `status.HTTP_401_UNAUTHORIZED`. Cobria só a primeira, e as outras três já
    existiam no monólito (os dois do `/auth/refresh` e o do `/d/{code}`): a
    promessa do docstring era falsa em 3 das 4.

    Fora do alcance por caminho: `core/admin_dashboard.py`, que é outra árvore de
    auth (sessão de admin) e cujas páginas não carregam o `auth-refresh.js`.
    """
    import pathlib

    raiz = pathlib.Path(__file__).resolve().parents[1]
    alvos = [pathlib.Path("frontend/finance_bot_websocket_custom.py")]
    alvos += sorted(
        p.relative_to(raiz) for p in (raiz / "frontend" / "routes").glob("*.py")
    )

    achados = []
    for rel in alvos:
        achados += _varre_401(rel.as_posix(), (raiz / rel).read_text(encoding="utf-8"))

    assert achados, "a varredura não achou 401 nenhum — o walk quebrou"

    sem_classificacao = sorted({c for c, _ in achados if c not in _401_RENOVAVEL})
    assert not sem_classificacao, (
        "401 novo sem família declarada. O interceptor do auth-refresh.js falha "
        "FECHADO: sem `WWW_AUTHENTICATE_401` ele não renova e o usuário cai no "
        f"login. Classifique em `_401_RENOVAVEL`: {sem_classificacao}"
    )

    literais = sorted({c for c, forma in achados if forma == "literal"})
    assert not literais, (
        "o `WWW-Authenticate` foi escrito como dict literal. O comportamento está "
        "certo e a §0.7 não: use `headers=WWW_AUTHENTICATE_401` (importe de "
        f"`frontend/routes/shared.py`), que é a fonte única do valor: {literais}"
    )

    faltando = sorted({c for c, forma in achados if _401_RENOVAVEL[c] and not forma})
    assert not faltando, (
        "401 classificado como RENOVÁVEL e sem `headers=`. Acrescente "
        "`headers=WWW_AUTHENTICATE_401` no `raise`/`Response` — sem ele o "
        "interceptor não renova e o usuário cai no login. Se este 401 é de "
        f"aplicação (o token está ótimo), troque a classificação para False: {faltando}"
    )

    sobrando = sorted({c for c, forma in achados if forma and not _401_RENOVAVEL[c]})
    assert not sobrando, (
        "401 classificado como DE APLICAÇÃO e com `WWW-Authenticate`. Ele vai "
        "renovar o token à toa e gastar dois slots do rate limit. Tire o "
        f"`headers=`, ou mude a classificação para True se ele é de autenticação: {sobrando}"
    )

    # Controle positivo: a família A não pode ter esvaziado. Se um refactor tirar
    # o `headers=` de todos os sítios, as três asserções acima continuariam verdes
    # caso a classificação fosse trocada junto — este número não.
    com_header = sorted(c for c, forma in achados if forma)
    assert len(com_header) == 8, (
        f"são {len(com_header)} sítios com `WWW-Authenticate`, e este controle "
        "espera 8. Se você ADICIONOU um 401 de autenticação legítimo (já "
        "classificado como True acima), atualize o 8 para o número novo. Se você "
        f"não mexeu em nenhum 401, alguém apagou um `headers=`: {com_header}"
    )
