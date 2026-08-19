"""Helpers comuns aos routers de frontend/routes/.

Cada etapa do refactor (docs/refactor_plan.md, Fase 1) move pra cá somente o
que os routers extraídos precisam — auth deps, limiter e cookies entram quando
as rotas que os usam saírem do monólito.

Monkeypatch em testes: patchar `frontend.routes.shared.<nome>` (os routers
chamam via atributo de módulo, ex: `shared.authorize_dashboard_access`).
"""

import asyncio
import json
import logging
import os
import pathlib
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import jwt as pyjwt
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, Response
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


def html_file(path: pathlib.Path, pixel: bool = True) -> Response:
    """Serve um .html do frontend com cache desligado.

    Com `pixel=True` (padrão) e META_PIXEL_ID setado, injeta o Meta Pixel no
    <head>. As páginas da área logada (dashboard, settings, onboarding) passam
    `pixel=False` — o rastreio de marketing fica só nas páginas públicas.
    """
    if pixel and META_PIXEL_ID:
        text = inject_meta_pixel(path.read_text(encoding="utf-8"))
        response: Response = Response(content=text, media_type="text/html; charset=utf-8")
    else:
        response = FileResponse(path, media_type="text/html")
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
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
