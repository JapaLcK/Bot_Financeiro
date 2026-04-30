"""
core/services/engagement_scheduler.py

Loop assíncrono que roda uma vez por dia e dispara emails de engajamento
para os usuários do PigBank AI.

Lógica:
  - Usuário inativo há 7+ dias  → email de reengajamento (uma vez por período de
                                   inatividade — não repete até o usuário voltar
                                   e sumir de novo)
  - Usuário ativo (usou nos últimos 7 dias):
      → email de dica de uso     uma vez a cada 28 dias
      → email de insight         uma vez a cada 28 dias, com mínimo de 1 dia de
                                   distância do email de dica (nunca no mesmo dia)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta

from core.observability import log_system_event_sync

logger = logging.getLogger(__name__)

# ─── Constantes ───────────────────────────────────────────────────────────────
INACTIVE_DAYS        = 7    # dias sem uso para considerar inativo
MONTHLY_INTERVAL     = 28   # intervalo mínimo entre emails do mesmo tipo (dias)
CHECK_INTERVAL_HOURS = 24   # frequência do loop


# ─── Loop principal ───────────────────────────────────────────────────────────

async def run_engagement_loop() -> None:
    """
    Task assíncrona que roda indefinidamente.
    Aguarda 60 s na primeira execução (dá tempo do app subir por completo),
    depois checa engagement a cada 24 h.
    """
    await asyncio.sleep(60)  # pequeno delay inicial

    while True:
        try:
            logger.info("[engagement] Iniciando verificação diária...")
            await _check_and_send()
            logger.info("[engagement] Verificação concluída.")
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("[engagement] Erro inesperado: %s", exc, exc_info=True)
            log_system_event_sync(
                "error",
                "engagement_loop_error",
                f"Erro inesperado no loop de engagement: {exc}",
                source="engagement_scheduler",
            )

        try:
            await asyncio.sleep(CHECK_INTERVAL_HOURS * 3600)
        except asyncio.CancelledError:
            break


# ─── Lógica de engajamento ────────────────────────────────────────────────────

async def _check_and_send() -> None:
    import db
    from core.services.email_service import (
        send_reengagement_email,
        send_tip_email,
        send_insight_email,
    )

    loop = asyncio.get_event_loop()

    # db.py é síncrono — roda em thread pool para não bloquear o event loop
    users: list[dict] = await loop.run_in_executor(None, db.get_users_for_engagement)

    now = datetime.now(timezone.utc)
    inactive_threshold = now - timedelta(days=INACTIVE_DAYS)
    monthly_threshold  = now - timedelta(days=MONTHLY_INTERVAL)

    for user in users:
        user_id          = user["user_id"]
        email            = user["email"]
        last_activity    = user["last_activity_at"]
        last_tip         = user["last_tip_sent_at"]
        last_insight     = user["last_insight_sent_at"]
        last_reeng       = user["last_reengagement_sent_at"]
        tip_opt_out      = bool(user.get("tip_email_opt_out", False))
        insight_opt_out  = bool(user.get("insight_email_opt_out", False))

        # Usuário nunca usou o bot → sem dados de atividade → pula
        if last_activity is None:
            continue

        is_active = last_activity >= inactive_threshold

        # ── Reengajamento ────────────────────────────────────────────────────
        if not is_active:
            # Envia apenas se ainda não enviamos desde a última atividade
            # (evita reenvios diários enquanto o usuário continua inativo)
            never_sent   = last_reeng is None
            stale_reeng  = last_reeng is not None and last_reeng < last_activity

            if never_sent or stale_reeng:
                ok = await loop.run_in_executor(None, send_reengagement_email, email, user_id)
                if ok:
                    await loop.run_in_executor(None, db.mark_reengagement_sent, user_id)
                    logger.info("[engagement] reengajamento → user_id=%s (%s)", user_id, email)
                    log_system_event_sync(
                        "info",
                        "engagement_reengagement_sent",
                        "Email de reengajamento enviado.",
                        source="engagement_scheduler",
                        user_id=user_id,
                    )

            continue  # usuário inativo não recebe dica/insight

        # ── Dica mensal ──────────────────────────────────────────────────────
        tip_sent_now = False
        should_send_tip = not tip_opt_out and (last_tip is None or last_tip < monthly_threshold)

        if should_send_tip:
            ok = await loop.run_in_executor(None, send_tip_email, email, user_id)
            if ok:
                await loop.run_in_executor(None, db.mark_tip_sent, user_id)
                logger.info("[engagement] dica → user_id=%s (%s)", user_id, email)
                log_system_event_sync(
                    "info",
                    "engagement_tip_sent",
                    "Email de dica enviado.",
                    source="engagement_scheduler",
                    user_id=user_id,
                )
                tip_sent_now = True

        # ── Insight mensal ───────────────────────────────────────────────────
        # Nunca envia insight no mesmo dia que a dica
        if tip_sent_now:
            continue

        should_send_insight = not insight_opt_out and (last_insight is None or last_insight < monthly_threshold)

        if should_send_insight:
            ok = await loop.run_in_executor(None, send_insight_email, email, user_id)
            if ok:
                await loop.run_in_executor(None, db.mark_insight_sent, user_id)
                logger.info("[engagement] insight → user_id=%s (%s)", user_id, email)
                log_system_event_sync(
                    "info",
                    "engagement_insight_sent",
                    "Email de insight enviado.",
                    source="engagement_scheduler",
                    user_id=user_id,
                )
