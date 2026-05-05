"""
db/privacy.py — Exportação e exclusão segura de dados do usuário.
"""
from __future__ import annotations

import csv
import io
import json
import secrets
import zipfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from .connection import get_conn
from .users import _check_password


def verify_user_password(user_id: int, password: str) -> bool:
    """Confirma que `password` corresponde ao hash atual da conta do usuário."""
    if not password:
        return False
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select password_hash from auth_accounts where user_id = %s limit 1",
            (user_id,),
        )
        row = cur.fetchone()
    if not row or not row.get("password_hash"):
        return False
    return _check_password(password, row["password_hash"])


def get_user_email(user_id: int) -> str | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select email from auth_accounts where user_id = %s limit 1",
            (user_id,),
        )
        row = cur.fetchone()
    return (row or {}).get("email")


def create_data_export_token(
    user_id: int,
    *,
    minutes_valid: int = 15,
    request_ip: str | None = None,
    request_user_agent: str | None = None,
    delivered_to_email: str | None = None,
) -> tuple[str, datetime]:
    """Cria um token de uso único para baixar a exportação completa.

    Retorna (token, expires_at). O token é opaco (urlsafe, ~43 chars).
    """
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=minutes_valid)

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            insert into data_export_tokens
              (token, user_id, expires_at, request_ip, request_user_agent, delivered_to_email)
            values (%s, %s, %s, %s, %s, %s)
            """,
            (token, user_id, expires_at, request_ip, request_user_agent, delivered_to_email),
        )
        conn.commit()

    return token, expires_at


def consume_data_export_token(token: str) -> int | None:
    """Valida e marca o token como usado em uma única transação atômica.

    Retorna o `user_id` associado se o token era válido (existe, não expirou
    e não foi usado). Retorna `None` em qualquer outro caso.
    """
    if not token:
        return None
    now = datetime.now(timezone.utc)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            update data_export_tokens
            set used_at = %s
            where token = %s
              and used_at is null
              and expires_at > %s
            returning user_id
            """,
            (now, token, now),
        )
        row = cur.fetchone()
        conn.commit()
    if not row:
        return None
    return int(row["user_id"])


def has_recent_export_request(user_id: int, within_minutes: int = 60) -> bool:
    """True se o usuário já solicitou um export nos últimos N minutos.

    Usado como cooldown adicional ao rate-limit por IP, pra evitar que o
    mesmo usuário gere múltiplos links válidos simultaneamente.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=within_minutes)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select 1
            from data_export_tokens
            where user_id = %s
              and created_at >= %s
              and used_at is null
              and expires_at > now()
            limit 1
            """,
            (user_id, cutoff),
        )
        row = cur.fetchone()
    return bool(row)


class PrivacyJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, UUID):
            return str(obj)
        if isinstance(obj, (bytes, bytearray, memoryview)):
            return bytes(obj).hex()
        if hasattr(obj, "isoformat"):
            return obj.isoformat()
        return super().default(obj)


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, cls=PrivacyJSONEncoder, ensure_ascii=False))


def _table_exists(cur, table: str) -> bool:
    cur.execute("select to_regclass(%s) is not null as exists", (table,))
    row = cur.fetchone()
    return bool(row and row["exists"])


def _column_exists(cur, table: str, column: str) -> bool:
    cur.execute(
        """
        select exists (
          select 1
          from information_schema.columns
          where table_schema = 'public'
            and table_name = %s
            and column_name = %s
        ) as exists
        """,
        (table, column),
    )
    row = cur.fetchone()
    return bool(row and row["exists"])


def ensure_account_deletion_columns() -> None:
    statements = [
        "alter table auth_accounts add column if not exists deletion_requested_at timestamptz",
        "alter table auth_accounts add column if not exists deletion_scheduled_for timestamptz",
        "alter table auth_accounts add column if not exists deletion_status text",
        "alter table auth_accounts add column if not exists deletion_processing_started_at timestamptz",
        """
        create index if not exists idx_auth_accounts_deletion_due
          on auth_accounts (deletion_scheduled_for)
          where deletion_status = 'scheduled'
        """,
        """
        create index if not exists idx_auth_accounts_deletion_processing
          on auth_accounts (deletion_processing_started_at)
          where deletion_status = 'processing'
        """,
    ]
    with get_conn() as conn:
        with conn.cursor() as cur:
            for stmt in statements:
                cur.execute(stmt)
        conn.commit()


