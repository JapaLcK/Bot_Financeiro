"""
scripts/migrate_pii_to_encrypted.py — Backfill das colunas cifradas de PII.

Lê cada tabela com PII em claro e popula as colunas `*_hash`/`*_enc` correspondentes
quando ainda estão NULL. Idempotente: rodar 2x não duplica nada.

Pré-requisitos:
  1. core/crypto.py instalado (já está)
  2. PII_ENCRYPTION_KEY e PII_HASH_PEPPER configuradas (.env)
     Gere com: python -m scripts.generate_pii_keys
  3. Schema atualizado (rode `python -c "from db import init_db; init_db()"` antes)

Uso:
  python -m scripts.migrate_pii_to_encrypted              # roda de verdade
  python -m scripts.migrate_pii_to_encrypted --dry-run    # só conta o que faria

Cobertura:
  - auth_accounts      → email, phone_e164, display_name
  - user_identities    → external_id (Discord ID, WhatsApp ID)
  - email_verification_codes → email, phone_e164, display_name (transitório)
  - pending_google_signups   → email, name_hint (transitório)
  - auth_identities    → email (snapshot OAuth)
  - auth_login_events  → email (snapshot audit, pode ter centenas de rows)
  - data_export_tokens → delivered_to_email (snapshot)

As colunas em claro NÃO são apagadas aqui — drop é fase separada (5).
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

from config.env import load_app_env

load_app_env()  # carrega .env antes de qualquer get_conn / Fernet

from core.crypto import (  # noqa: E402
    encrypt_pii_optional,
    hash_pii_optional,
)
from db.connection import get_conn  # noqa: E402


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class TableStats:
    table: str
    scanned: int = 0
    updated: int = 0
    skipped: int = 0  # já tinha cifragem
    errors: int = 0


def _commit_or_rollback(conn, *, dry_run: bool):
    if dry_run:
        conn.rollback()
    else:
        conn.commit()


# ──────────────────────────────────────────────────────────────────────────────
# auth_accounts
# ──────────────────────────────────────────────────────────────────────────────

def migrate_auth_accounts(*, dry_run: bool) -> TableStats:
    stats = TableStats(table="auth_accounts")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select id, email, phone_e164, display_name,
                   email_hash, phone_hash, display_name_enc
              from auth_accounts
            """
        )
        rows = cur.fetchall()
        stats.scanned = len(rows)

        for row in rows:
            needs_email = bool(row["email"]) and not row["email_hash"]
            needs_phone = bool(row["phone_e164"]) and not row["phone_hash"]
            needs_name = bool(row["display_name"]) and not row["display_name_enc"]

            if not (needs_email or needs_phone or needs_name):
                stats.skipped += 1
                continue

            try:
                sets = []
                params: list = []
                if needs_email:
                    sets.append("email_hash = %s")
                    sets.append("email_enc = %s")
                    params.extend([
                        hash_pii_optional(row["email"], kind="email"),
                        encrypt_pii_optional(row["email"]),
                    ])
                if needs_phone:
                    sets.append("phone_hash = %s")
                    sets.append("phone_enc = %s")
                    params.extend([
                        hash_pii_optional(row["phone_e164"], kind="phone"),
                        encrypt_pii_optional(row["phone_e164"]),
                    ])
                if needs_name:
                    sets.append("display_name_enc = %s")
                    params.append(encrypt_pii_optional(row["display_name"]))

                params.append(row["id"])
                cur.execute(
                    f"update auth_accounts set {', '.join(sets)} where id = %s",
                    tuple(params),
                )
                stats.updated += 1
            except Exception as exc:
                stats.errors += 1
                print(f"[auth_accounts:{row['id']}] erro: {exc}", file=sys.stderr)

        _commit_or_rollback(conn, dry_run=dry_run)
    return stats


# ──────────────────────────────────────────────────────────────────────────────
# user_identities
# ──────────────────────────────────────────────────────────────────────────────

