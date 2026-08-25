"""Helpers comuns aos routers de frontend/routes/.

Cada etapa do refactor (docs/refactor_plan.md, Fase 1) move pra cá somente o
que os routers extraídos precisam — auth deps, limiter e cookies entram quando
as rotas que os usam saírem do monólito.

Monkeypatch em testes: patchar `frontend.routes.shared.<nome>` (os routers
chamam via atributo de módulo, ex: `shared.authorize_dashboard_access`).
"""

import asyncio
import functools
import hashlib
import json
import logging
import os
import pathlib
import re
from datetime import date, datetime, timezone
from html import escape
from decimal import Decimal
from typing import Any

import jwt as pyjwt
from fastapi import HTTPException, Request
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from slowapi import Limiter
from slowapi.util import get_remote_address

from config.env import load_app_env
from core.sessions import get_active_session
from token_utils import decode_dashboard_token_full

# Idempotente (os.environ.setdefault) — garante .env carregado mesmo quando
# este módulo é importado antes do load_app_env() do monólito.
load_app_env()

# Diretório frontend/ — onde vivem os .html e assets servidos ao navegador.
FRONTEND_DIR = pathlib.Path(__file__).resolve().parent.parent

DASHBOARD_URL = os.getenv("DASHBOARD_URL", "http://localhost:8000").strip()
# Sanitiza caso a var de ambiente tenha sido definida como "DASHBOARD_URL=https://..."
if DASHBOARD_URL.startswith("DASHBOARD_URL="):
    DASHBOARD_URL = DASHBOARD_URL[len("DASHBOARD_URL="):]
DASHBOARD_URL = DASHBOARD_URL.rstrip("/")

DATABASE_URL = os.getenv("DATABASE_URL")
DB_CONNECT_TIMEOUT = int(os.getenv("DB_CONNECT_TIMEOUT", "5"))
JWT_SECRET = (os.getenv("JWT_SECRET") or "").strip()

AUTH_COOKIE_NAME = "auth_token"
DASHBOARD_COOKIE_NAME = "dashboard_token"

# Meta (Facebook) Pixel — injetado no <head> das páginas públicas quando setado.
# Vazio em dev/staging → nenhum pixel é injetado (site limpo, sem rastreio).
META_PIXEL_ID = (os.getenv("META_PIXEL_ID") or "").strip()

# default_limits exige SlowAPIMiddleware (nunca registrado) — hoje é inerte;
# só os @limiter.limit() explícitos valem. Ligar o middleware é decisão aberta.
limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])


def meta_pixel_snippet() -> str:
    """Código base do Meta Pixel pra injetar no topo do <head>.

    Retorna string vazia quando META_PIXEL_ID não está configurado — assim o
    site roda sem rastreio em dev/staging sem mexer em nada.
    """
    if not META_PIXEL_ID:
        return ""
    pid = META_PIXEL_ID
    return (
        "<!-- Meta Pixel Code -->\n"
        "<script>\n"
        "!function(f,b,e,v,n,t,s){if(f.fbq)return;n=f.fbq=function(){n.callMethod?\n"
        "n.callMethod.apply(n,arguments):n.queue.push(arguments)};if(!f._fbq)f._fbq=n;\n"
        "n.push=n;n.loaded=!0;n.version='2.0';n.queue=[];t=b.createElement(e);t.async=!0;\n"
        "t.src=v;s=b.getElementsByTagName(e)[0];s.parentNode.insertBefore(t,s)}(window,\n"
        "document,'script','https://connect.facebook.net/en_US/fbevents.js');\n"
        f"fbq('init', '{pid}');\n"
        "fbq('track', 'PageView');\n"
        "</script>\n"
        "<noscript><img height=\"1\" width=\"1\" style=\"display:none\" "
        f'src="https://www.facebook.com/tr?id={pid}&ev=PageView&noscript=1"/></noscript>\n'
        "<!-- End Meta Pixel Code -->\n"
    )


def inject_meta_pixel(html_text: str) -> str:
    """Insere o Meta Pixel imediatamente antes de </head> (o mais alto possível).

    No-op quando não há pixel configurado ou a página não tem </head>.
    """
    snippet = meta_pixel_snippet()
    if not snippet:
        return html_text
    idx = html_text.lower().find("</head>")
    if idx == -1:
        return html_text
    return html_text[:idx] + snippet + html_text[idx:]


