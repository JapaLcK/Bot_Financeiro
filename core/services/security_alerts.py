"""
core/services/security_alerts.py — Detecção de eventos de segurança → alerta.

Hoje: spike de falha de login por IP (A09 detector 1). Quando um IP acumula
muitas falhas de login numa janela curta, dispara um alerta administrativo via
`core.services.admin_notify` (Slack/Discord).

Princípios:
  - Fire-and-forget: nunca bloqueia o login; o envio roda em thread separada.
  - No-op silencioso sem `ADMIN_NOTIFY_WEBHOOK_URL` (herda de admin_notify).
  - Nunca levanta exceção: um alerta que falha não pode derrubar a autenticação.
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


async def detect_auth_failure_spike(cur, *, ip_address: str | None) -> dict | None:
    """Roda DENTRO da transação de `log_auth_login_event` (mesmo cursor async),
    logo já enxerga a tentativa recém-inserida.

    Se o IP passou do threshold de falhas na janela E não está em cooldown,
    grava o marcador de cooldown (na mesma transação) e devolve o payload do
    alerta pra ser disparado FORA da transação. Caso contrário devolve None.

    Nunca levanta: em qualquer erro, devolve None (login segue normal).
    """
    if not _enabled() or not ip_address:
        return None
    try:
        threshold = _int_env("AUTH_ALERT_THRESHOLD", 5)
        window = _int_env("AUTH_ALERT_WINDOW_MIN", 5)
        cooldown = _int_env("AUTH_ALERT_COOLDOWN_MIN", 30)

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
        if n < threshold:
            return None

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
        if await cur.fetchone():
            return None  # já alertamos sobre esse IP recentemente

        await cur.execute(
            """
            insert into system_event_logs (level, event_type, message, source, details)
            values ('warning', %s, %s, 'security_alerts', %s)
            """,
            (
                _ALERT_EVENT_TYPE,
                f"Spike de falha de login: {n} em {window}min (IP {ip_address})",
                Jsonb({"key": key, "count": int(n), "ip": ip_address, "window": window}),
            ),
        )
        return {"ip": ip_address, "count": int(n), "window": window}
    except Exception as exc:  # pragma: no cover - defensivo
        logger.warning("[security_alerts] detect_auth_failure_spike falhou: %s", exc)
        return None


async def fire_auth_spike_alert(payload: dict) -> None:
    """Dispara o alerta (fire-and-forget). Chamar FORA da transação do login."""
    try:
        from core.services.admin_notify import notify_security_alert

        message = (
            "🚨 Spike de falha de login\n"
            f"IP {payload['ip']} — {payload['count']} falhas em {payload['window']}min"
        )
        await asyncio.to_thread(notify_security_alert, message)
    except Exception as exc:  # pragma: no cover - defensivo
        logger.warning("[security_alerts] fire_auth_spike_alert falhou: %s", exc)
