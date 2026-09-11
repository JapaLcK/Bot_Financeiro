"""
core/services/table_cleanup.py — Poda das três tabelas que só crescem.

As três funções de limpeza já existiam e ninguém as chamava; o cron do Railway
que deveria chamá-las nunca foi criado no painel (e, desde 2026-08-28, serviço
novo não consegue mais optar por Config as Code). Por isso a poda passou a
rodar como tarefa de fundo do próprio app, no lifespan:

  core/refresh_tokens.py  cleanup_expired_refresh_tokens  → auth_refresh_tokens
  db/mfa.py               cleanup_expired_challenges      → mfa_login_challenges
  db/google_auth.py       cleanup_expired_pending_signups → pending_google_signups

Uma fonte de verdade, dois consumidores: `run_table_cleanup` é chamada pelo loop
daqui (`_table_cleanup` no lifespan) e por `scripts/cleanup_job.py` (execução
manual, `--dry-run`).

As três funções são SÍNCRONAS e usam `get_conn()` bloqueante — o loop as chama
por `asyncio.to_thread`, senão o event loop do app inteiro trava durante o
DELETE.

Horário: o laço roda em boot+122s e a cada TABLE_CLEANUP_INTERVAL_HOURS — 122 e não
120 porque o wrapper do lifespan dorme 2s antes do import tardio
(`frontend/finance_bot_websocket_custom.py:1893`) e só então entra no `sleep(120)`
daqui. Ou seja
NO HORÁRIO DO ÚLTIMO DEPLOY, e de novo a cada deploy. A propriedade das 04:00 UTC
que o `railway.cleanup.toml` apagado justificava ("duas horas antes do job de
exclusão, no vale de tráfego") foi ABANDONADA de propósito: ela já era falsa
quando escrita — o `_account_deletion_worker` roda de hora em hora dentro do app
e `auth_refresh_tokens` tem cascade por `user_id`, então DELETE nessa tabela já
acontece em horário arbitrário 24×/dia.

Config (env, opcional):
  - TABLE_CLEANUP_INTERVAL_HOURS (default '24') — 0 (ou negativo) desliga o job.

ponytail: tetos conhecidos, não implementados (P2/P3 do Tester) — sem
`lock_timeout` no DELETE, sem cancelar a thread do `to_thread` quando a task cai,
e sem validar a env (valor não-inteiro cai no default 24, negativo desliga como
0). Intervalo FRACIONÁRIO não sai de validação nenhuma: o `int()` de
`_interval_hours` (`:79`) é quem trunca, e quem pedir 0,5h troca esse `int` por
`float`. Validar a env é outro pedido — o de recusar lixo em vez de cair no 24
calado.

O teto do lock NÃO tem gatilho vindo daqui, e isso é a parte que importa: se a
poda travar segurando lock, o laço fica preso DENTRO do `to_thread` — o `while`
só relê `TABLE_CLEANUP_INTERVAL_HOURS` quando o `await` volta, então zerar a env
não desliga nada, e nenhum log sai (o `log_event` do fim da volta nunca é
alcançado). Não existe instrumento no app que informe que aconteceu: quem mostra
é o banco (`pg_stat_activity` / `pg_locks`), e a evidência de fora é a poda
parar de gravar o evento `cleanup_job` no painel.
"""
from __future__ import annotations

import asyncio
import os
import sys

from core.observability import get_logger

logger = get_logger(__name__)


def log_event(level: str, message: str, details: dict) -> None:
    """Registra em `system_event_logs` — é de lá que o painel lê.

    Uma fonte, dois consumidores: o laço daqui e `scripts/cleanup_job.py`.
    `logger.info` NÃO serve: o `_DashboardHandler` (`core/observability.py`) só
    espelha registro >= WARNING, então a falha da poda não chegava ao painel
    pelo caminho do app (o `rc=1` do script cobria só a execução manual)."""
    try:
        from core.observability import log_system_event_sync

        log_system_event_sync(
            level,
            "cleanup_job",
            message,
            source="core.services.table_cleanup",
            details=details,
        )
    except Exception:
        pass


