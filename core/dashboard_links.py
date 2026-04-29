from __future__ import annotations

import logging
import os

DEFAULT_DASHBOARD_BASE_URL = "http://localhost:8000"


def get_dashboard_base_url() -> str:
    base_url = (os.getenv("DASHBOARD_URL") or DEFAULT_DASHBOARD_BASE_URL).strip()
    if base_url.startswith("DASHBOARD_URL="):
        base_url = base_url[len("DASHBOARD_URL="):]
    return base_url.rstrip("/")


logger = logging.getLogger(__name__)


def build_dashboard_link(user_id: int, hours: float = 5 / 60, view: str | None = None) -> str | None:
    base_url = get_dashboard_base_url()

    try:
        from db import create_dashboard_session

        code = create_dashboard_session(user_id, hours=hours)
        suffix = ""
        if view in {"overview", "investments", "open-finance"}:
            suffix = f"?view={view}"
        return f"{base_url}/d/{code}{suffix}"
    except Exception:
        logger.exception("Falha ao gerar link seguro do dashboard para user_id=%s", user_id)
        return None
