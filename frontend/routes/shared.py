"""Helpers comuns aos routers de frontend/routes/.

Cada etapa do refactor (docs/refactor_plan.md, Fase 1) move pra cá somente o
que os routers extraídos precisam — auth deps, limiter e cookies entram quando
as rotas que os usam saírem do monólito.

Monkeypatch em testes: patchar `frontend.routes.shared.<nome>` (os routers
chamam via atributo de módulo, ex: `shared.authorize_dashboard_access`).
"""

import os
import pathlib
from datetime import date

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse

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

DASHBOARD_COOKIE_NAME = "dashboard_token"


def html_file(path: pathlib.Path) -> FileResponse:
    response = FileResponse(path, media_type="text/html")
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


def public_site_url(path: str = "") -> str:
    base_url = DASHBOARD_URL if DASHBOARD_URL.startswith("https://") else "https://pigbankai.com"
    return f"{base_url.rstrip('/')}{path}"


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
