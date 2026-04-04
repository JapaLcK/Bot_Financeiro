from __future__ import annotations

import os


PROD_DASHBOARD_BASE_URL = "https://pigbankai.com"


def get_dashboard_base_url() -> str:
    raw = (os.getenv("DASHBOARD_URL") or "").strip().rstrip("/")
    if raw.startswith("DASHBOARD_URL="):
        raw = raw[len("DASHBOARD_URL="):].rstrip("/")

    if (not raw) or ("localhost" in raw) or ("127.0.0.1" in raw):
        return PROD_DASHBOARD_BASE_URL

    return raw


def build_dashboard_link(user_id: int, hours: int = 2) -> str:
    base_url = get_dashboard_base_url()

    try:
        from db import create_dashboard_session

        code = create_dashboard_session(user_id, hours=hours)
        return f"{base_url}/d/{code}"
    except Exception:
        return f"{base_url}/app?user_id={user_id}"