def is_account_scheduled_for_deletion(user_id: int) -> dict | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select deletion_requested_at, deletion_scheduled_for, deletion_status
            from auth_accounts
            where user_id = %s
              and deletion_status in ('scheduled', 'processing')
              and deletion_scheduled_for is not null
            limit 1
            """,
            (user_id,),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def schedule_account_deletion(user_id: int, password: str, grace_days: int = 7) -> dict:
    if not password:
        raise ValueError("Informe sua senha para confirmar a exclusão.")

    now = datetime.now(timezone.utc)
    scheduled_for = now + timedelta(days=grace_days)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, email, password_hash, deletion_status, deletion_scheduled_for
                from auth_accounts
                where user_id = %s
                limit 1
                """,
                (user_id,),
            )
            account = cur.fetchone()
            if not account:
                raise LookupError("Conta de login não encontrada.")
            if not _check_password(password, account["password_hash"]):
                raise PermissionError("Senha incorreta.")

            if account.get("deletion_status") == "scheduled" and account.get("deletion_scheduled_for"):
                scheduled_for = account["deletion_scheduled_for"]
            else:
                cur.execute(
                    """
                    update auth_accounts
                    set deletion_status = 'scheduled',
                        deletion_requested_at = %s,
                        deletion_scheduled_for = %s,
                        deletion_processing_started_at = null
                    where user_id = %s
                    """,
                    (now, scheduled_for, user_id),
                )

            # Reduz a janela de uso de tokens de uso único. Cookies JWT antigos
            # também são bloqueados pelos guards do backend.
            for table in ("dashboard_sessions", "link_codes", "platform_onboarding_tokens", "password_reset_tokens"):
                if _table_exists(cur, table):
                    cur.execute(f"delete from {table} where user_id = %s", (user_id,))

        conn.commit()

    return {
        "user_id": user_id,
        "status": "scheduled",
        "deletion_scheduled_for": scheduled_for,
        "grace_days": grace_days,
    }


def _fetch_rows(cur, name: str, sql: str, params: tuple) -> tuple[str, list[dict]]:
    cur.execute(sql, params)
    return name, [dict(row) for row in cur.fetchall()]


def build_user_export_zip(user_id: int) -> bytes:
    datasets: dict[str, list[dict]] = {}

    with get_conn() as conn, conn.cursor() as cur:
        queries = [
            ("usuario", "select * from users where id = %s", (user_id,)),
            (
                "conta_login",
                """
                select id, user_id, email, phone_e164, phone_status, phone_confirmed_at,
                       whatsapp_verified_at, plan, plan_expires_at, created_at,
                       stripe_customer_id, engagement_opt_out, last_activity_at,
                       last_tip_sent_at, tip_email_opt_out, last_insight_sent_at,
                       insight_email_opt_out, whatsapp_updates_opt_out,
                       last_reengagement_sent_at, deletion_requested_at,
                       deletion_scheduled_for, deletion_status,
                       deletion_processing_started_at
                from auth_accounts
                where user_id = %s
                """,
                (user_id,),
            ),
            ("identidades", "select * from user_identities where user_id = %s", (user_id,)),
            ("contas", "select * from accounts where user_id = %s", (user_id,)),
            ("lancamentos", "select * from launches where user_id = %s", (user_id,)),
            ("orcamentos", "select * from category_budgets where user_id = %s", (user_id,)),
            ("regras_categorias", "select * from user_category_rules where user_id = %s", (user_id,)),
            ("gatilhos_categorias", "select * from user_category_triggers where user_id = %s", (user_id,)),
            ("candidatos_gatilhos_categorias", "select * from user_trigger_candidates where user_id = %s", (user_id,)),
            ("feedback_categorias", "select * from user_category_feedback where user_id = %s", (user_id,)),
            ("acoes_pendentes", "select * from pending_actions where user_id = %s", (user_id,)),
            ("caixinhas", "select * from pockets where user_id = %s", (user_id,)),
            ("investimentos", "select * from investments where user_id = %s", (user_id,)),
            ("lotes_investimentos", "select * from investment_lots where user_id = %s", (user_id,)),
            ("cartoes", "select * from credit_cards where user_id = %s", (user_id,)),
            ("faturas_cartao", "select * from credit_bills where user_id = %s", (user_id,)),
            ("transacoes_cartao", "select * from credit_transactions where user_id = %s", (user_id,)),
            ("preferencias_resumo_diario", "select * from daily_report_prefs where user_id = %s", (user_id,)),
            ("importacoes_ofx", "select * from ofx_imports where user_id = %s", (user_id,)),
            ("sessoes_dashboard", "select code, user_id, expires_at, created_at from dashboard_sessions where user_id = %s", (user_id,)),
            (
                "conexoes_open_finance",
                "select * from open_finance_connections where user_id = %s",
                (user_id,),
            ),
            (
                "contas_open_finance",
                """
                select a.*
                from open_finance_accounts a
                join open_finance_connections c on c.id = a.connection_id
                where c.user_id = %s
                """,
                (user_id,),
            ),
            (
                "transacoes_open_finance",
                """
                select t.*
                from open_finance_transactions t
                join open_finance_accounts a on a.id = t.account_id
                join open_finance_connections c on c.id = a.connection_id
                where c.user_id = %s
                """,
                (user_id,),
            ),
        ]

        for name, sql, params in queries:
            table_name = sql.split(" from ", 1)[-1].split()[0].strip()
            if table_name and table_name.isidentifier() and not _table_exists(cur, table_name):
                datasets[name] = []
                continue
            datasets[name] = _fetch_rows(cur, name, sql, params)[1]

        optional_queries = [
            (
                "eventos_login",
                "select id, user_id, email, success, failure_reason, ip_address, user_agent, created_at from auth_login_events where user_id = %s",
                (user_id,),
            ),
            (
                "eventos_sistema",
                "select id, level, event_type, message, source, user_id, details, created_at from system_event_logs where user_id = %s",
                (user_id,),
            ),
        ]
        for name, sql, params in optional_queries:
            table_name = sql.split(" from ", 1)[-1].split()[0].strip()
            if _table_exists(cur, table_name):
                datasets[name] = _fetch_rows(cur, name, sql, params)[1]

    manifest = {
        "generated_at": datetime.now(timezone.utc),
        "user_id": user_id,
        "format": "json+csv",
        "datasets": {name: len(rows) for name, rows in datasets.items()},
        "notes": [
            "Hashes de senha não são exportados.",
            "Arquivos CSV são cópias tabulares; dados aninhados também aparecem no JSON completo.",
        ],
    }
    payload = {"manifesto": manifest, "dados": datasets}

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "dados.json",
            json.dumps(_json_safe(payload), ensure_ascii=False, indent=2),
        )
        zf.writestr(
            "manifesto.json",
            json.dumps(_json_safe(manifest), ensure_ascii=False, indent=2),
        )
        for name, rows in datasets.items():
            csv_buffer = io.StringIO()
            fieldnames = sorted({key for row in rows for key in row.keys()})
            writer = csv.DictWriter(csv_buffer, fieldnames=fieldnames or ["sem_dados"])
            writer.writeheader()
            for row in rows:
                writer.writerow({
                    key: json.dumps(_json_safe(value), ensure_ascii=False) if isinstance(value, (dict, list)) else value
                    for key, value in row.items()
                })
            zf.writestr(f"csv/{name}.csv", csv_buffer.getvalue())

    return buffer.getvalue()