# ─── Cache-buster dos assets (?v=) ───────────────────────────────────────────
# Antes o `?v=N` de app-mode.css/js, pb-nav.js etc. era hardcoded no <head> de
# cada HTML e bumpado à mão a cada deploy — o que dava merge conflict toda vez
# que duas PRs mexiam no mesmo asset em paralelo (v=31 vs v=33 na mesma linha).
# Agora o número no HTML é ignorado: reescrevemos `?v=N` no serve-time com um
# hash do conteúdo do próprio arquivo. Zero bump manual → zero conflito, e a
# invalidação passa a ser exata (só muda quando o arquivo muda de verdade).
_ASSET_VER_RE = re.compile(r"(/[A-Za-z0-9_.-]+\.(?:css|js))\?v=\d+")


@functools.lru_cache(maxsize=256)
def _asset_hash(name: str, _mtime_ns: int) -> str:
    # `_mtime_ns` entra na chave do cache só pra forçar recomputo quando o
    # arquivo muda em disco (dev sem restart); não é usado no corpo.
    data = (FRONTEND_DIR / name).read_bytes()
    return hashlib.blake2b(data, digest_size=6).hexdigest()


def stamp_asset_versions(html_text: str) -> str:
    """Troca o `?v=N` de cada CSS/JS de frontend/ por um hash do conteúdo.

    Aplicado em TODA saída de HTML (template, gerado em Python, montado à mão) —
    ver os call sites: `html_file` logo abaixo, `error_page_response` (no cache do
    template), static_pages.py e as duas páginas geradas no monólito. Assets fora
    de FRONTEND_DIR ficam intactos.
    """
    def repl(m: "re.Match[str]") -> str:
        url = m.group(1)             # ex: /app-mode.css
        try:
            mtime_ns = (FRONTEND_DIR / url[1:]).stat().st_mtime_ns
        except OSError:
            return m.group(0)        # arquivo não existe aqui → deixa como está
        return f"{url}?v={_asset_hash(url[1:], mtime_ns)}"

    return _ASSET_VER_RE.sub(repl, html_text)


def html_file(path: pathlib.Path, pixel: bool = True) -> Response:
    """Serve um .html do frontend com cache desligado.

    Com `pixel=True` (padrão) e META_PIXEL_ID setado, injeta o Meta Pixel no
    <head>. As páginas da área logada (dashboard, settings, onboarding) passam
    `pixel=False` — o rastreio de marketing fica só nas páginas públicas.
    """
    text = path.read_text(encoding="utf-8")
    if pixel and META_PIXEL_ID:
        text = inject_meta_pixel(text)
    response = Response(content=stamp_asset_versions(text),
                        media_type="text/html; charset=utf-8")
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


# ─── Página de erro HTML (navegação) ─────────────────────────────────────────
# Texto SEMPRE constante do servidor: exc.detail nunca chega aqui — ele carrega
# str(exc) de exceção arbitrária (finance_bot_websocket_custom.py:3208), nem
# sempre é string (dict/lista nos 422) e vaza nome de parâmetro interno
# (parse_date_param).
_ERROR_TEXTS = {
    404: ("Página não encontrada", "Este endereço não existe ou mudou de lugar."),
    # 405 herda o texto do 404 de propósito: o Allow já diz que o recurso existe,
    # mas para quem navegou é o mesmo beco sem saída — não vale uma tela própria.
    405: ("Página não encontrada", "Este endereço não existe ou mudou de lugar."),
    401: ("Acesso negado", "Você não tem acesso a esta página. Entre na sua conta e tente de novo."),
    403: ("Acesso negado", "Você não tem acesso a esta página. Entre na sua conta e tente de novo."),
    # 410 é hoje só o link de download da exportação (finance_bot_websocket_custom.py:3203),
    # clicado DE DENTRO do e-mail — navegação pura. O default 4xx tirava a única
    # instrução acionável da tela, por isso ele tem texto próprio.
    410: ("Link expirado", "Este link já foi usado ou passou da validade. Peça um novo em Configurações → Meus dados."),
    429: ("Muitas tentativas", "Você fez muitas requisições em pouco tempo. Aguarde um instante."),
    400: ("Requisição inválida", "Não consegui entender esse pedido."),
    422: ("Requisição inválida", "Não consegui entender esse pedido."),
    503: ("Serviço indisponível", "Estamos com instabilidade. Tente novamente em instantes."),
}
_ERROR_DEFAULT_4XX = ("Não deu pra abrir", "Algo nesse pedido não está certo.")
_ERROR_DEFAULT_5XX = ("Algo deu errado do nosso lado", "Já registramos o problema. Tente de novo em instantes.")

