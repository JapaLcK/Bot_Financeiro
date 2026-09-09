"""
Job de manutenção: poda as três tabelas que só crescem.

Projetado para Railway Cron: executa uma vez, fecha recursos e encerra.

As três funções de limpeza já existiam e ninguém as chamava:
  core/refresh_tokens.py  cleanup_expired_refresh_tokens  → auth_refresh_tokens
  db/mfa.py               cleanup_expired_challenges      → mfa_login_challenges
  db/google_auth.py       cleanup_expired_pending_signups → pending_google_signups

Horário (railway.cleanup.toml): 04:00 UTC = 01:00 BRT. Duas horas antes do job
de exclusão de conta (06:00 UTC), então os dois nunca competem por conexão nem
por lock; e é o vale de tráfego do WhatsApp (madrugada no Brasil).
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


def _log_event(level: str, message: str, details: dict) -> None:
    try:
        from core.observability import log_system_event_sync

        log_system_event_sync(
            level,
            "cleanup_job",
            message,
            source="scripts.cleanup_job",
            details=details,
        )
    except Exception:
        pass


def _cleanups() -> list[tuple[str, object]]:
    from core.refresh_tokens import cleanup_expired_refresh_tokens
    from db.google_auth import cleanup_expired_pending_signups
    from db.mfa import cleanup_expired_challenges

    return [
        ("auth_refresh_tokens", cleanup_expired_refresh_tokens),
        ("mfa_login_challenges", cleanup_expired_challenges),
        ("pending_google_signups", cleanup_expired_pending_signups),
    ]


def run(*, dry_run: bool = False) -> int:
    started_at = datetime.now(timezone.utc).isoformat()
    print(f"[cleanup_job] iniciado em {started_at}; dry_run={dry_run}", flush=True)

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

    summary = {"removed": removed, "errors": errors, "dry_run": dry_run}
    _log_event(
        "error" if errors else "info",
        f"Job de limpeza concluído: {summary}",
        summary,
    )
    print(f"[cleanup_job] concluído: {summary}", flush=True)

    return 1 if errors else 0


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
