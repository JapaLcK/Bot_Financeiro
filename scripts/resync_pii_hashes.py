"""
scripts/resync_pii_hashes.py — Reconcilia hashes de PII desatualizados em auth_accounts.

Contexto: a rota PATCH /settings/{user_id}/security/contact atualizava `email`
e `phone_e164` em claro SEM recalcular `email_hash`/`phone_hash`. Como o login
usa `email_hash` e o auto-link do WhatsApp usa `phone_hash`, contas que
alteraram contato pelo site ficaram com hash apontando pro valor antigo — o
bot nunca reconhece o número novo ("no_match" eterno).

Diferença pro migrate_pii_to_encrypted.py: aquele só PREENCHE hashes nulos
(`needs_phone = phone_e164 and not phone_hash`); este RECALCULA quando o hash
existente não bate com o valor em claro atual.

Idempotente: rodar 2x não altera nada na segunda passada.

Uso:
  python -m scripts.resync_pii_hashes              # aplica
  python -m scripts.resync_pii_hashes --dry-run    # só relata divergências
"""
from __future__ import annotations

import argparse
import sys

from config.env import load_app_env

load_app_env()  # carrega .env antes de qualquer get_conn / Fernet

from core.crypto import encrypt_pii_optional, hash_pii_optional  # noqa: E402
from db.connection import get_conn  # noqa: E402
from utils_phone import normalize_phone_e164  # noqa: E402


def resync_auth_accounts(*, dry_run: bool) -> tuple[int, int, int]:
    scanned = fixed = errors = 0
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select id, user_id, email, phone_e164, display_name,
                   email_hash, phone_hash, display_name_enc
              from auth_accounts
             where email is not null or phone_e164 is not null
            """
        )
        rows = cur.fetchall()
        scanned = len(rows)

        for row in rows:
            sets: list[str] = []
            params: list = []

            try:
                email = (row["email"] or "").strip().lower() or None
                if email:
                    expected_email_hash = hash_pii_optional(email, kind="email")
                    if row["email_hash"] != expected_email_hash:
                        sets.extend(["email_hash = %s", "email_enc = %s"])
                        params.extend([expected_email_hash, encrypt_pii_optional(email)])

                phone = row["phone_e164"]
                if phone:
                    try:
                        phone = normalize_phone_e164(phone)
                    except ValueError:
                        pass  # mantém como está; hasheia o valor gravado
                    expected_phone_hash = hash_pii_optional(phone, kind="phone")
                    if row["phone_hash"] != expected_phone_hash:
                        sets.extend(["phone_hash = %s", "phone_enc = %s"])
                        params.extend([expected_phone_hash, encrypt_pii_optional(phone)])

                if not sets:
                    continue

                print(
                    f"  auth_accounts id={row['id']} user_id={row['user_id']}: "
                    f"{'corrigiria' if dry_run else 'corrigindo'} {', '.join(s.split(' =')[0] for s in sets)}"
                )
                params.append(row["id"])
                cur.execute(
                    f"update auth_accounts set {', '.join(sets)} where id = %s",
                    tuple(params),
                )
                fixed += 1
            except Exception as exc:
                errors += 1
                print(f"  [auth_accounts:{row['id']}] erro: {exc}", file=sys.stderr)

        if dry_run:
            conn.rollback()
        else:
            conn.commit()

    return scanned, fixed, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Relata as divergências sem persistir nada (rollback no fim).",
    )
    args = parser.parse_args()

    print()
    print("=" * 70)
    print(f"  Resync de hashes PII (auth_accounts)  {'(DRY RUN)' if args.dry_run else ''}")
    print("=" * 70)
    print()

    scanned, fixed, errors = resync_auth_accounts(dry_run=args.dry_run)

    print()
    print("-" * 70)
    print(f"  Escaneadas: {scanned}    Corrigidas: {fixed}    Erros: {errors}")
    if args.dry_run:
        print("  (DRY RUN — nenhuma alteração persistida)")
    print()

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