_error_template: str | None = None
# Já logamos a queda pro fallback? Sem isto o warning sai POR REQUISIÇÃO, e no
# processo web o root logger carrega o _DashboardHandler (core/observability.py:23),
# que faz psycopg.connect() + INSERT bloqueante dentro do event loop — um connect
# por registro. Medido aqui, chamando logging.warning() em série com e sem o
# handler, contra o Postgres em localhost: 11,3 ms na 1ª chamada, 3,7 ms/chamada
# em 10 e 2,1 ms/chamada em 25, contra 0,02 ms com o handler fora. Cada connect
# paga o RTT até o banco, então com Postgres remoto (produção) a conta é maior —
# quanto, não dá pra medir daqui (CLAUDE.md §6: produção inacessível). Loga na
# transição pro estado degradado e cala enquanto ele durar.
_error_degraded = False

# Só estes headers do exc.headers são repassados à página. O resto é descartado
# porque quem monta o exc não sabe que a resposta virou HTML: um Content-Length
# vindo dali mata a conexão (h11: "Too much data for declared Content-Length") e
# um Content-Type rotula ~1,4 KB de HTML como application/json. `location` também
# fica de fora: todo 3xx do repo é RedirectResponse (que não passa por aqui) — não
# há um `raise HTTPException` 3xx sequer —, então na prática ele só faria um 404
# sair com Location.
_ERROR_PASSTHROUGH_HEADERS = frozenset({"allow", "www-authenticate", "retry-after"})

# Último recurso quando nem o template abre (não entrou no deploy, permissão,
# disco). Autossuficiente: nada de arquivo, nada de import, mesmos placeholders.
# Sem CSS/JS externo de propósito (só style inline e um <a href="/">), então não
# passa nem precisa passar pelo `stamp_asset_versions` — não há `?v=` aqui.
_ERROR_FALLBACK_HTML = (
    '<!DOCTYPE html><html lang="pt-BR" style="background:#050506"><head><meta charset="UTF-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1">'
    '<meta name="robots" content="noindex"><title>{{CODE}} — {{TITLE}} | PigBank</title></head>'
    '<body style="background:#050506;color:#fff;font-family:sans-serif;text-align:center;padding:64px 24px">'
    "<h1>{{CODE}}</h1><h2>{{TITLE}}</h2><p>{{MESSAGE}}</p>"
    '<p><a href="/" style="color:#FF2D8E">← Página inicial</a></p></body></html>'
)


def wants_html(request: Request) -> bool:
    """True quando a requisição é navegação (browser/WebView), não chamada de API.

    Só o Accept: `*/*` NÃO conta — é o que o fetch de API e o TestClient mandam,
    e é o que mantém o contrato JSON (o front lê `detail` dali).

    ponytail: comparação por substring, sem q-values — `text/html;q=0.1,
    application/json;q=0.9` cai no ramo HTML. Inclusive `text/html;q=0`, que é a
    forma padrão de dizer "NÃO me mande isto": é o único caso em que a
    simplificação faz o oposto do pedido em vez de só ignorar a preferência.
    Fica assim de propósito — separar `q=0` de `q=0.5` já é parsear Accept, e
    zero clientes nossos mandam qualquer q (pb-nav.js:226,255 pede text/html;
    blog-news.js:89 pede application/json; o resto é fetch sem Accept → */*).
    Accept duplicado também: o Starlette devolve só a primeira ocorrência, então
    dois headers caem no ramo da primeira — navegador nenhum duplica Accept.
    Se algum dia importar: a stdlib não tem parser de Accept e o werkzeug (cujo
    `http.parse_accept_header` resolveria) NÃO está no requirements.txt — só chega
    transitivamente no venv local, então contar com ele quebraria no CI/produção.
    São ~10 linhas à mão (split por vírgula, ler o `q=`, ordenar) ou assumir a
    dependência de propósito."""
    # Case-insensitive: media type não tem caixa (RFC 7231 §3.1.1.1) — sem o
    # .lower() um `Accept: TEXT/HTML` caía no ramo JSON.
    return "text/html" in (request.headers.get("accept") or "").lower()