def delete_user_data(user_id: int) -> dict:
    primary_email = None
    user_owned_tables = (
        "credit_cards",
        "investment_lots",
        "investments",
        "category_budgets",
        "pending_actions",
        "user_category_rules",
        "user_category_triggers",
        "user_trigger_candidates",
        "user_category_feedback",
        "daily_report_prefs",
        "ofx_imports",
        "dashboard_sessions",
        "link_codes",
        "platform_onboarding_tokens",
        "password_reset_tokens",
        "accounts",
        "launches",
        "pockets",
        "user_identities",
        "auth_accounts",
    )
    deleted = 0

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select email from auth_accounts where user_id = %s", (user_id,))
            emails = [row["email"] for row in cur.fetchall() if row.get("email")]
            primary_email = emails[0] if emails else None

            if _table_exists(cur, "auth_login_events"):
                cur.execute("delete from auth_login_events where user_id = %s", (user_id,))
                if emails:
                    cur.execute("delete from auth_login_events where email = any(%s)", (emails,))

            if _table_exists(cur, "system_event_logs"):
                cur.execute("delete from system_event_logs where user_id = %s", (user_id,))

            if _table_exists(cur, "email_verification_codes") and emails:
                cur.execute("delete from email_verification_codes where email = any(%s)", (emails,))

            if _table_exists(cur, "auth_rate_limits") and emails:
                identifiers = [f"email:{email.strip().lower()}" for email in emails]
                cur.execute("delete from auth_rate_limits where identifier = any(%s)", (identifiers,))

            if _table_exists(cur, "open_finance_transactions"):
                cur.execute(
                    """
                    delete from open_finance_transactions t
                    using open_finance_accounts a, open_finance_connections c
                    where t.account_id = a.id
                      and a.connection_id = c.id
                      and c.user_id = %s
                    """,
                    (user_id,),
                )
            if _table_exists(cur, "open_finance_accounts"):
                cur.execute(
                    """
                    delete from open_finance_accounts a
                    using open_finance_connections c
                    where a.connection_id = c.id
                      and c.user_id = %s
                    """,
                    (user_id,),
                )

            for table in (
                "open_finance_connections",
                "credit_transactions",
            ):
                if _table_exists(cur, table):
                    cur.execute(f"delete from {table} where user_id = %s", (user_id,))

            if _table_exists(cur, "credit_bills"):
                if _table_exists(cur, "credit_cards"):
                    cur.execute(
                        """
                        delete from credit_bills b
                        using credit_cards c
                        where b.card_id = c.id
                          and c.user_id = %s
                        """,
                        (user_id,),
                    )
                if _column_exists(cur, "credit_bills", "user_id"):
                    cur.execute("delete from credit_bills where user_id = %s", (user_id,))

            for table in user_owned_tables:
                if _table_exists(cur, table) and _column_exists(cur, table, "user_id"):
                    cur.execute(f"delete from {table} where user_id = %s", (user_id,))

            cur.execute("delete from users where id = %s", (user_id,))
            deleted += cur.rowcount

            # Bancos antigos podem não ter todas as FKs/cascades esperadas.
            # A segunda passada remove qualquer resíduo órfão que tenha ficado.
            for table in user_owned_tables:
                if _table_exists(cur, table) and _column_exists(cur, table, "user_id"):
                    cur.execute(f"delete from {table} where user_id = %s", (user_id,))

            cur.execute("delete from users where id = %s", (user_id,))
            deleted += cur.rowcount

            cur.execute("select 1 from users where id = %s", (user_id,))
            if cur.fetchone():
                raise RuntimeError(f"Falha ao remover usuário {user_id}: registro ainda existe após a limpeza final.")

        conn.commit()

    # Verificação pós-commit: garante que outra conexão também enxerga a conta
    # como removida antes de o job considerar a exclusão concluída.
    with get_conn() as conn:
        with conn.cursor() as cur:
            for table in user_owned_tables:
                if _table_exists(cur, table) and _column_exists(cur, table, "user_id"):
                    cur.execute(f"delete from {table} where user_id = %s", (user_id,))

            cur.execute("delete from users where id = %s", (user_id,))
            deleted += cur.rowcount

            cur.execute("select 1 from users where id = %s", (user_id,))
            user_still_exists = cur.fetchone() is not None

            leftovers: dict[str, int] = {}
            for table in user_owned_tables:
                if _table_exists(cur, table) and _column_exists(cur, table, "user_id"):
                    cur.execute(f"select count(*) as total from {table} where user_id = %s", (user_id,))
                    total = int(cur.fetchone()["total"])
                    if total:
                        leftovers[table] = total

        conn.commit()

    if user_still_exists or leftovers:
        raise RuntimeError(
            f"Falha ao confirmar exclusão do usuário {user_id}: "
            f"user_exists={user_still_exists}; leftovers={leftovers}"
        )

    return {"user_id": user_id, "deleted": bool(deleted), "email": primary_email}