def _interval_hours() -> int:
    try:
        return int(os.getenv("TABLE_CLEANUP_INTERVAL_HOURS", "24"))
    except (TypeError, ValueError):
        return 24


def _cleanups() -> list[tuple[str, object]]:
    from core.refresh_tokens import cleanup_expired_refresh_tokens
    from db.google_auth import cleanup_expired_pending_signups
    from db.mfa import cleanup_expired_challenges

    return [
        ("auth_refresh_tokens", cleanup_expired_refresh_tokens),
        ("mfa_login_challenges", cleanup_expired_challenges),
        ("pending_google_signups", cleanup_expired_pending_signups),
    ]


def run_table_cleanup(*, dry_run: bool = False) -> dict:
    """Poda as três tabelas. Síncrona; devolve {"removed", "errors", "dry_run"}."""
    removed: dict[str, int] = {}
    errors: list[dict] = []

    for table, fn in _cleanups():
        if dry_run:
            # ponytail: dry-run só lista o que seria podado. Contar as linhas
            # exigiria repetir o predicado de cada DELETE aqui (segunda fonte
            # de verdade, CLAUDE.md §0.7). Se a contagem for necessária, ela
            # nasce dentro de cada função de limpeza, não aqui.
            print(f"[cleanup_job] dry-run: {table} seria podada (nada apagado)", flush=True)
            continue
        try:
            n = int(fn() or 0)  # type: ignore[operator]
        except Exception as exc:
            # Uma tabela que falha não pode impedir a poda das outras duas.
            errors.append({"table": table, "error": repr(exc)})
            print(f"[cleanup_job] {table}: FALHOU — {exc}", file=sys.stderr, flush=True)
            continue
        removed[table] = n
        print(f"[cleanup_job] {table}: {n} linha(s) removida(s)", flush=True)

    return {"removed": removed, "errors": errors, "dry_run": dry_run}


async def run_table_cleanup_loop() -> None:
    """Loop perpétuo (asyncio task no lifespan). Roda a poda 1x/dia.

    Com TABLE_CLEANUP_INTERVAL_HOURS <= 0 a corrotina TERMINA (não fica
    dormindo). A condição é relida a cada volta, então intervalo zerado em
    tempo de execução também encerra em vez de virar laço quente.

    CLASSE CEGA — o valor da espera não é medido por teste nenhum. Os testes
    trocam `asyncio.sleep` por um no-op para não esperar os 120s + 24h, então
    trocar `espera = _interval_hours() * 3600` por `espera = 1` (poda a cada
    segundo, em produção) deixa os 10 de `tests/test_table_cleanup.py` +
    `tests/test_cleanup_job.py` VERDES — medido, 10 passed. Eles provam O QUE o
    laço faz por volta, nunca QUANDO. Um teste de relógio aqui seria cerimônia
    (mockar o `sleep` para conferir o argumento afirma a própria linha); quem
    confere é a leitura. Não leia "testes com controle de mutação" como se o
    intervalo estivesse coberto — só o `<= 0` do kill switch está (T4)."""
    espera = 120  # deixa o boot assentar antes do 1º tick
    while _interval_hours() > 0:
        await asyncio.sleep(espera)
        try:
            resumo = await asyncio.to_thread(run_table_cleanup)
            # `to_thread` também aqui: o INSERT de `log_event` é bloqueante
            # (ponytail do `_DashboardHandler`) e isto roda no event loop do app.
            await asyncio.to_thread(
                log_event,
                "error" if resumo["errors"] else "info",
                f"Poda de tabelas concluída: {resumo}",
                resumo,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - defensivo
            # `to_thread` aqui também, e pelo MESMO motivo do `log_event`:
            # `get_logger` deixa o `_DashboardHandler` no root logger, e o
            # `emit` dele chama `log_system_event_sync` → `psycopg.connect` +
            # INSERT na thread do caller. Um `logger.warning` daqui é o mesmo
            # INSERT bloqueante, dentro do event loop do app.
            await asyncio.to_thread(logger.warning, "[table_cleanup] erro: %s", exc)
        espera = _interval_hours() * 3600