def vary_accept(response: Response) -> Response:
    """Marca a resposta como dependente do Accept (ramo JSON dos erros).

    O ramo HTML já sai marcado do `error_page_response`. `add_vary_header` soma
    ao Vary existente em vez de sobrescrever (o CORS depois soma o `Origin`)."""
    response.headers.add_vary_header("Accept")
    return response


def error_page_response(status_code: int, headers: dict | None = None,
                        text: tuple[str, str] | None = None) -> Response:
    """Página de erro HTML preservando o status (nada de soft-404) e no-store.

    `{{CODE}}`/`{{TITLE}}`/`{{MESSAGE}}` do error.html saem do mapa fixo
    `_ERROR_TEXTS` acima, mas o escape é por CONSTRUÇÃO e não por regra escrita:
    título e mensagem passam por `html.escape()` e a troca é em UMA passagem (ver
    abaixo). Constante do servidor não tem nada para escapar, então o custo é zero
    e a garantia deixa de depender de quem lê este parágrafo.

    `text=(titulo, mensagem)` sobrepõe o mapa para o caso em que o status já está
    tomado por outra coisa e a tela ficaria sem instrução — hoje só o `/unsubscribe`
    com token inválido, que é 400 como qualquer 422 de validação mas precisa dizer o
    que fazer. Continua sendo para constante do servidor (é texto de produto, não
    eco de entrada), só que agora um deslize ali sai escapado em vez de virar HTML.

    NÃO passa pelo `html_file`: aquele funil injeta o Meta Pixel, e a página de erro
    fica fora do rastreio de marketing por decisão explícita. O que ela precisa dele
    — o `stamp_asset_versions` — está aplicado abaixo, no cache do template.

    Sem caminho de falha: é a resposta de último recurso, então uma exceção aqui
    viraria 500 + `http_unhandled_exception` no log a cada 404 de bot varrendo URL.
    """
    global _error_template, _error_degraded
    default = _ERROR_DEFAULT_5XX if status_code >= 500 else _ERROR_DEFAULT_4XX
    # Desempacotamento que NÃO pode estourar (a promessa do parágrafo acima): um
    # `text=` de aridade errada levantava `ValueError: not enough values to
    # unpack` — medido com `text=("so-um-item",)` — e um call site errado bastava
    # para todo 404 daquela rota virar 500 + `http_unhandled_exception`. O
    # isinstance cobre o resto: `len()` de um não-Sized levanta TypeError, e
    # `escape()` de um não-str também. Fora do contrato → cai no texto do mapa.
    if not (isinstance(text, tuple) and len(text) == 2
            and all(isinstance(t, str) for t in text)):
        text = _ERROR_TEXTS.get(status_code, default)
    title, message = text
    template = _error_template
    if template is None:
        try:
            # Contrato do error.html — esta função é o único consumidor dele, e a
            # nota mora aqui e não lá dentro porque comentário em HTML VIAJA no
            # corpo de toda resposta de erro (era o caso; saiu). Quem for editar o
            # arquivo precisa saber de duas coisas: os três placeholders abaixo são
            # obrigatórios (a validação seguinte rejeita o arquivo sem eles), e nada
            # de CDN — só CSS inline e o `/safe-area.js` do próprio domínio, porque
            # esta página roda justamente quando algo já quebrou.
            # `raw`, não `text`: `text` é o parâmetro de override do título/
            # mensagem acima — reusar o nome aqui seria shadowing silencioso.
            raw = (FRONTEND_DIR / "error.html").read_text(encoding="utf-8")
            # Arquivo que ABRE mas veio pela metade (deploy interrompido, rsync
            # cortado, disco cheio) é o mesmo problema do arquivo ausente — e sem
            # esta checagem viraria cache envenenado até o restart. `</html>` é a
            # última linha (pega truncamento no fim, e o vazio de graça) e os TRÊS
            # placeholders precisam estar lá: truncamento não é a única corrupção —
            # lixo no meio, ou um `{{MESSAGE}}</html>` de 50 bytes, passava sem
            # {{CODE}}/{{TITLE}} e ia ao usuário sem o código do erro na tela.
            if not all(p in raw for p in ("{{CODE}}", "{{TITLE}}", "{{MESSAGE}}")) \
                    or not raw.rstrip().endswith("</html>"):
                raise ValueError(f"error.html incompleto ({len(raw)} bytes)")
            # Stamp aqui, JUNTO do cache, e não na montagem do corpo: o
            # `?v=` do <script src="/safe-area.js"> do error.html precisa do
            # hash como qualquer outra saída de HTML (senão é a única URL de
            # asset do produto que nunca invalida), mas re-hashear a cada
            # requisição pagaria um stat() por 404 de bot varrendo URL. O
            # preço é que trocar o safe-area.js sem reiniciar o processo deixa
            # ESTA página com o hash velho até o restart — em deploy o
            # processo reinicia, e o html_file/static_pages (que releem o
            # arquivo por requisição) continuam pegando na hora.
            template = _error_template = stamp_asset_versions(raw)
            _error_degraded = False  # arquivo voltou: pode logar a próxima queda
        except Exception as exc:
            # O fallback NÃO é cacheado: se o arquivo voltar (deploy pela metade),
            # a próxima requisição tenta de novo em vez de degradar até o restart.
            # Um log por TRANSIÇÃO, não por requisição (ver _error_degraded acima):
            # como nada é cacheado aqui, logar sempre transformaria um bot varrendo
            # URL em um INSERT síncrono por 404. Sem exc_info: ~25 linhas de
            # traceback não dizem mais que o repr (tipo + caminho).
            if not _error_degraded:
                _error_degraded = True
                logging.getLogger(__name__).warning(
                    "error.html indisponível (%r) — servindo fallback embutido", exc
                )
            template = _ERROR_FALLBACK_HTML
    # Escape + UMA passagem, no lugar dos três `.replace()` encadeados. Os dois
    # defeitos eram do encadeamento, não do texto: (1) o valor entrava cru, então
    # qualquer deslize no `text=` renderizava HTML; (2) a ordem CODE→TITLE→MESSAGE
    # reexpandia placeholder que caísse DENTRO de um valor — um título contendo
    # `{{MESSAGE}}` era reescrito pelo replace seguinte. O escape sozinho não
    # resolve o (2): `html.escape` não toca em chaves. A passagem única resolve os
    # dois. `re` e não `str.format`/`Template`: o template tem CSS cheia de chaves.
    valores = {"{{CODE}}": str(status_code),
               "{{TITLE}}": escape(title), "{{MESSAGE}}": escape(message)}
    body = re.sub(r"\{\{(?:CODE|TITLE|MESSAGE)\}\}", lambda m: valores[m.group()], template)
    response = Response(content=body, status_code=status_code, media_type="text/html; charset=utf-8")
    for key, value in (headers or {}).items():
        if key.lower() in _ERROR_PASSTHROUGH_HEADERS:
            response.headers[key] = value
    response.headers["Cache-Control"] = "no-store"
    # A mesma URL devolve HTML ou JSON conforme o Accept — sem isto um CDN pode
    # servir a página de erro para uma chamada de API (e vice-versa).
    response.headers.add_vary_header("Accept")
    return response


