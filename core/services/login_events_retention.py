"""
core/services/login_events_retention.py — Retenção de auth_login_events e
auth_rate_limits: dois jobs independentes (conexão e commit próprios), no mesmo
loop diário.

1) purge_old_login_events: apaga tentativas de login FALHAS mais antigas que N
dias (minimização / LGPD). É o mecanismo que cobre as tentativas ÓRFÃS (falha de login antes de a conta
existir, sem user_id): a exclusão de conta apaga o que tem user_id, e este job
apaga as falhas órfãs por tempo — sem precisar buscar por e-mail. Logins
bem-sucedidos NÃO são apagados aqui (ancoram is_known_login_ip; já saem na
exclusão de conta por user_id).

2) purge_old_rate_limits: apaga contadores de auth_rate_limits (`ip:<IP real>`,
`email:`, `user:`) parados há mais de RATE_LIMITS_RETENTION. Sempre ligado: a
minimização do IP real não depende da retenção de auditoria acima.

Loop diário, registrado no lifespan do app.

Config (envs, opcionais):
  - LOGIN_EVENTS_RETENTION_DAYS  (default '90')  — 0 desliga só o job 1.
  - LOGIN_EVENTS_RETENTION_INTERVAL_HOURS (default '24')
"""
from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

# Tem de ser >= a maior janela de quem grava em auth_rate_limits, senão a purga
# zera contador com janela correndo. Maior janela = 3600 s (register,
# forgot-password, quiz, quiz-webhook, quiz-conta), medida em 2026-10-06 com
#   grep -rn "_check_persistent_rate_limit(\|EMAIL_RATE_LIMITS = \|TETO_GLOBAL_WEBHOOK =\|LIMITE_IP_QUIZ =" frontend/
# mais o síncrono de `db/rate_limits.py` (wa-link-code, 900 s, 2026-10-08):
#   grep -rn "rate_limit_estourado(\|_TETO_CODIGO =" core/
# — remeça antes de baixar este valor.
RATE_LIMITS_RETENTION = "1 day"


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


async def purge_old_rate_limits() -> int:
    """Apaga contadores de auth_rate_limits parados há mais de RATE_LIMITS_RETENTION.
    Retorna quantos caíram. Sem lote: 200 mil linhas levaram ~0,4 s (Tester, 2026-10-06)."""
    from core.admin_dashboard import db_connect

    async with await db_connect() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "delete from auth_rate_limits where updated_at < now() - %s::interval",
                (RATE_LIMITS_RETENTION,),
            )
            deleted = cur.rowcount
        await conn.commit()
    return int(deleted or 0)


async def run_login_events_retention_loop() -> None:
    """Loop perpétuo (asyncio task no lifespan). Roda as duas purgas 1x/dia;
    a falha de uma não impede a outra."""
    await asyncio.sleep(120)  # deixa o boot assentar antes do 1º tick
    while True:
        for purge in (purge_old_login_events, purge_old_rate_limits):
            try:
                n = await purge()
                if n:
                    logger.info("[login_retention] %s apagou %s linhas", purge.__name__, n)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("[login_retention] %s erro: %s", purge.__name__, exc)
        await asyncio.sleep(_interval_hours() * 3600)
