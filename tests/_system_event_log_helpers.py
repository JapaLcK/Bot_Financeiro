"""Fixtures e helpers dos testes de `core/system_event_log.py`.

Sem prefixo `test_` de propósito, como `tests/_billing_grants_helpers.py`: o
pytest não coleta este arquivo. Ele existe porque o assunto mora em DOIS
arquivos — `test_system_event_log_teto.py` (cronômetro, lock, reentrância) e
`test_system_event_log_config.py` (o helper `statement_timeout_options`) — e
duas cópias do mesmo `tabela_travada`/`_limpa` é como dois arquivos passam a
medir coisas diferentes achando que medem a mesma (CLAUDE.md §0.7).

As fixtures moram AQUI e não num `conftest.py`, pelo mesmo motivo de
`tests/_corpo_json_helpers.py:9-12`: `sem_env_de_teto` é `autouse`, e num
conftest ela passaria a apagar a env na suíte inteira. Importada por nome, ela
vale só nos arquivos que a importam.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import threading

import psycopg
import pytest

import core.observability as observability
from db.connection import get_conn

# Valor exato usado na limpeza — nunca `like`, nunca prefixo (CLAUDE.md §0:
# limpe só o que você criou).
EVENTO_INSERT = "teste_teto_insert"
EVENTO_LEITURA = "teste_teto_leitura"
EVENTO_LIVRE = "teste_teto_livre"
EVENTO_DEFAULT = "teste_teto_default"
EVENTO_OPTIONS = "teste_teto_options"

TETO_PADRAO_OPTIONS = "-c statement_timeout=2000ms"


@pytest.fixture(autouse=True)
def tabelas_admin():
    """`system_event_logs` não nasce de `db/schema.py` — é criada
    preguiçosamente por `core/admin_dashboard.py`. Sem ela o INSERT falha em
    silêncio e os controles positivos não mediriam nada (mesmo idioma de
    `tests/_billing_grants_helpers.py:29-30` e `tests/test_of_health.py:372`)."""
    from core.admin_dashboard import ensure_admin_tables
    asyncio.run(ensure_admin_tables())


@pytest.fixture(autouse=True)
def sem_env_de_teto(monkeypatch):
    """A env é o knob sob teste: quem quiser um valor, faz `setenv` explícito."""
    monkeypatch.delenv("SYSTEM_EVENT_LOG_TIMEOUT_MS", raising=False)


@pytest.fixture
def sem_dashboard_handler():
    """Destaca os `_DashboardHandler` do root logger e repõe no fim.

    É O CONFUNDIDOR dos testes de cronômetro: com a tabela travada, um
    `logger.warning()` de qualquer módulo da suíte paga a mesma espera, e o
    cronômetro passaria a medir o handler em vez da função sob teste.
    `test_destacar_o_handler_e_o_que_faz_o_cronometro_medir_a_funcao` mede que o
    destacamento de fato funciona."""
    root = logging.getLogger()
    presos = [h for h in root.handlers
              if isinstance(h, observability._DashboardHandler)]
    for h in presos:
        root.removeHandler(h)
    try:
        yield
    finally:
        for h in presos:
            root.addHandler(h)


@contextlib.contextmanager
def tabela_travada(segundos: float):
    """`system_event_logs` em `access exclusive` por `segundos`, solta por Timer.

    Conexão DEDICADA de propósito: com `get_conn` o lock prenderia uma vaga do
    pool que o resto do teste (e o cleanup) precisa. `lock_timeout` porque o
    próprio `lock table` pendura sem limite se outra transação estiver segurando
    a tabela, e o Timer que solta só é armado DEPOIS dele: sem teto aqui, a
    suíte trava em vez de falhar. `closing` porque o `lock table` pode estourar
    esse `lock_timeout` (55P03) ANTES de haver Timer a cancelar — sem ele a
    conexão vazava até o GC nesse caminho."""
    with contextlib.closing(
        psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=5,
                        options="-c lock_timeout=5000ms")
    ) as conn:
        with conn.cursor() as cur:
            cur.execute("lock table system_event_logs in access exclusive mode")
        solta = threading.Timer(segundos, conn.rollback)
        solta.start()
        try:
            yield
        finally:
            solta.cancel()
            solta.join()
            conn.rollback()


def _linhas(event_type: str) -> int:
    with get_conn() as c:
        with c.cursor() as cur:
            cur.execute("select count(*) as n from system_event_logs where event_type = %s",
                        (event_type,))
            return int((cur.fetchone() or {}).get("n") or 0)


def _limpa(event_type: str) -> None:
    with get_conn() as c:
        c.execute("delete from system_event_logs where event_type = %s", (event_type,))
        c.commit()