def public_site_url(path: str = "") -> str:
    base_url = DASHBOARD_URL if DASHBOARD_URL.startswith("https://") else "https://pigbankai.com"
    return f"{base_url.rstrip('/')}{path}"


# ─── JSON serializer ─────────────────────────────────────────────────────────

class FinanceEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):  return float(obj)
        if isinstance(obj, datetime): return obj.isoformat()
        if isinstance(obj, date):     return obj.isoformat()
        return super().default(obj)


def jdump(data: dict) -> str:
    return json.dumps(data, cls=FinanceEncoder, ensure_ascii=False)


def months_pt():
    return [
        "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
        "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
    ]


# ─── Cache do snapshot "mês corrente" do dashboard ───────────────────────────
# Estado compartilhado: o data fetcher (monólito) lê/escreve; launches
# (monólito) e pockets/cards (routers) invalidam após cada escrita.

DASHBOARD_CURRENT_CACHE_TTL_SECONDS = 45
dashboard_current_cache: dict[int, tuple[float, Any, Any, Any]] = {}


def invalidate_dashboard_current_cache(user_id: int) -> None:
    dashboard_current_cache.pop(int(user_id), None)


# ─── DB helpers (com connection pool) ────────────────────────────────────────
# Pool global de conexões assíncronas. Em vez de abrir nova conn a cada query
# (custa 1-2s no Railway), reusa de um pool. O `_PooledConn` mantém a interface
# antiga (`async with await db_connect() as conn:`) intacta — todos os callers
# antigos continuam funcionando sem mudança.

