"""Helpers comuns aos routers de frontend/routes/.

Cada etapa do refactor (docs/refactor_plan.md, Fase 1) move pra cá somente o
que os routers extraídos precisam — auth deps, limiter e cookies entram quando
as rotas que os usam saírem do monólito.
"""

import os
import pathlib

from fastapi.responses import FileResponse

from config.env import load_app_env

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


def html_file(path: pathlib.Path) -> FileResponse:
    response = FileResponse(path, media_type="text/html")
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


def public_site_url(path: str = "") -> str:
    base_url = DASHBOARD_URL if DASHBOARD_URL.startswith("https://") else "https://pigbankai.com"
    return f"{base_url.rstrip('/')}{path}"
