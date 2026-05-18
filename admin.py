"""
admin.py — CLI administrativa do PigBank AI.

Toda leitura de PII via este CLI passa por core/crypto.decrypt_pii com
actor=cli:<user> — acessos ficam registrados em pii_access_log e podem
ser consultados via `admin.py audit`.

Comandos:
  inspect <user_id> [--reason=...]   Mostra dados decifrados de um user
  audit  [filtros]                   Lista entries do pii_access_log
  users  [--limit=N]                 Lista users (sem decifrar PII)
  purge  --level=info [--days=N]     Apaga em massa de system_event_logs

Exemplos:
  python admin.py users
  python admin.py inspect 88648360 --reason="ticket #42 — não recebe email"
  python admin.py audit --actor=admin --days=1
  python admin.py audit --user=88648360 --field=email
  python admin.py purge --level=info --days=7 -y
"""
from __future__ import annotations

import argparse
import os
import sys

from config.env import load_app_env

load_app_env()  # carrega .env antes de qualquer get_conn / Fernet

from core.crypto import PiiAccessContext, decrypt_pii_optional  # noqa: E402
from db.connection import get_conn  # noqa: E402


def _actor() -> str:
    """Identifica o operador via shell ($USER) — vai pro audit log."""
    user = (os.getenv("USER") or os.getenv("LOGNAME") or "unknown").strip()
    return f"cli:{user}"


# ──────────────────────────────────────────────────────────────────────────────
# inspect
# ──────────────────────────────────────────────────────────────────────────────

def cmd_inspect(args) -> int:
    user_id = int(args.user_id)
    reason = (args.reason or "").strip() or "(sem motivo)"
    actor = _actor()
    purpose = f"cli_inspect:{reason[:80]}"

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select user_id, email, email_enc, phone_e164, phone_enc,
                   display_name, display_name_enc, plan, plan_expires_at,
                   created_at, last_activity_at, phone_status, stripe_customer_id
            from auth_accounts where user_id = %s
            """,
            (user_id,),
        )
        row = cur.fetchone()

    if not row:
        print(f"User {user_id}: sem auth_account.", file=sys.stderr)
        return 1

    ctx = lambda f: PiiAccessContext(  # noqa: E731
        purpose=purpose, actor=actor, subject_user_id=user_id, field=f
    )

    email = (
        decrypt_pii_optional(row.get("email_enc"), ctx=ctx("email"))
        if row.get("email_enc")
        else row.get("email")
    )
    phone = (
        decrypt_pii_optional(row.get("phone_enc"), ctx=ctx("phone"))
        if row.get("phone_enc")
        else row.get("phone_e164")
    )
    name = (
        decrypt_pii_optional(row.get("display_name_enc"), ctx=ctx("name"))
        if row.get("display_name_enc")
        else row.get("display_name")
    )

    plan_until = (
        f" até {str(row['plan_expires_at'])[:10]}"
        if row.get("plan_expires_at")
        else ""
    )
    last_act = (
        str(row["last_activity_at"])[:19] if row.get("last_activity_at") else "nunca"
    )

    bar = "━" * 60
    print(
        f"\n{bar}\n"
        f"  User {user_id}\n"
        f"{bar}\n"
        f"  Nome:      {name or '(sem nome)'}\n"
        f"  Email:     {email}\n"
        f"  Telefone:  {phone or '(sem phone)'}\n"
        f"  Plano:     {row['plan']}{plan_until}\n"
        f"  Status WA: {row['phone_status']}\n"
        f"  Criado:    {str(row['created_at'])[:19]}\n"
        f"  Atividade: {last_act}\n"
        f"  Stripe:    {row.get('stripe_customer_id') or '—'}\n"
        f"{bar}\n"
    )

    # Identidades vinculadas (Discord, WhatsApp, email)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select provider, external_id, external_id_enc, created_at
            from user_identities where user_id = %s
            order by created_at
            """,
            (user_id,),
        )
        identities = cur.fetchall() or []

    if identities:
        print("Identidades:")
        for r in identities:
            ext = (
                decrypt_pii_optional(
                    r.get("external_id_enc"), ctx=ctx(f"{r['provider']}_id")
                )
                if r.get("external_id_enc")
                else r.get("external_id")
            )
            print(
                f"  • {r['provider']:<10}  {ext:<32}  (desde {str(r['created_at'])[:10]})"
            )
        print()

    n_audited = 3 + sum(1 for r in identities if r.get("external_id_enc"))
    print(f"Audit:  actor={actor}  motivo={reason!r}")
    print(f"        {n_audited} entries registradas em pii_access_log\n")
    return 0


# ──────────────────────────────────────────────────────────────────────────────
# audit
# ──────────────────────────────────────────────────────────────────────────────