_db_pool: AsyncConnectionPool | None = None
_db_pool_lock = asyncio.Lock()


# NÃO ponha `reset=` neste pool. O pool síncrono (db/connection.py::_reset_conn)
# tem essa guarda porque lá o bug existia de verdade: o init_db ligava
# `autocommit` numa conexão do pool e não restaurava. Aqui nada liga autocommit
# (`git grep autocommit` confirma), e a guarda simétrica foi tentada e revertida:
# um callback de reset async vira uma task no event loop, e o encerramento do
# loop passa a pendurar em `asyncio.runners._cancel_all_tasks`. Medido: 33
# testes falhando e a suíte de 87s para 268s, com o arquivo
# tests/test_unsubscribe_one_click.py travando por completo.
async def _get_db_pool() -> AsyncConnectionPool:
    global _db_pool
    if _db_pool is not None:
        return _db_pool
    async with _db_pool_lock:
        if _db_pool is not None:  # double-check após pegar lock
            return _db_pool
        pool = AsyncConnectionPool(
            DATABASE_URL,
            min_size=1,
            max_size=int(os.getenv("DB_POOL_MAX", "8")),
            timeout=DB_CONNECT_TIMEOUT,
            kwargs={"row_factory": dict_row},
            open=False,
        )
        await pool.open(wait=True, timeout=DB_CONNECT_TIMEOUT)
        _db_pool = pool
        return _db_pool


class _PooledConn:
    """Adapter pra preservar a interface `async with await db_connect() as conn`.
    `pool.connection()` retorna um async-context-manager direto, mas o caller
    legado faz `await db_connect()` antes de entrar no async-with — esse wrapper
    casa os dois protocolos."""
    def __init__(self, pool: AsyncConnectionPool):
        self._pool = pool
        self._cm = None

    async def __aenter__(self):
        self._cm = self._pool.connection()
        return await self._cm.__aenter__()

    async def __aexit__(self, exc_type, exc, tb):
        if self._cm is None:
            return False
        return await self._cm.__aexit__(exc_type, exc, tb)


async def db_connect():
    pool = await _get_db_pool()
    return _PooledConn(pool)


# ─── Auth-token (JWT de login) deps ──────────────────────────────────────────

def make_jwt(user_id: int, email: str, *, jti: str | None = None) -> str:
    from datetime import timedelta
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "email": email,
        "type": "auth",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=15),
    }
    if jti:
        payload["jti"] = jti
    return pyjwt.encode(payload, JWT_SECRET, algorithm="HS256")


def decode_jwt(token: str) -> dict | None:
    try:
        return pyjwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except Exception:
        return None


def get_auth_token_from_request(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = None,
) -> str | None:
    if creds and creds.credentials:
        return creds.credentials
    cookie_token = (request.cookies.get(AUTH_COOKIE_NAME) or "").strip()
    return cookie_token or None


# ─── Auth do dashboard (token de escopo dashboard, cookie ou Bearer) ─────────

def extract_bearer_token(request: Request) -> str | None:
    auth = request.headers.get("authorization", "").strip()
    if not auth:
        return None
    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token.strip()


def raise_if_account_scheduled_for_deletion(user_id: int) -> None:
    from db import is_account_scheduled_for_deletion

    deletion = is_account_scheduled_for_deletion(int(user_id))
    if deletion:
        scheduled = deletion.get("deletion_scheduled_for")
        scheduled_txt = scheduled.isoformat() if hasattr(scheduled, "isoformat") else str(scheduled)
        raise HTTPException(
            status_code=403,
            detail=f"Esta conta está agendada para exclusão em {scheduled_txt}.",
        )