def _claim_due_account_deletions(limit: int, stale_after_minutes: int) -> list[int]:
    ensure_account_deletion_columns()

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select user_id
            from auth_accounts
            where deletion_scheduled_for <= now()
              and (
                deletion_status = 'scheduled'
                or (
                  deletion_status = 'processing'
                  and (
                    deletion_processing_started_at is null
                    or deletion_processing_started_at <= now() - (%s * interval '1 minute')
                  )
                )
              )
            order by deletion_scheduled_for
            limit %s
            for update skip locked
            """,
            (stale_after_minutes, limit),
        )
        due_user_ids = [int(row["user_id"]) for row in cur.fetchall()]

        for user_id in due_user_ids:
            cur.execute(
                """
                update auth_accounts
                set deletion_status = 'processing',
                    deletion_processing_started_at = now()
                where user_id = %s
                """,
                (user_id,),
            )

        conn.commit()

    return due_user_ids


def _restore_account_deletion_schedule(user_id: int) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            update auth_accounts
            set deletion_status = 'scheduled',
                deletion_processing_started_at = null
            where user_id = %s
              and deletion_status = 'processing'
            """,
            (user_id,),
        )
        conn.commit()


def process_due_account_deletions(limit: int = 50, stale_after_minutes: int = 120) -> list[dict]:
    due_user_ids = _claim_due_account_deletions(limit, stale_after_minutes)

    results = []
    for user_id in due_user_ids:
        try:
            results.append(delete_user_data(user_id))
        except Exception as exc:
            _restore_account_deletion_schedule(user_id)
            results.append({"user_id": user_id, "deleted": False, "error": str(exc)})
    return results
