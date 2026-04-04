from __future__ import annotations

import logging

PROD_DASHBOARD_BASE_URL = "https://pigbankai.com"


def get_dashboard_base_url() -> str:
    return PROD_DASHBOARD_BASE_URL


logger = logging.getLogger(__name__)


def build_dashboard_link(user_id: int, hours: float = 0.25) -> str | None:
    base_url = get_dashboard_base_url()

    try:
        from db import create_dashboard_session

        code = create_dashboard_session(user_id, hours=hours)
        return f"{base_url}/d/{code}"
    except Exception:
        logger.exception("Falha ao gerar link seguro do dashboard para user_id=%s", user_id)
        return None