def resolve_dashboard_user_id(request: Request) -> int:
    token = (
        extract_bearer_token(request)
        or (request.cookies.get(DASHBOARD_COOKIE_NAME) or "").strip()
    )
    payload = decode_dashboard_token_full(token or "")
    if not payload:
        raise HTTPException(status_code=401, detail="Token de dashboard inválido ou expirado.")
    user_id = payload["user_id"]
    jti = payload.get("jti")
    # Tokens com jti: validar contra auth_sessions (revogacao instantanea).
    # Tokens sem jti (legacy / rollout) sao grandfathered ate expirarem.
    if jti:
        session = get_active_session(jti)
        if not session or int(session.get("user_id") or 0) != user_id:
            raise HTTPException(status_code=401, detail="Sessão encerrada. Faça login novamente.")
        request.state.session_jti = jti
    else:
        # Token de dashboard legado sem jti: se a conta trocou de senha (reset),
        # invalida — senão um token roubado sobrevive ao reset até expirar (12h).
        from db import get_password_changed_at
        if get_password_changed_at(user_id):
            raise HTTPException(status_code=401, detail="Sessão encerrada. Faça login novamente.")
    return int(user_id)


# Prefixos de rota que continuam acessíveis sem assinatura ativa — senão o
# usuário bloqueado não conseguiria nem assinar nem sair.
_GATE_EXEMPT_PREFIXES = ("/billing", "/auth", "/conta")


def _is_pigbank_app(request: Request) -> bool:
    """True se a requisição vem do WebView do app iOS (UA anexa "PigBankApp").
    Usado pra isentar o app dos gates que forçariam a /precos — diretriz 3.1.1
    da App Store proíbe empurrar tela de compra; o app fica no acesso base."""
    return "PigBankApp" in (request.headers.get("user-agent") or "")


def signup_source_from_request(request: Request, *, google: bool = False) -> str:
    """Origem do cadastro, gravada em auth_accounts.signup_source. Distingue web
    de app iOS (mesmo UA que isenta o gate da /precos) pra o painel de admin
    separar quem passou pela escolha de plano de quem entrou pelo acesso base.

      web | app | google | google_app"""
    in_app = _is_pigbank_app(request)
    if google:
        return "google_app" if in_app else "google"
    return "app" if in_app else "web"


def _enforce_subscription_gate(request: Request, user_id: int) -> None:
    """Backstop server-side das rotas de dados do dashboard. Além do paywall
    (assinatura ativa/trial), fecha o gate de escolha de plano no cadastro: sem
    ele, um cadastro novo poderia pular a /precos batendo direto numa API
    autenticada (ex.: /data/{id}) ou navegando pro /settings. Retorna 402 pro
    front mandar ao paywall/escolha. As rotas de /billing, /auth e /conta são
    isentas (são elas que resolvem o gate — checkout, /auth/me, select-free)."""
    path = request.url.path or ""
    if any(path.startswith(p) for p in _GATE_EXEMPT_PREFIXES):
        return
    from core.services.plan_service import has_app_access, needs_plan_selection
    # App iOS não passa pelo gate de escolha (não pode comprar/escolher via web);
    # segue governado por has_app_access e pelos limites por-feature/tier.
    if not _is_pigbank_app(request) and needs_plan_selection(user_id):
        raise HTTPException(status_code=402, detail={"error": "plan_selection_required"})
    if not has_app_access(user_id):
        raise HTTPException(status_code=402, detail={"error": "subscription_required"})


def authorize_dashboard_access(request: Request, user_id: int) -> int:
    current_user_id = resolve_dashboard_user_id(request)
    if current_user_id != int(user_id):
        raise HTTPException(status_code=403, detail="Acesso negado para este usuário.")
    raise_if_account_scheduled_for_deletion(current_user_id)
    _enforce_subscription_gate(request, current_user_id)
    return current_user_id


def gate_pro_page(request: Request):
    """Gate de PÁGINA só-Pro, pra rotas que servem HTML (ex: /changelog, /blog/*).

    Retorna None quando o acesso é permitido (usuário Pro/trial autenticado — is_pro
    inclui trial). Caso contrário, devolve um RedirectResponse:
      - deslogado / sessão inválida → /login?next=<path>
      - logado mas sem Pro         → /precos
    Diferente de authorize_dashboard_access (que levanta HTTP p/ APIs), aqui a gente
    redireciona porque é navegação de página no browser.
    """
    from urllib.parse import quote
    from fastapi.responses import RedirectResponse
    from core.services.plan_service import is_pro

    path = request.url.path or "/"
    login_redirect = RedirectResponse(url=f"/login?next={quote(path)}", status_code=302)

    token = get_auth_token_from_request(request, None)
    payload = decode_jwt(token) if token else None
    if not payload or payload.get("type") != "auth":
        return login_redirect
    user_id = int(payload["sub"])
    jti = payload.get("jti")
    if jti:
        session = get_active_session(jti)
        if not session or int(session.get("user_id") or 0) != user_id:
            return login_redirect
    if not is_pro(user_id):
        return RedirectResponse(url="/precos", status_code=302)
    return None


