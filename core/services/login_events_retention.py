"""
core/services/login_events_retention.py — Retenção de auth_login_events.

Apaga tentativas de login FALHAS mais antigas que N dias (minimização / LGPD).
É o mecanismo que cobre as tentativas ÓRFÃS (falha de login antes de a conta
existir, sem user_id): a exclusão de conta apaga o que tem user_id, e este job
apaga as falhas órfãs por tempo — sem precisar buscar por e-mail. Logins
bem-sucedidos NÃO são apagados aqui (ancoram is_known_login_ip; já saem na
exclusão de conta por user_id).

Loop diário, registrado no lifespan do app.

Config (envs, opcionais):
  - LOGIN_EVENTS_RETENTION_DAYS  (default '90')  — 0 desliga o job.
  - LOGIN_EVENTS_RETENTION_INTERVAL_HOURS (default '24')
"""
from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger(__name__)


def _retention_days() -> int:
    try:
        return int(os.getenv("LOGIN_EVENTS_RETENTION_DAYS", "90"))
    except (TypeError, ValueError):
        return 90


def _interval_hours() -> int:
    try:
        return int(os.getenv("LOGIN_EVENTS_RETENTION_INTERVAL_HOURS", "24"))
    except (TypeError, ValueError):
        return 24


async def purge_old_login_events() -> int:
    """Apaga tentativas de login FALHAS com mais de N dias. Retorna quantas caíram.

    Só falhas (`success = false`) de propósito:
      - São exatamente as linhas órfãs (falha pré-conta, `user_id` nulo) que a
        exclusão de conta não alcança — o alvo original desta retenção.
      - Os logins BEM-SUCEDIDOS são preservados porque ancoram
        `core.audit.is_known_login_ip` (senão um IP conhecido, mas sem login nos
        últimos N dias, voltaria a disparar e-mail de "novo dispositivo").
        Esses já são apagados por `user_id` na exclusão de conta.

    Com LOGIN_EVENTS_RETENTION_DAYS <= 0, é no-op (retorna 0)."""
    days = _retention_days()
    if days <= 0:
        return 0
    # Import tardio pra evitar ciclo de import no boot (admin_dashboard puxa muita
    # coisa); mesmo padrão dos wrappers do lifespan.
    from core.admin_dashboard import db_connect

    async with await db_connect() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                delete from auth_login_events
                where success = false
                  and created_at < now() - (%s || ' days')::interval
                """,
                (str(days),),
            )
            deleted = cur.rowcount
        await conn.commit()
    return int(deleted or 0)


async def run_login_events_retention_loop() -> None:
    """Loop perpétuo (asyncio task no lifespan). Roda a purga 1x/dia."""
    await asyncio.sleep(120)  # deixa o boot assentar antes do 1º tick
    while True:
        try:
            n = await purge_old_login_events()
            if n:
                logger.info(
                    "[login_retention] apagou %s login events > %s dias",
                    n, _retention_days(),
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - defensivo
            logger.warning("[login_retention] erro: %s", exc)
        await asyncio.sleep(_interval_hours() * 3600)
