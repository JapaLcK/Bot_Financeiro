"""
core/services/proactive_ai_scheduler.py — Pré-computa insights e padrões IA proativos.

Sprint 7 #2. Pra cada user ativo nos últimos 7 dias, chama
`generate_ai_insights` e `generate_ai_patterns` antecipadamente — popula o
cache em `ai_proactive_cache`, evitando que o user veja cold call de 3-8s
quando abre a view Análises.

Configuração:
  - Roda 1x/dia, default às 3h UTC (≈ 0h BRT)
  - Override via env PROACTIVE_AI_HOUR_UTC (0-23)
  - Janela de "ativos" = 7 dias (last_activity_at)

Sem rate-limit entre users — volume muito baixo (~3-5 hoje). Se a base
crescer pra centenas, reavaliar e adicionar sleep entre chamadas.

TTLs dos caches em core/ai_patterns.py:
  - insights: 6h  → após ~6h do cron, cache expira e card volta ao cold call
  - patterns: 24h → coberto pelo cron diário

Telemetria: cada execução grava system_event_logs event_type='proactive_ai_run'.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

from core.observability import log_system_event_sync

logger = logging.getLogger(__name__)


# ─── Constantes ───────────────────────────────────────────────────────────────
RUN_HOUR_UTC = int(os.getenv("PROACTIVE_AI_HOUR_UTC", "3"))  # 3h UTC = 0h BRT
ACTIVE_DAYS = 7
MAX_USERS_PER_RUN = 200  # guardrail defensivo
STARTUP_DELAY_SECONDS = 30  # espera o servidor estabilizar antes do primeiro tick


async def run_proactive_ai_loop() -> None:
    """Loop perpétuo: dorme até RUN_HOUR_UTC do próximo dia, executa, repete."""
    await asyncio.sleep(STARTUP_DELAY_SECONDS)
    logger.info("[proactive-ai] Loop iniciado. Alvo: %dh UTC todo dia.", RUN_HOUR_UTC)

    while True:
        try:
            wait = _seconds_until_next_run()
            logger.info(
                "[proactive-ai] Aguardando %.0fs (~%.1fh) até próxima execução.",
                wait, wait / 3600,
            )
            await asyncio.sleep(wait)
            await _precompute_once()
        except asyncio.CancelledError:
            logger.info("[proactive-ai] Loop cancelado (shutdown).")
            raise
        except Exception as exc:
            logger.error("[proactive-ai] Erro no loop: %s", exc, exc_info=True)
            await asyncio.sleep(60)  # backoff defensivo


def _seconds_until_next_run() -> float:
    """Devolve segundos até o próximo RUN_HOUR_UTC do dia (hoje se ainda não passou,
    senão amanhã)."""
    now = datetime.now(timezone.utc)
    today_target = now.replace(
        hour=RUN_HOUR_UTC, minute=0, second=0, microsecond=0
    )
    target = today_target if now < today_target else today_target + timedelta(days=1)
    return max(60.0, (target - now).total_seconds())


async def _precompute_once() -> None:
    """Uma rodada de pré-computa: itera users ativos e popula caches."""
    started = datetime.now(timezone.utc)
    loop = asyncio.get_event_loop()

    try:
        users = await loop.run_in_executor(None, _fetch_active_users)
    except Exception as exc:
        logger.error("[proactive-ai] Falha ao buscar users ativos: %s", exc, exc_info=True)
        return

    if not users:
        logger.info("[proactive-ai] Sem users ativos nos últimos %dd. Nada a fazer.", ACTIVE_DAYS)
        log_system_event_sync(
            "info", "proactive_ai_run",
            f"proactive_ai pre-compute: 0 users ativos nos últimos {ACTIVE_DAYS}d.",
            source="proactive_ai_scheduler",
            details={"users": 0, "ok": 0, "err": 0, "elapsed_s": 0.0},
        )
        return

    logger.info("[proactive-ai] Iniciando pré-computa pra %d user(s).", len(users))

    n_ok = 0
    n_err = 0
    for uid in users:
        try:
            await loop.run_in_executor(None, _precompute_user, uid)
            n_ok += 1
        except Exception as exc:
            n_err += 1
            logger.warning("[proactive-ai] user=%s falhou: %s", uid, exc)

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    summary = (
        f"proactive_ai pre-compute: users={len(users)}, ok={n_ok}, "
        f"err={n_err}, elapsed={elapsed:.1f}s"
    )
    logger.info("[proactive-ai] %s", summary)
    log_system_event_sync(
        "info" if n_err == 0 else "warning",
        "proactive_ai_run",
        summary,
        source="proactive_ai_scheduler",
        details={
            "users": len(users),
            "ok": n_ok,
            "err": n_err,
            "elapsed_s": round(elapsed, 1),
            "active_days_window": ACTIVE_DAYS,
        },
    )


def _fetch_active_users() -> list[int]:
    """Lista users com last_activity_at nos últimos ACTIVE_DAYS dias."""
    from db.connection import get_conn
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select user_id
            from auth_accounts
            where last_activity_at is not null
              and last_activity_at >= now() - (%s || ' days')::interval
            order by last_activity_at desc
            limit %s
            """,
            (str(ACTIVE_DAYS), MAX_USERS_PER_RUN),
        )
        return [int(row["user_id"]) for row in cur.fetchall()]


def _precompute_user(user_id: int) -> None:
    """Gera insights + patterns pra um user. Lazy import evita ciclo no startup."""
    from core.ai_patterns import generate_ai_insights, generate_ai_patterns
    # force=False: respeita cache TTL — se já tá fresh, retorna sem chamar LLM
    generate_ai_insights(user_id)
    generate_ai_patterns(user_id)
