"""Testes da retenção de auth_login_events.

Integração leve (banco de teste). Garante que a purga apaga só FALHAS antigas
e preserva logins bem-sucedidos (âncora do is_known_login_ip) e falhas recentes.
"""
import asyncio

import pytest

from core.services import login_events_retention as ret


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _ensure_tables():
    from core.admin_dashboard import ensure_admin_tables
    _run(ensure_admin_tables())


async def _insert(ip, success, days_ago):
    from core.admin_dashboard import db_connect
    async with await db_connect() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "insert into auth_login_events (success, ip_address, created_at) "
                "values (%s, %s, now() - (%s || ' days')::interval)",
                (success, ip, str(days_ago)),
            )
        await conn.commit()


async def _exists(ip, success):
    from core.admin_dashboard import db_connect
    async with await db_connect() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "select count(*) as n from auth_login_events "
                "where ip_address = %s and success = %s",
                (ip, success),
            )
            row = await cur.fetchone()
    return (row["n"] if isinstance(row, dict) else row[0]) > 0


def test_purga_so_falhas_antigas(monkeypatch):
    monkeypatch.setenv("LOGIN_EVENTS_RETENTION_DAYS", "90")
    ip = "198.51.100.10"
    _run(_insert(ip, False, 120))   # falha antiga  → deve sumir
    _run(_insert(ip, False, 5))     # falha recente → fica
    _run(_insert(ip, True, 120))    # sucesso antigo (âncora IP) → fica

    deleted = _run(ret.purge_old_login_events())

    assert deleted == 1
    assert _run(_exists(ip, False)) is True   # a falha recente sobrou
    assert _run(_exists(ip, True)) is True    # o sucesso antigo foi preservado


def test_desligado_e_noop(monkeypatch):
    monkeypatch.setenv("LOGIN_EVENTS_RETENTION_DAYS", "0")
    assert _run(ret.purge_old_login_events()) == 0
