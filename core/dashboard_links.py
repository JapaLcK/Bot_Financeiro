from __future__ import annotations

import logging

from token_utils import make_dashboard_token


PROD_DASHBOARD_BASE_URL = "https://pigbankai.com"


def get_dashboard_base_url() -> str:
    return PROD_DASHBOARD_BASE_URL


logger = logging.getLogger(__name__)


def build_dashboard_link(user_id: int, hours: int = 2) -> str | None:
    base_url = get_dashboard_base_url()

    try:
        token = make_dashboard_token(user_id, hours=hours)
        return f"{base_url}/app?token={token}"
    except Exception:
        logger.exception("Falha ao gerar link seguro do dashboard para user_id=%s", user_id)
        return None