def cmd_audit(args) -> int:
    actor = args.actor
    user = args.user
    field = args.field
    days = max(1, int(args.days))
    limit = max(1, min(int(args.limit), 1000))

    clauses = [f"created_at > now() - interval '{days} days'"]
    params: list = []
    if actor:
        clauses.append("actor ILIKE %s")
        params.append(f"%{actor}%")
    if user is not None:
        clauses.append("subject_user_id = %s")
        params.append(int(user))
    if field:
        clauses.append("field = %s")
        params.append(field)
    where = " AND ".join(clauses)

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"select count(*) as n from pii_access_log where {where}", tuple(params)
        )
        total = int(cur.fetchone()["n"])

        cur.execute(
            f"""
            select created_at, actor, subject_user_id, field, purpose
            from pii_access_log
            where {where}
            order by created_at desc
            limit %s
            """,
            tuple([*params, limit]),
        )
        rows = cur.fetchall()

    if not rows:
        print(f"\nNenhum acesso PII encontrado nos últimos {days} dias.\n")
        return 0

    print(f"\n{total} acessos PII nos últimos {days}d (mostrando {len(rows)}):\n")
    header = (
        f"  {'QUANDO':<22}  {'ATOR':<26}  {'USER':<12}  {'CAMPO':<14}  PROPÓSITO"
    )
    print(header)
    print(f"  {'-'*22}  {'-'*26}  {'-'*12}  {'-'*14}  {'-'*40}")
    for r in rows:
        when = str(r["created_at"])[:19]
        uid = "—" if r["subject_user_id"] is None else str(r["subject_user_id"])
        print(
            f"  {when:<22}  {(r['actor'] or '?'):<26}  {uid:<12}  {(r['field'] or '?'):<14}  {r['purpose']}"
        )
    print()
    return 0


# ──────────────────────────────────────────────────────────────────────────────
# users
# ──────────────────────────────────────────────────────────────────────────────

def cmd_users(args) -> int:
    limit = max(1, min(int(args.limit), 500))
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select user_id, plan, plan_expires_at, last_activity_at, created_at, phone_status
            from auth_accounts
            order by created_at desc
            limit %s
            """,
            (limit,),
        )
        rows = cur.fetchall()

    if not rows:
        print("Sem users.")
        return 0

    print(f"\n{len(rows)} users (mais recentes primeiro):\n")
    print(
        f"  {'USER_ID':<14}  {'PLANO':<8}  {'STATUS WA':<14}  {'CRIADO':<12}  ATIVIDADE"
    )
    print(f"  {'-'*14}  {'-'*8}  {'-'*14}  {'-'*12}  {'-'*12}")
    for r in rows:
        criado = str(r["created_at"])[:10]
        ativ = (
            str(r["last_activity_at"])[:10] if r.get("last_activity_at") else "—"
        )
        print(
            f"  {r['user_id']:<14}  {r['plan']:<8}  {r['phone_status']:<14}  {criado:<12}  {ativ}"
        )
    print(f"\n→ Use `python admin.py inspect <user_id> --reason='...'` pra ver dados decifrados.\n")
    return 0


# ──────────────────────────────────────────────────────────────────────────────
# purge
# ──────────────────────────────────────────────────────────────────────────────

def cmd_purge(args) -> int:
    level = args.level
    days = args.days

    clauses: list[str] = []
    params: list = []
    if level:
        clauses.append("level = %s")
        params.append(level)
    if days is not None:
        clauses.append(f"created_at < now() - interval '{int(days)} days'")

    if not clauses:
        print(
            "Use ao menos um filtro: --level=info|warning|error ou --days=N",
            file=sys.stderr,
        )
        return 2

    where = " AND ".join(clauses)

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"select count(*) as n from system_event_logs where {where}", tuple(params)
        )
        n = int(cur.fetchone()["n"])

    if not n:
        print("Nada a apagar com esse filtro.")
        return 0

    if not args.yes:
        ok = input(f"Apagar {n} rows de system_event_logs onde [{where}]? [y/N] ")
        if ok.strip().lower() != "y":
            print("Cancelado.")
            return 0

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"delete from system_event_logs where {where}", tuple(params))
        conn.commit()

    print(f"{n} rows apagadas.")
    return 0


# ──────────────────────────────────────────────────────────────────────────────
# Orchestrator
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    # inspect
    pi = sub.add_parser("inspect", help="Mostra dados decifrados de um user (auditado)")
    pi.add_argument("user_id", type=int)
    pi.add_argument(
        "--reason",
        "-r",
        help="Motivo do acesso (vai pro audit log). Forte recomendação preencher.",
    )
    pi.set_defaults(func=cmd_inspect)

    # audit
    pa = sub.add_parser("audit", help="Lista entries do pii_access_log")
    pa.add_argument("--actor", help="Filtrar por ator (LIKE %%X%%)")
    pa.add_argument("--user", type=int, help="Filtrar por user_id")
    pa.add_argument(
        "--field", help="Filtrar por campo (email|phone|name|discord_id|whatsapp_id|...)"
    )
    pa.add_argument("--days", type=int, default=7, help="Janela em dias (default: 7)")
    pa.add_argument("--limit", type=int, default=50, help="Max rows (default: 50)")
    pa.set_defaults(func=cmd_audit)

    # users
    pu = sub.add_parser("users", help="Lista users (sem decifrar PII)")
    pu.add_argument("--limit", type=int, default=50)
    pu.set_defaults(func=cmd_users)

    # purge
    pp = sub.add_parser("purge", help="Bulk delete em system_event_logs (equivalente ao 'Limpar' do painel)")
    pp.add_argument(
        "--level",
        choices=["info", "warning", "error"],
        help="Filtrar por level",
    )
    pp.add_argument(
        "--days", type=int, help="Apagar registros mais antigos que N dias"
    )
    pp.add_argument(
        "-y", "--yes", action="store_true", help="Não pedir confirmação"
    )
    pp.set_defaults(func=cmd_purge)

    args = p.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
