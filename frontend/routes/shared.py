"""Helpers comuns aos routers de frontend/routes/.

Cada etapa do refactor (docs/refactor_plan.md, Fase 1) move pra cá somente o
que os routers extraídos precisam — auth deps, limiter e cookies entram quando
as rotas que os usam saírem do monólito.

Monkeypatch em testes: patchar `frontend.routes.shared.<nome>` (os routers
chamam via atributo de módulo, ex: `shared.authorize_dashboard_access`).
"""

import asyncio
import json
import os
import pathlib
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import jwt as pyjwt
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse
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

# default_limits exige SlowAPIMiddleware (nunca registrado) — hoje é inerte;
# só os @limiter.limit() explícitos valem. Ligar o middleware é decisão aberta.
limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])


def html_file(path: pathlib.Path) -> FileResponse:
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


# ─── DB helpers (com connection pool) ────────────────────────────────────────
# Pool global de conexões assíncronas. Em vez de abrir nova conn a cada query
# (custa 1-2s no Railway), reusa de um pool. O `_PooledConn` mantém a interface
# antiga (`async with await db_connect() as conn:`) intacta — todos os callers
# antigos continuam funcionando sem mudança.

_db_pool: AsyncConnectionPool | None = None
_db_pool_lock = asyncio.Lock()


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
    return int(user_id)


def authorize_dashboard_access(request: Request, user_id: int) -> int:
    current_user_id = resolve_dashboard_user_id(request)
    if current_user_id != int(user_id):
        raise HTTPException(status_code=403, detail="Acesso negado para este usuário.")
    raise_if_account_scheduled_for_deletion(current_user_id)
    return current_user_id


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
