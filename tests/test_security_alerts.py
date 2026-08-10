"""Testes do detector de spike de falha de login (A09 detector 1).

Integração leve: usa o banco de teste (como test_audit.py). Exercita o novo
desenho — detecção em transação própria, depois do commit do login, com
cooldown por IP. Cada teste usa um IP distinto pra não interferir nos outros.
"""
import asyncio

import pytest

from core.services import admin_notify, security_alerts


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _ensure_tables():
    from core.admin_dashboard import ensure_admin_tables
    _run(ensure_admin_tables())


async def _insert_failures(ip: str, n: int) -> None:
    from core.admin_dashboard import db_connect
    async with await db_connect() as conn:
        async with conn.cursor() as cur:
            for _ in range(n):
                await cur.execute(
                    "insert into auth_login_events (success, ip_address, failure_reason) "
                    "values (false, %s, 'bad_password')",
                    (ip,),
                )
        await conn.commit()


async def _marker_count(ip: str) -> int:
    from core.admin_dashboard import db_connect
    async with await db_connect() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "select count(*) as n from system_event_logs "
                "where event_type = 'auth_spike_alert' and details->>'key' = %s",
                (f"ip:{ip}",),
            )
            row = await cur.fetchone()
    return row["n"] if isinstance(row, dict) else row[0]


@pytest.fixture
def _capture(monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(admin_notify, "notify_security_alert", lambda m: sent.append(m) or True)
    monkeypatch.setenv("AUTH_ALERT_THRESHOLD", "5")
    monkeypatch.setenv("AUTH_ALERT_COOLDOWN_MIN", "30")
    monkeypatch.delenv("SECURITY_ALERTS_ENABLED", raising=False)
    return sent


def test_abaixo_do_threshold_nao_alerta(_capture):
    ip = "203.0.113.201"
    _run(_insert_failures(ip, 4))
    _run(security_alerts._detect_and_maybe_alert(ip))
    assert _capture == []
    assert _run(_marker_count(ip)) == 0


def test_no_threshold_alerta_e_grava_marcador(_capture):
    ip = "203.0.113.202"
    _run(_insert_failures(ip, 5))
    _run(security_alerts._detect_and_maybe_alert(ip))
    assert len(_capture) == 1
    assert ip in _capture[0]
    assert _run(_marker_count(ip)) == 1


def test_cooldown_nao_realerta(_capture):
    ip = "203.0.113.203"
    _run(_insert_failures(ip, 6))
    _run(security_alerts._detect_and_maybe_alert(ip))   # 1º: alerta
    _run(security_alerts._detect_and_maybe_alert(ip))   # 2º: em cooldown
    assert len(_capture) == 1
    assert _run(_marker_count(ip)) == 1


def test_schedule_desligado_por_env_e_noop(monkeypatch):
    # SECURITY_ALERTS_ENABLED=0 → schedule vira no-op (nem agenda a task).
    monkeypatch.setenv("SECURITY_ALERTS_ENABLED", "0")
    security_alerts.schedule_auth_failure_spike_check("203.0.113.204")  # não levanta


def test_schedule_sem_ip_e_noop():
    security_alerts.schedule_auth_failure_spike_check(None)  # não levanta
