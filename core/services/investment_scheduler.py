"""
Atualização automática dos investimentos.

O cálculo de juros é idempotente por investimento/lote porque usa last_date.
Assim, o loop pode rodar algumas vezes ao dia: ele só aplica datas novas quando
taxas oficiais novas estiverem disponíveis.
"""
from __future__ import annotations

import asyncio
import logging
import os

from core.observability import log_system_event_sync

logger = logging.getLogger(__name__)

DEFAULT_CHECK_INTERVAL_HOURS = 6
DEFAULT_STARTUP_DELAY_SECONDS = 90


def _interval_seconds() -> int:
    raw = os.getenv("INVESTMENT_ACCRUAL_INTERVAL_HOURS", str(DEFAULT_CHECK_INTERVAL_HOURS))
    try:
        hours = float(raw)
    except ValueError:
        hours = DEFAULT_CHECK_INTERVAL_HOURS
    return max(int(hours * 3600), 900)


def _startup_delay_seconds() -> int:
    raw = os.getenv("INVESTMENT_ACCRUAL_STARTUP_DELAY_SECONDS", str(DEFAULT_STARTUP_DELAY_SECONDS))
    try:
        return max(int(raw), 0)
    except ValueError:
        return DEFAULT_STARTUP_DELAY_SECONDS


def accrue_all_users_investments() -> dict[str, int]:
    import db

    user_ids = db.list_users_with_investments()
    updated = 0
    failed = 0

    for user_id in user_ids:
        try:
            db.accrue_all_investments(user_id)
            updated += 1
        except Exception as exc:
            failed += 1
            logger.warning("Falha ao atualizar investimentos user_id=%s: %s", user_id, exc, exc_info=True)
            log_system_event_sync(
                "warning",
                "investment_accrual_user_failed",
                f"Falha ao atualizar investimentos automaticamente: {exc}",
                source="investment_scheduler",
                user_id=user_id,
            )

    return {"users": len(user_ids), "updated": updated, "failed": failed}


async def run_investment_accrual_loop() -> None:
    await asyncio.sleep(_startup_delay_seconds())
    interval = _interval_seconds()

    while True:
        try:
            result = await asyncio.to_thread(accrue_all_users_investments)
            logger.info(
                "[investments] accrual automatico concluido users=%s updated=%s failed=%s",
                result["users"],
                result["updated"],
                result["failed"],
            )
            log_system_event_sync(
                "info",
                "investment_accrual_completed",
                "Atualizacao automatica de investimentos concluida.",
                source="investment_scheduler",
                details=result,
            )
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("[investments] erro no loop de accrual: %s", exc, exc_info=True)
            log_system_event_sync(
                "error",
                "investment_accrual_loop_error",
                f"Erro no loop de atualizacao automatica de investimentos: {exc}",
                source="investment_scheduler",
            )

        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            break
