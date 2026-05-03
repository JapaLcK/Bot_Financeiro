"""
Job dedicado para concluir exclusões de conta vencidas.

Projetado para Railway Cron: executa uma vez, fecha recursos e encerra.
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


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _log_event(level: str, message: str, details: dict) -> None:
    try:
        from core.observability import log_system_event_sync

        log_system_event_sync(
            level,
            "account_deletion_job",
            message,
            source="scripts.account_deletion_job",
            details=details,
        )
    except Exception:
        pass


def _count_due_accounts() -> int:
    import db

    db.ensure_account_deletion_columns()
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select count(*) as total
            from auth_accounts
            where deletion_status = 'scheduled'
              and deletion_scheduled_for <= now()
            """
        )
        row = cur.fetchone()
    return int(row["total"] if row else 0)


def run(limit: int, *, dry_run: bool = False) -> int:
    import db
    from core.services.email_service import send_account_deletion_completed_email

    started_at = datetime.now(timezone.utc).isoformat()
    print(f"[account_deletion_job] iniciado em {started_at}; limit={limit}; dry_run={dry_run}", flush=True)

    if dry_run:
        total = _count_due_accounts()
        summary = {"due_accounts": total, "dry_run": True}
        _log_event("info", f"Dry-run do job de exclusão de contas: {summary}", summary)
        print(f"[account_deletion_job] dry-run concluído: {summary}", flush=True)
        return 0

    db.ensure_account_deletion_columns()
    results = db.process_due_account_deletions(limit=limit)

    deleted = 0
    email_sent = 0
    email_failed = 0
    errors: list[dict] = []

    for result in results:
        user_id = result.get("user_id")
        if result.get("error"):
            errors.append({"user_id": user_id, "error": result["error"]})
            continue

        if result.get("deleted"):
            deleted += 1

        email = (result.get("email") or "").strip().lower()
        if result.get("deleted") and email:
            if send_account_deletion_completed_email(email):
                email_sent += 1
            else:
                email_failed += 1

    summary = {
        "processed": len(results),
        "deleted": deleted,
        "email_sent": email_sent,
        "email_failed": email_failed,
        "errors": errors,
    }

    level = "error" if errors or email_failed else "info"
    _log_event(level, f"Job de exclusão de contas concluído: {summary}", summary)
    print(f"[account_deletion_job] concluído: {summary}", flush=True)

    return 1 if errors or email_failed else 0


def main(argv: list[str] | None = None) -> int:
    load_app_env()

    parser = argparse.ArgumentParser(description="Processa exclusões de conta vencidas.")
    parser.add_argument(
        "--limit",
        type=int,
        default=_env_int("ACCOUNT_DELETION_JOB_LIMIT", 50),
        help="Número máximo de contas processadas nesta execução.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Conta exclusões vencidas sem apagar dados nem enviar e-mail.",
    )
    args = parser.parse_args(argv)

    if not (os.getenv("DATABASE_URL") or "").strip():
        print("[account_deletion_job] DATABASE_URL não configurado.", file=sys.stderr)
        return 2

    return run(max(1, args.limit), dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
