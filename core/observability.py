from __future__ import annotations

import logging
import os
import sys
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from config.env import load_app_env


load_app_env()

# ── Logger centralizado ───────────────────────────────────────────────────────

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_root_configured = False


class _DashboardHandler(logging.Handler):
    """
    Handler que espelha WARNING e ERROR no dashboard (tabela system_event_logs).
    Só grava se DATABASE_URL estiver configurado.
    """

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno < logging.WARNING:
            return
        level = record.levelname.lower()      # "warning" | "error" | "critical"
        source = record.name                   # nome do módulo (ex: adapters.discord.discord_bot)
        message = self.format(record)

        # inclui traceback no campo details se disponível
        details: dict[str, Any] = {"logger": record.name}
        if record.exc_info:
            import traceback as _tb
            details["traceback"] = _tb.format_exception(*record.exc_info)

        # chama de forma síncrona — handler roda em thread do bot
        log_system_event_sync(
            level,
            event_type=f"logger.{level}",
            message=message[:1000],
            source=source,
            details=details,
        )


def _configure_root_logger() -> None:
    global _root_configured
    if _root_configured:
        return

    root = logging.getLogger()
    if not root.handlers:
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATE_FORMAT))
        root.setLevel(logging.INFO)
        root.addHandler(stderr_handler)

    # adiciona handler do dashboard se ainda não estiver presente
    if not any(isinstance(h, _DashboardHandler) for h in root.handlers):
        dash_handler = _DashboardHandler()
        dash_handler.setLevel(logging.WARNING)
        root.addHandler(dash_handler)

    _root_configured = True


def get_logger(name: str) -> logging.Logger:
    """
    Retorna um logger configurado. Use no topo de cada módulo:
        logger = get_logger(__name__)

    WARNING e ERROR aparecem automaticamente no dashboard admin.
    """
    _configure_root_logger()
    return logging.getLogger(name)


# ── DB event log ──────────────────────────────────────────────────────────────

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