def migrate_user_identities(*, dry_run: bool) -> TableStats:
    stats = TableStats(table="user_identities")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select provider, external_id, external_id_hash
              from user_identities
            """
        )
        rows = cur.fetchall()
        stats.scanned = len(rows)

        for row in rows:
            if row["external_id_hash"]:
                stats.skipped += 1
                continue
            try:
                cur.execute(
                    """
                    update user_identities
                       set external_id_hash = %s, external_id_enc = %s
                     where provider = %s and external_id = %s
                    """,
                    (
                        hash_pii_optional(row["external_id"], kind="external_id"),
                        encrypt_pii_optional(row["external_id"]),
                        row["provider"],
                        row["external_id"],
                    ),
                )
                stats.updated += 1
            except Exception as exc:
                stats.errors += 1
                print(
                    f"[user_identities:{row['provider']}/{row['external_id']}] erro: {exc}",
                    file=sys.stderr,
                )

        _commit_or_rollback(conn, dry_run=dry_run)
    return stats


# ──────────────────────────────────────────────────────────────────────────────
# email_verification_codes
# ──────────────────────────────────────────────────────────────────────────────

def migrate_email_verification_codes(*, dry_run: bool) -> TableStats:
    stats = TableStats(table="email_verification_codes")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select id, email, phone_e164, display_name,
                   email_hash, phone_hash, display_name_enc
              from email_verification_codes
            """
        )
        rows = cur.fetchall()
        stats.scanned = len(rows)

        for row in rows:
            needs_email = bool(row["email"]) and not row["email_hash"]
            needs_phone = bool(row["phone_e164"]) and not row["phone_hash"]
            needs_name = bool(row["display_name"]) and not row["display_name_enc"]

            if not (needs_email or needs_phone or needs_name):
                stats.skipped += 1
                continue

            try:
                sets = []
                params: list = []
                if needs_email:
                    sets.extend(["email_hash = %s", "email_enc = %s"])
                    params.extend([
                        hash_pii_optional(row["email"], kind="email"),
                        encrypt_pii_optional(row["email"]),
                    ])
                if needs_phone:
                    sets.extend(["phone_hash = %s", "phone_enc = %s"])
                    params.extend([
                        hash_pii_optional(row["phone_e164"], kind="phone"),
                        encrypt_pii_optional(row["phone_e164"]),
                    ])
                if needs_name:
                    sets.append("display_name_enc = %s")
                    params.append(encrypt_pii_optional(row["display_name"]))

                params.append(row["id"])
                cur.execute(
                    f"update email_verification_codes set {', '.join(sets)} where id = %s",
                    tuple(params),
                )
                stats.updated += 1
            except Exception as exc:
                stats.errors += 1
                print(f"[email_verification_codes:{row['id']}] erro: {exc}", file=sys.stderr)

        _commit_or_rollback(conn, dry_run=dry_run)
    return stats


# ──────────────────────────────────────────────────────────────────────────────
# pending_google_signups
# ──────────────────────────────────────────────────────────────────────────────

