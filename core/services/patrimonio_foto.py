"""
Foto diária do patrimônio (dashboard v2, etapa 0 — docs/plano-dashboard-v2.md §4).

A cada hora, a partir das 18h do fuso do app (decisão do dono), grava a foto do
dia de quem ainda não tem (`db/patrimonio.py`). Atrás de `PATRIMONIO_FOTO_ENABLED`,
desligada por padrão e lida a cada volta: desligada, a volta não consulta nada.
Sem trava entre instâncias: a PK (user_id, dia) + `on conflict do nothing` basta.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

from core.observability import log_system_event_sync
from utils_date import _tz

logger = logging.getLogger(__name__)

HORA_DA_FOTO = 18
INTERVALO_S = 3600


def ligado() -> bool:
    return (os.getenv("PATRIMONIO_FOTO_ENABLED") or "").strip().lower() in ("1", "true", "yes", "on")


def gravar_fotos_do_dia(now: datetime | None = None) -> dict | None:
    if not ligado():
        return None
    local = (now or datetime.now(timezone.utc)).astimezone(_tz())
    if local.hour < HORA_DA_FOTO:
        return None

    from core.reports.reports_daily import filtrar_por_acesso
    from db import patrimonio

    dia = local.date()
    gravadas = falhas = 0
    for uid in filtrar_por_acesso(patrimonio.candidatos(dia)):
        try:
            gravadas += patrimonio.gravar_foto(uid, dia)
        except Exception as exc:
            falhas += 1
            # Só o tipo: a mensagem do psycopg pode trazer valor da linha.
            log_system_event_sync("warning", "patrimonio_foto_falhou",
                                  f"Foto do patrimônio não gravada: {type(exc).__name__}",
                                  source="patrimonio_foto", user_id=uid)
    return {"dia": dia.isoformat(), "gravadas": gravadas, "falhas": falhas}


async def run_patrimonio_foto_loop() -> None:
    while True:
        try:
            result = await asyncio.to_thread(gravar_fotos_do_dia)
            if result:
                logger.info("[patrimonio_foto] %s", result)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("[patrimonio_foto] erro no laço: %s", type(exc).__name__, exc_info=True)
            log_system_event_sync("error", "patrimonio_foto_loop_error",
                                  f"Erro no laço da foto do patrimônio: {type(exc).__name__}",
                                  source="patrimonio_foto")
        try:
            await asyncio.sleep(INTERVALO_S)
        except asyncio.CancelledError:
            break