def _resolve_page_user_id(request: Request) -> int | None:
    """user_id de uma navegação de página autenticada, aceitando os DOIS cookies
    que valem uma sessão logada: auth_token (JWT de 15min) E dashboard_token (12h).

    Espelha o que /auth/validate + resolve_dashboard_user_id aceitam. Sem isto, o
    gate acharia "deslogado" quando o access expira mas o dashboard_token ainda é
    válido (estado normal — o front só renova o access DEPOIS que o HTML carrega),
    servindo a página sem checar o gate. Devolve None quando nenhum cookie é uma
    sessão válida (aí o próprio HTML manda pro login)."""
    # 1. auth_token (JWT curto)
    token = get_auth_token_from_request(request, None)
    payload = decode_jwt(token) if token else None
    if payload and payload.get("type") == "auth":
        uid = int(payload["sub"])
        jti = payload.get("jti")
        if not jti:
            return uid
        session = get_active_session(jti)
        if session and int(session.get("user_id") or 0) == uid:
            return uid
    # 2. dashboard_token (cookie de 12h) — mesma validação de sessão/jti das
    #    rotas de dados. Levanta 401 quando inválido → tratamos como deslogado.
    try:
        return resolve_dashboard_user_id(request)
    except HTTPException:
        return None


def gate_plan_selection(request: Request):
    """Gate de PÁGINA do cadastro: obriga a escolher um plano na /precos antes
    de servir o HTML do dashboard (home/app/settings). É o enforcement REAL —
    os redirects em JS são só UX e são burláveis (JS em cache, navegação direta,
    página sem o script). Aqui o servidor decide antes de entregar a página.

    Retorna None quando pode servir (deslogado — o próprio HTML manda pro login;
    ou já escolheu plano). Devolve RedirectResponse pra /precos quando o usuário
    está logado e ainda não escolheu. Nunca levanta — é navegação de browser."""
    from fastapi.responses import RedirectResponse
    from core.services.plan_service import needs_plan_selection

    # App iOS (WebView anexa "PigBankApp" ao UA): não redireciona pra tela de
    # planos/compra — diretriz 3.1.1 da App Store. Espelha o !window.PB_IN_APP
    # do cliente. O acesso segue governado pelos gates por-feature/tier.
    if _is_pigbank_app(request):
        return None

    # Retorno do checkout com sucesso: o webhook checkout.session.completed
    # (que fecha o gate via mark_plan_selected) pode ainda estar em trânsito.
    # Não jogamos quem ACABOU de pagar de volta pra /precos — a tela de
    # confirmação em /home espera o webhook e libera (fail-open). Espelha o
    # bypass _justUpgraded do cliente. Só o gate de ESCOLHA é dispensado aqui;
    # o paywall por feature/tier segue valendo normalmente.
    if request.query_params.get("upgrade") == "success":
        return None

    user_id = _resolve_page_user_id(request)
    # Deslogado / sessão inválida: deixa o HTML carregar e redirecionar pro login
    # (comportamento atual preservado — não força /precos em quem nem logou).
    if user_id is None:
        return None
    try:
        if needs_plan_selection(user_id):
            return RedirectResponse(url="/precos?escolha=1", status_code=302)
    except Exception:
        # Nunca trava a navegação por erro no gate; o backstop de dados (402) e o
        # redirect em JS seguem valendo como rede de segurança.
        logging.getLogger(__name__).warning("gate_plan_selection falhou", exc_info=True)
    return None


# ─── Janela de análise (rotas de analytics e history) ────────────────────────

def parse_date_param(value: str | None, name: str) -> date | None:
    """Parsea 'YYYY-MM-DD' → date. None se vazio. 400 se inválido."""
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Parâmetro '{name}' inválido (esperado YYYY-MM-DD).")


def resolve_analytics_window(months: int, from_str: str | None, to_str: str | None):
    """Wrapper de resolve_window que parseia strings de query."""
    from db import resolve_window
    fd = parse_date_param(from_str, "from")
    td = parse_date_param(to_str, "to")
    return resolve_window(months=months, from_date=fd, to_date=td)
