"""
core/services/preview_scheduler.py

Reset diario do usuario demo (modo preview).

O endpoint /preview/login ja chama reset_preview_user_data a cada login,
mas isso garante que mesmo sem trafego o demo fique limpo. Tambem ajuda
quando alguem deixa o preview aberto por horas e quer voltar a um estado
inicial sem dar refresh com a key na mao.
"""
from __future__ import annotations

import asyncio
import logging
import os

from core.observability import log_system_event_sync

logger = logging.getLogger(__name__)


DEFAULT_RESET_INTERVAL_HOURS = 24
DEFAULT_STARTUP_DELAY_SECONDS = 120


def _interval_seconds() -> int:
    raw = os.getenv("PREVIEW_RESET_INTERVAL_HOURS", str(DEFAULT_RESET_INTERVAL_HOURS))
    try:
        hours = float(raw)
    except ValueError:
        hours = DEFAULT_RESET_INTERVAL_HOURS
    # Minimo 30min para evitar reset em loop curto demais.
    return max(int(hours * 3600), 1800)


def _startup_delay_seconds() -> int:
    raw = os.getenv("PREVIEW_RESET_STARTUP_DELAY_SECONDS", str(DEFAULT_STARTUP_DELAY_SECONDS))
    try:
        return max(int(raw), 0)
    except ValueError:
        return DEFAULT_STARTUP_DELAY_SECONDS


def _reset_demo_data_sync() -> bool:
    """Reset sincrono - chamado dentro de asyncio.to_thread."""
    import db

    try:
        db.reset_preview_user_data()
        return True
    except Exception as exc:
        logger.warning("Falha ao resetar user demo: %s", exc, exc_info=True)
        log_system_event_sync(
            "warning",
            "preview_reset_failed",
            f"Falha ao resetar dados do user demo: {exc}",
            source="preview_scheduler",
        )
        return False


async def run_preview_reset_loop() -> None:
    """Roda indefinidamente, chamando reset a cada PREVIEW_RESET_INTERVAL_HOURS."""
    if not (os.getenv("PREVIEW_KEY") or "").strip():
        logger.info("[preview] PREVIEW_KEY nao configurado - scheduler desligado")
        return

    await asyncio.sleep(_startup_delay_seconds())
    interval = _interval_seconds()

    while True:
        try:
            ok = await asyncio.to_thread(_reset_demo_data_sync)
            if ok:
                logger.info("[preview] reset do user demo concluido")
                log_system_event_sync(
                    "info",
                    "preview_reset_completed",
                    "Reset automatico do user demo concluido.",
                    source="preview_scheduler",
                )
        except Exception as exc:
            logger.exception("[preview] erro inesperado no loop: %s", exc)

        await asyncio.sleep(interval)
