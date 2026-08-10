"""
core/services/security_alerts.py — Detecção de eventos de segurança → alerta.

Hoje: spike de falha de login por IP (A09 detector 1). Quando um IP acumula
muitas falhas de login numa janela curta, dispara um alerta administrativo via
`core.services.admin_notify` (Slack/Discord).

Arquitetura (importante — evita 3 armadilhas):
  - A detecção roda em uma **task de fundo** (`asyncio.create_task`), DEPOIS de
    o evento de login já ter sido commitado. Assim:
      1. NÃO atrasa a resposta do login (o webhook não é aguardado inline).
      2. Um erro de SQL do detector NÃO aborta a transação do login (conexão
         separada) — o registro de auditoria do login está garantido.
      3. A contagem enxerga as tentativas concorrentes já commitadas (não fica
         cega pra inserts não-commitados de outras transações).
  - Serializa a detecção por IP com `pg_advisory_xact_lock`, então rajadas
    concorrentes do mesmo IP não geram alerta duplicado nem perdem o threshold.
  - Fire-and-forget: no-op silencioso sem `ADMIN_NOTIFY_WEBHOOK_URL`; nunca
    levanta — um alerta que falha não pode derrubar a autenticação.
  - Anti-storm: cooldown por IP via marcador em `system_event_logs`.
  - Desligável sem deploy: `SECURITY_ALERTS_ENABLED=0`.

Config (envs, todas opcionais):
  - SECURITY_ALERTS_ENABLED   (default '1')
  - AUTH_ALERT_THRESHOLD      (default '5')   falhas na janela pra alertar
  - AUTH_ALERT_WINDOW_MIN     (default '5')   tamanho da janela, em minutos
  - AUTH_ALERT_COOLDOWN_MIN   (default '30')  silêncio por IP após um alerta
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from psycopg.types.json import Jsonb

logger = logging.getLogger(__name__)

_ALERT_EVENT_TYPE = "auth_spike_alert"

# Mantém referência forte às tasks de fundo (senão o GC pode coletá-las).
_background_tasks: set[asyncio.Task] = set()


def _enabled() -> bool:
    return (os.getenv("SECURITY_ALERTS_ENABLED", "1") or "").strip().lower() not in (
        "0", "false", "no", "off", "",
    )


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _cell(row: Any, key: str, idx: int) -> Any:
    """Lê um valor de row seja ela dict_row (dict) ou tupla."""
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[idx]
    except (IndexError, KeyError, TypeError):
        return None


def schedule_auth_failure_spike_check(ip_address: str | None) -> None:
    """Agenda a detecção de spike numa task de fundo — FORA da transação do
    login e do caminho de resposta. Chamar DEPOIS do commit do evento de login.

    No-op sem IP, desligado, ou sem event loop rodando (ex.: chamada síncrona
    em teste). Nunca levanta.
    """
    if not _enabled() or not ip_address:
        return
    try:
        task = asyncio.create_task(_detect_and_maybe_alert(ip_address))
    except RuntimeError:
        # Sem event loop em execução — nada a agendar.
        return
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _detect_and_maybe_alert(ip_address: str) -> None:
    """Roda em transação PRÓPRIA (conexão separada), serializada por IP.

    Como roda depois do commit do login, a contagem enxerga as tentativas já
    persistidas. O advisory lock garante que rajadas concorrentes do mesmo IP
    avaliem o threshold e gravem o cooldown de forma atômica. Nunca levanta.
    """
    try:
        threshold = _int_env("AUTH_ALERT_THRESHOLD", 5)
        window = _int_env("AUTH_ALERT_WINDOW_MIN", 5)
        cooldown = _int_env("AUTH_ALERT_COOLDOWN_MIN", 30)
        payload = None

        # Import tardio: admin_dashboard puxa muita coisa; evita ciclo no boot.
        from core.admin_dashboard import db_connect

        async with await db_connect() as conn:
            async with conn.cursor() as cur:
                # Serializa a detecção por IP (lock de transação, liberado no
                # commit). Evita alerta duplicado / threshold perdido sob rajada.
                await cur.execute(
                    "select pg_advisory_xact_lock(hashtext(%s)::bigint)",
                    (f"auth_spike:{ip_address}",),
                )
                await cur.execute(
                    """
                    select count(*) as n
                    from auth_login_events
                    where ip_address = %s
                      and success = false
                      and created_at >= now() - (%s || ' minutes')::interval
                    """,
                    (ip_address, str(window)),
                )
                n = _cell(await cur.fetchone(), "n", 0) or 0
                if n >= threshold:
                    key = f"ip:{ip_address}"
                    await cur.execute(
                        """
                        select 1
                        from system_event_logs
                        where event_type = %s
                          and details ->> 'key' = %s
                          and created_at >= now() - (%s || ' minutes')::interval
                        limit 1
                        """,
                        (_ALERT_EVENT_TYPE, key, str(cooldown)),
                    )
                    if not await cur.fetchone():
                        await cur.execute(
                            """
                            insert into system_event_logs
                              (level, event_type, message, source, details)
                            values ('warning', %s, %s, 'security_alerts', %s)
                            """,
                            (
                                _ALERT_EVENT_TYPE,
                                f"Spike de falha de login: {n} em {window}min (IP {ip_address})",
                                Jsonb({"key": key, "count": int(n), "ip": ip_address, "window": window}),
                            ),
                        )
                        payload = {"ip": ip_address, "count": int(n), "window": window}
            await conn.commit()

        if payload:
            await _send_alert(payload)
    except Exception as exc:  # pragma: no cover - defensivo
        logger.warning("[security_alerts] detecção de spike falhou: %s", exc)


async def _send_alert(payload: dict) -> None:
    """Envia o alerta pelo webhook. Já roda fora da transação e da task do
    request, então pode aguardar o envio em thread sem impactar ninguém."""
    try:
        from core.services.admin_notify import notify_security_alert

        message = (
            "🚨 Spike de falha de login\n"
            f"IP {payload['ip']} — {payload['count']} falhas em {payload['window']}min"
        )
        await asyncio.to_thread(notify_security_alert, message)
    except Exception as exc:  # pragma: no cover - defensivo
        logger.warning("[security_alerts] envio do alerta falhou: %s", exc)
