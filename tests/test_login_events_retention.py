"""Testes da retenção de auth_login_events e auth_rate_limits.

Integração leve (banco de teste). Garante que a purga apaga só FALHAS antigas
e preserva logins bem-sucedidos (âncora do is_known_login_ip) e falhas recentes.
"""
import asyncio
import types

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


async def _rate_rows(rows=(), bucket=None, ids=()):
    """Insere (bucket, identifier, idade da janela, idade do updated_at) e devolve
    os identifiers de `ids` que restam no `bucket`. O conftest zera a tabela a cada teste."""
    from core.admin_dashboard import db_connect
    async with await db_connect() as conn:
        async with conn.cursor() as cur:
            for b, ident, window_age, updated_age in rows:
                await cur.execute(
                    "insert into auth_rate_limits (bucket, identifier, window_started_at, attempts, updated_at) "
                    "values (%s, %s, now() - %s::interval, 1, now() - %s::interval)",
                    (b, ident, window_age, updated_age),
                )
            await cur.execute(
                "select identifier from auth_rate_limits where bucket = %s and identifier = any(%s)",
                (bucket, list(ids)),
            )
            found = await cur.fetchall()
        await conn.commit()
    return {r["identifier"] if isinstance(r, dict) else r[0] for r in found}


# Todos os baldes que gravam em auth_rate_limits (grep do comentário de RATE_LIMITS_RETENTION).
BUCKETS = ["login", "register", "forgot-password", "verify-email", "mfa-verify", "quiz", "quiz-webhook", "quiz-conta"]
IDENTS = ["ip:198.51.100.20", "email:ret@example.com", "user:42", "global"]


@pytest.mark.parametrize("bucket", BUCKETS)
def test_purga_contadores_de_rate_limit_vencidos(bucket):
    rows = (
        [(bucket, i, "25 hours", "25 hours") for i in IDENTS]                 # > 1 dia → somem
        + [(bucket, i + "-recente", "23 hours", "23 hours") for i in IDENTS]  # < 1 dia → ficam
        + [(bucket, "janela-velha", "25 hours", "23 hours"),  # contador ativo de janela antiga → fica
           (bucket, "updated-velho", "23 hours", "25 hours")]  # artificial: prova que o corte é por updated_at
    )
    _run(_rate_rows(rows))

    assert _run(ret.purge_old_rate_limits()) == len(IDENTS) + 1
    assert _run(_rate_rows(bucket=bucket, ids=[r[1] for r in rows])) == (
        {i + "-recente" for i in IDENTS} | {"janela-velha"}
    )


class _Fim(Exception):
    pass


def _um_ciclo(monkeypatch):
    """Roda o loop real por um ciclo: o sleep do boot passa, o do intervalo encerra."""
    chamadas = []

    async def sleep(s):
        chamadas.append(s)
        if len(chamadas) > 1:
            raise _Fim

    monkeypatch.setattr(ret, "asyncio", types.SimpleNamespace(sleep=sleep, CancelledError=asyncio.CancelledError))
    with pytest.raises(_Fim):
        _run(ret.run_login_events_retention_loop())


def test_dias_zero_desliga_so_login_events(monkeypatch):
    monkeypatch.setenv("LOGIN_EVENTS_RETENTION_DAYS", "0")
    ip = "198.51.100.30"
    _run(_insert(ip, False, 200))
    _run(_rate_rows([("login", f"ip:{ip}", "25 hours", "25 hours")]))

    _um_ciclo(monkeypatch)

    assert _run(_exists(ip, False)) is True                                # retenção de auditoria desligada
    assert _run(_rate_rows(bucket="login", ids=[f"ip:{ip}"])) == set()    # o IP real some mesmo assim
    monkeypatch.setenv("LOGIN_EVENTS_RETENTION_DAYS", "90")
    _run(ret.purge_old_login_events())  # não deixa falha antiga para o test_purga_so_falhas_antigas


@pytest.mark.parametrize("quebra", ["rate_limits", "login_events"])
def test_falha_de_uma_purga_nao_desfaz_a_outra(monkeypatch, quebra):
    monkeypatch.setenv("LOGIN_EVENTS_RETENTION_DAYS", "90")
    ip = "198.51.100.31"
    _run(_insert(ip, False, 200))
    _run(_rate_rows([("login", f"ip:{ip}", "25 hours", "25 hours")]))
    if quebra == "rate_limits":
        monkeypatch.setattr(ret, "RATE_LIMITS_RETENTION", "nao-e-intervalo")  # o delete falha no banco
    else:
        async def boom():
            raise RuntimeError("falha injetada")
        monkeypatch.setattr(ret, "purge_old_login_events", boom)

    _um_ciclo(monkeypatch)

    sobrou_evento = _run(_exists(ip, False))
    sobrou_contador = _run(_rate_rows(bucket="login", ids=[f"ip:{ip}"])) == {f"ip:{ip}"}
    assert (sobrou_evento, sobrou_contador) == ((False, True) if quebra == "rate_limits" else (True, False))
    monkeypatch.undo()
    _run(ret.purge_old_login_events())  # não deixa falha antiga para o test_purga_so_falhas_antigas
