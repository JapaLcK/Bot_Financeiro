from __future__ import annotations

import os
import sys
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from config.env import load_app_env


load_app_env()


def _database_url() -> str:
    return (os.getenv("DATABASE_URL") or "").strip()


def log_system_event_sync(
    level: str,
    event_type: str,
    message: str,
    *,
    source: str | None = None,
    user_id: int | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    database_url = _database_url()
    if not database_url:
        return

    try:
        with psycopg.connect(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO system_event_logs (level, event_type, message, source, user_id, details)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (level, event_type, message[:1000], source, user_id, Jsonb(details or {})),
                )
            conn.commit()
    except Exception as exc:
        print(f"[observability] failed to record {event_type}: {exc}", file=sys.stderr)