def migrate_pending_google_signups(*, dry_run: bool) -> TableStats:
    stats = TableStats(table="pending_google_signups")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select token, email, name_hint, email_hash, name_hint_enc
              from pending_google_signups
            """
        )
        rows = cur.fetchall()
        stats.scanned = len(rows)

        for row in rows:
            needs_email = bool(row["email"]) and not row["email_hash"]
            needs_name = bool(row["name_hint"]) and not row["name_hint_enc"]

            if not (needs_email or needs_name):
                stats.skipped += 1
                continue

            try:
                sets, params = [], []
                if needs_email:
                    sets.extend(["email_hash = %s", "email_enc = %s"])
                    params.extend([
                        hash_pii_optional(row["email"], kind="email"),
                        encrypt_pii_optional(row["email"]),
                    ])
                if needs_name:
                    sets.append("name_hint_enc = %s")
                    params.append(encrypt_pii_optional(row["name_hint"]))

                params.append(row["token"])
                cur.execute(
                    f"update pending_google_signups set {', '.join(sets)} where token = %s",
                    tuple(params),
                )
                stats.updated += 1
            except Exception as exc:
                stats.errors += 1
                print(f"[pending_google_signups:{row['token'][:8]}...] erro: {exc}", file=sys.stderr)

        _commit_or_rollback(conn, dry_run=dry_run)
    return stats


# ──────────────────────────────────────────────────────────────────────────────
# auth_identities (Google OAuth) — só email, sem hash
# ──────────────────────────────────────────────────────────────────────────────

def migrate_auth_identities(*, dry_run: bool) -> TableStats:
    stats = TableStats(table="auth_identities")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select id, email, email_enc from auth_identities where email is not null"
        )
        rows = cur.fetchall()
        stats.scanned = len(rows)

        for row in rows:
            if row["email_enc"]:
                stats.skipped += 1
                continue
            try:
                cur.execute(
                    "update auth_identities set email_enc = %s where id = %s",
                    (encrypt_pii_optional(row["email"]), row["id"]),
                )
                stats.updated += 1
            except Exception as exc:
                stats.errors += 1
                print(f"[auth_identities:{row['id']}] erro: {exc}", file=sys.stderr)

        _commit_or_rollback(conn, dry_run=dry_run)
    return stats


# ──────────────────────────────────────────────────────────────────────────────
# auth_login_events — só email, sem hash. Pode ter MUITAS rows.
# ──────────────────────────────────────────────────────────────────────────────

def migrate_auth_login_events(*, dry_run: bool, batch: int = 500) -> TableStats:
    stats = TableStats(table="auth_login_events")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) as n from auth_login_events where email is not null and email_enc is null")
        pending = int(cur.fetchone()["n"])
        stats.scanned = pending

        if pending == 0:
            _commit_or_rollback(conn, dry_run=dry_run)
            return stats

        while True:
            cur.execute(
                """
                select id, email from auth_login_events
                 where email is not null and email_enc is null
                 limit %s
                """,
                (batch,),
            )
            rows = cur.fetchall()
            if not rows:
                break

            for row in rows:
                try:
                    cur.execute(
                        "update auth_login_events set email_enc = %s where id = %s",
                        (encrypt_pii_optional(row["email"]), row["id"]),
                    )
                    stats.updated += 1
                except Exception as exc:
                    stats.errors += 1
                    print(f"[auth_login_events:{row['id']}] erro: {exc}", file=sys.stderr)

            if dry_run:
                # em dry-run, não persistimos e saímos do loop pra não fazer
                # contagem inflada (a próxima iteração veria as mesmas rows)
                break

            conn.commit()

        _commit_or_rollback(conn, dry_run=dry_run)
    return stats


# ──────────────────────────────────────────────────────────────────────────────
# data_export_tokens
# ──────────────────────────────────────────────────────────────────────────────

def migrate_data_export_tokens(*, dry_run: bool) -> TableStats:
    stats = TableStats(table="data_export_tokens")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select token, delivered_to_email, delivered_to_email_enc
              from data_export_tokens
             where delivered_to_email is not null
            """
        )
        rows = cur.fetchall()
        stats.scanned = len(rows)

        for row in rows:
            if row["delivered_to_email_enc"]:
                stats.skipped += 1
                continue
            try:
                cur.execute(
                    "update data_export_tokens set delivered_to_email_enc = %s where token = %s",
                    (encrypt_pii_optional(row["delivered_to_email"]), row["token"]),
                )
                stats.updated += 1
            except Exception as exc:
                stats.errors += 1
                print(f"[data_export_tokens:{row['token'][:8]}...] erro: {exc}", file=sys.stderr)

        _commit_or_rollback(conn, dry_run=dry_run)
    return stats


# ──────────────────────────────────────────────────────────────────────────────
# Orchestrator
# ──────────────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Conta o que seria migrado sem persistir nada (rollback no fim).",
    )
    args = parser.parse_args()

    print()
    print("=" * 70)
    print(f"  Migração PII → cifrado  {'(DRY RUN)' if args.dry_run else ''}")
    print("=" * 70)
    print()

    runners = [
        migrate_auth_accounts,
        migrate_user_identities,
        migrate_email_verification_codes,
        migrate_pending_google_signups,
        migrate_auth_identities,
        migrate_auth_login_events,
        migrate_data_export_tokens,
    ]

    total_updated = 0
    total_errors = 0
    for fn in runners:
        stats = fn(dry_run=args.dry_run)
        print(
            f"  {stats.table:<32}  "
            f"scan={stats.scanned:<5}  "
            f"upd={stats.updated:<5}  "
            f"skip={stats.skipped:<5}  "
            f"err={stats.errors}"
        )
        total_updated += stats.updated
        total_errors += stats.errors

    print()
    print("-" * 70)
    print(f"  Total atualizadas: {total_updated}    Erros: {total_errors}")
    if args.dry_run:
        print("  (DRY RUN — nenhuma alteração persistida)")
    print()

    return 1 if total_errors else 0


if __name__ == "__main__":
    sys.exit(main())
