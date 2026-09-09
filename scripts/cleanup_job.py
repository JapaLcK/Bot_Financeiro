"""
Job de manutenção: poda as três tabelas que só crescem.

Execução MANUAL (e `--dry-run`): roda uma vez, fecha recursos e encerra.

Em produção quem agenda a poda NÃO é cron: é a tarefa de fundo do próprio app
(`core/services/table_cleanup.py`, registrada no lifespan). A lógica mora lá —
aqui restou só a casca de linha de comando, para podar na hora sem esperar o
tick e para ver o que seria podado.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config.env import load_app_env
# `log_event` mora no serviço (uma fonte, dois consumidores): o laço do app
# também precisa registrar a falha em `system_event_logs`, não só este script.
# Sem underscore de propósito — é contrato entre módulos, não detalhe interno.
# (O `_log_event` de `scripts/account_deletion_job.py` continua privado: tem um
# consumidor só, o próprio arquivo.)
from core.services.table_cleanup import log_event, run_table_cleanup


def run(*, dry_run: bool = False) -> int:
    started_at = datetime.now(timezone.utc).isoformat()
    print(f"[cleanup_job] iniciado em {started_at}; dry_run={dry_run}", flush=True)

    summary = run_table_cleanup(dry_run=dry_run)
    log_event(
        "error" if summary["errors"] else "info",
        f"Job de limpeza concluído: {summary}",
        summary,
    )
    print(f"[cleanup_job] concluído: {summary}", flush=True)

    return 1 if summary["errors"] else 0


def main(argv: list[str] | None = None) -> int:
    load_app_env()

    parser = argparse.ArgumentParser(description="Poda tabelas de tokens/challenges/pendências expirados.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Lista as limpezas sem apagar nada.",
    )
    args = parser.parse_args(argv)

    if not (os.getenv("DATABASE_URL") or "").strip():
        print("[cleanup_job] DATABASE_URL não configurado.", file=sys.stderr)
        return 2

    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
