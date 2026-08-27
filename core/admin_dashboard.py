from __future__ import annotations

import asyncio
import logging
import os
import secrets
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import bcrypt
import jwt as pyjwt
import psycopg
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel
from slowapi.util import get_remote_address

from config.env import load_app_env

from core.crypto import (
    PiiAccessContext,
    decrypt_pii_optional,
    encrypt_pii_optional,
    pii_audit_batch,
)


load_app_env()

DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()
WHATSAPP_NUMBER = os.getenv("WHATSAPP_NUMBER", "")
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_ID_PRO = os.getenv("STRIPE_PRICE_ID_PRO", "")
STRIPE_PRICE_ID_PRO_MENSAL = os.getenv("STRIPE_PRICE_ID_PRO_MENSAL", "")
STRIPE_PRICE_ID_PRO_ANUAL = os.getenv("STRIPE_PRICE_ID_PRO_ANUAL", "")
_STRIPE_PRICE_CONFIGURED = bool(STRIPE_PRICE_ID_PRO_MENSAL or STRIPE_PRICE_ID_PRO_ANUAL or STRIPE_PRICE_ID_PRO)
ADMIN_DASHBOARD_USERNAME = (os.getenv("ADMIN_DASHBOARD_USERNAME") or "admin").strip()
ADMIN_DASHBOARD_PASSWORD = os.getenv("ADMIN_DASHBOARD_PASSWORD", "")
ADMIN_DASHBOARD_PASSWORD_HASH = os.getenv("ADMIN_DASHBOARD_PASSWORD_HASH", "")
ADMIN_DASHBOARD_SESSION_HOURS = float(os.getenv("ADMIN_DASHBOARD_SESSION_HOURS", "12"))
DB_CONNECT_TIMEOUT = int(os.getenv("DB_CONNECT_TIMEOUT", "5"))
ADMIN_AUTH_COOKIE_NAME = "admin_auth_token"

_bearer = HTTPBearer(auto_error=False)


async def db_connect():
    return await psycopg.AsyncConnection.connect(
        DATABASE_URL,
        row_factory=dict_row,
        connect_timeout=DB_CONNECT_TIMEOUT,
    )


def _json_safe(obj: Any) -> Any:
    """Recursively convert Decimal/datetime/date to JSON-serializable primitives."""
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    return obj


async def ensure_admin_tables():
    """Create lightweight observability tables used by the private admin dashboard."""
    async with await db_connect() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                CREATE TABLE IF NOT EXISTS auth_login_events (
                    id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
                    email TEXT,
                    success BOOLEAN NOT NULL,
                    ip_address TEXT,
                    user_agent TEXT,
                    failure_reason TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_auth_login_events_created_at
                ON auth_login_events (created_at DESC)
                """
            )
            await cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_auth_login_events_user_success
                ON auth_login_events (user_id, success, created_at DESC)
                """
            )
            # Suporta a detecção de spike de falha de login por IP (A09).
            await cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_auth_login_events_ip_time
                ON auth_login_events (ip_address, success, created_at DESC)
                """
            )
            await cur.execute(
                """
                CREATE TABLE IF NOT EXISTS system_event_logs (
                    id BIGSERIAL PRIMARY KEY,
                    level TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    source TEXT,
                    user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
                    details JSONB,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_system_event_logs_created_at
                ON system_event_logs (created_at DESC)
                """
            )
            await cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_system_event_logs_level_type
                ON system_event_logs (level, event_type, created_at DESC)
                """
            )
        await conn.commit()


async def log_auth_login_event(
    email: str,
    success: bool,
    user_id: int | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    failure_reason: str | None = None,
):
    try:
        normalized_email = (email or "").strip().lower() or None
        async with await db_connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO auth_login_events (
                        user_id, email, email_enc, success, ip_address, user_agent, failure_reason
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (user_id, normalized_email, encrypt_pii_optional(normalized_email),
                     success, ip_address, user_agent, failure_reason),
                )
            await conn.commit()
        # A09 — spike de falha de login: agenda a detecção FORA desta transação
        # (task de fundo, conexão própria, depois do commit acima). Assim não
        # atrasa a resposta do login, não pode abortar este commit, e a contagem
        # enxerga as tentativas concorrentes já persistidas. Fire-and-forget.
        if not success:
            try:
                from core.services.security_alerts import schedule_auth_failure_spike_check
                schedule_auth_failure_spike_check(ip_address)
            except Exception:
                pass
    except Exception as exc:
        print(f"[admin] failed to record auth login event: {exc}", file=sys.stderr)


async def log_system_event(
    level: str,
    event_type: str,
    message: str,
    *,
    source: str | None = None,
    user_id: int | None = None,
    details: dict[str, Any] | None = None,
):
    try:
        async with await db_connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO system_event_logs (level, event_type, message, source, user_id, details)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (level, event_type, message[:1000], source, user_id, Jsonb(details or {})),
                )
            await conn.commit()
    except Exception as exc:
        print(f"[admin] failed to record system event: {exc}", file=sys.stderr)


def admin_enabled() -> bool:
    return bool(ADMIN_DASHBOARD_PASSWORD or ADMIN_DASHBOARD_PASSWORD_HASH)


_admin_log = logging.getLogger("admin_dashboard")

# Plaintext só é tolerado fora de prod (testes/dev local). Em prod o boot
# falha — mesmo padrão do WA_APP_SECRET em adapters/whatsapp/wa_app.py.
if ADMIN_DASHBOARD_PASSWORD and not ADMIN_DASHBOARD_PASSWORD_HASH:
    if (os.getenv("APP_ENV") or "").strip().lower() == "prod":
        raise RuntimeError(
            "ADMIN_DASHBOARD_PASSWORD (plaintext) não é permitida com APP_ENV=prod. "
            "Configure ADMIN_DASHBOARD_PASSWORD_HASH (bcrypt) e remova "
            "ADMIN_DASHBOARD_PASSWORD. Gere o hash com:\n"
            '  python -c "import bcrypt; print(bcrypt.hashpw(b\'SUA_SENHA\', '
            'bcrypt.gensalt()).decode())"'
        )
    _admin_log.warning(
        "[admin] ADMIN_DASHBOARD_PASSWORD em plaintext detectada (tolerada fora "
        "de prod). Configure ADMIN_DASHBOARD_PASSWORD_HASH (bcrypt) e remova "
        "ADMIN_DASHBOARD_PASSWORD."
    )


def _check_admin_password(password: str) -> bool:
    if ADMIN_DASHBOARD_PASSWORD_HASH:
        try:
            return bcrypt.checkpw(
                password.encode("utf-8"),
                ADMIN_DASHBOARD_PASSWORD_HASH.encode("utf-8"),
            )
        except Exception as exc:
            # Hash malformado / chave incompatível. Loga loud e NEGA o login —
            # nunca cai pro fallback plaintext (era o bug original).
            _admin_log.error(
                "[admin] Falha ao verificar bcrypt do admin (hash malformado?): %s",
                exc,
            )
            return False
    if not ADMIN_DASHBOARD_PASSWORD:
        return False
    # compare_digest pra evitar timing leak no fallback plaintext.
    return secrets.compare_digest(password, ADMIN_DASHBOARD_PASSWORD)


def _make_admin_jwt(username: str, jwt_secret: str) -> str:
    payload = {
        "sub": username,
        "type": "admin",
        "exp": datetime.now(timezone.utc) + timedelta(hours=ADMIN_DASHBOARD_SESSION_HOURS),
    }
    return pyjwt.encode(payload, jwt_secret, algorithm="HS256")


def _decode_jwt(token: str, jwt_secret: str) -> dict | None:
    try:
        return pyjwt.decode(token, jwt_secret, algorithms=["HS256"])
    except Exception:
        return None


def _set_admin_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        ADMIN_AUTH_COOKIE_NAME,
        token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=int(ADMIN_DASHBOARD_SESSION_HOURS * 3600),
        path="/admin",
    )


def _clear_admin_cookie(response: Response) -> None:
    response.delete_cookie(ADMIN_AUTH_COOKIE_NAME, path="/admin")


def _resolve_admin_username(
    jwt_secret: str,
    request: Request,
    creds: HTTPAuthorizationCredentials | None = None,
) -> str:
    if not admin_enabled():
        raise HTTPException(status_code=503, detail="Painel admin não configurado.")
    token = creds.credentials if creds else request.cookies.get(ADMIN_AUTH_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Token admin não fornecido.")
    payload = _decode_jwt(token, jwt_secret)
    if (
        not payload
        or payload.get("type") != "admin"
        or payload.get("sub") != ADMIN_DASHBOARD_USERNAME
    ):
        raise HTTPException(status_code=401, detail="Token admin inválido ou expirado.")
    return str(payload["sub"])


async def get_current_admin(
    jwt_secret: str,
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    return _resolve_admin_username(jwt_secret, request, creds)


class AdminLoginBody(BaseModel):
    username: str
    password: str


def _decrypt_admin_field(ct: str, fallback, *, purpose: str, admin_user: str,
                         user_id: int, field: str):
    """decrypt_pii_optional que NÃO derruba o painel se a row for indecifrável
    (ex.: lixo de teste cifrado com chave errada). Uma linha ruim vira um
    marcador visível em vez de um 500 no /admin inteiro."""
    try:
        return decrypt_pii_optional(
            ct,
            ctx=PiiAccessContext(
                purpose=purpose,
                actor=f"admin:{admin_user}",
                subject_user_id=user_id,
                field=field,
            ),
        )
    except Exception:
        logging.getLogger(__name__).warning(
            "PII indecifrável no admin: user_id=%s field=%s (chave errada?)", user_id, field
        )
        return fallback or "⚠️ indecifrável"


def _decrypt_admin_row(row: dict, admin_user: str, purpose: str) -> dict:
    """Decifra colunas email/phone/display_name de uma row de auth_accounts/auth_login_events.

    Cai pra coluna em claro se _enc estiver ausente. Registra cada decrypt em
    pii_access_log com actor=admin:{username}.
    """
    user_id = int(row.get("user_id") or 0)
    if row.get("email_enc"):
        row["email"] = _decrypt_admin_field(
            row["email_enc"], row.get("email"),
            purpose=purpose, admin_user=admin_user, user_id=user_id, field="email",
        )
    if row.get("phone_enc"):
        row["phone_e164"] = _decrypt_admin_field(
            row["phone_enc"], row.get("phone_e164"),
            purpose=purpose, admin_user=admin_user, user_id=user_id, field="phone",
        )
    if row.get("display_name_enc"):
        row["display_name"] = _decrypt_admin_field(
            row["display_name_enc"], row.get("display_name"),
            purpose=purpose, admin_user=admin_user, user_id=user_id, field="name",
        )
    # Não vaza colunas _enc pro JSON do frontend
    row.pop("email_enc", None)
    row.pop("phone_enc", None)
    row.pop("display_name_enc", None)
    return row


async def fetch_admin_overview(days: int = 30, admin_user: str = "admin") -> dict[str, Any]:
    """Wrapper que envolve a coleta de dados num pii_audit_batch — os ~30
    decrypts (top_users + recent_signups + recent_logins) acumulam e geram
    1 INSERT batch no fim em vez de N inserts individuais. Evita congestionar
    o pool sync durante request async (causa raiz do connection timeout em prod)."""
    audit = pii_audit_batch()
    audit.__enter__()
    try:
        return await _fetch_admin_overview_inner(days, admin_user)
    finally:
        audit.__exit__(None, None, None)


async def _fetch_admin_overview_inner(days: int = 30, admin_user: str = "admin") -> dict[str, Any]:
    days = max(7, min(int(days or 30), 180))
    now = datetime.now(timezone.utc)
    start_30d = now - timedelta(days=30)
    start_7d = now - timedelta(days=7)
    start_window = now - timedelta(days=days)

    async with await db_connect() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM users) AS total_users,
                    (SELECT COUNT(*) FROM auth_accounts) AS total_accounts,
                    (SELECT COUNT(*) FROM auth_accounts WHERE created_at >= %s) AS accounts_30d,
                    (SELECT COUNT(*) FROM auth_accounts WHERE created_at >= %s) AS accounts_7d,
                    (SELECT COUNT(*) FROM auth_accounts WHERE last_activity_at >= %s) AS active_users_30d,
                    (SELECT COUNT(*) FROM auth_accounts WHERE last_activity_at >= %s) AS active_users_7d,
                    (SELECT COUNT(*) FROM launches WHERE criado_em >= %s AND is_internal_movement = false) AS transactions_30d,
                    (SELECT COALESCE(SUM(valor), 0) FROM launches WHERE criado_em >= %s AND tipo IN ('receita', 'entrada') AND is_internal_movement = false) AS revenue_30d,
                    (SELECT COALESCE(SUM(valor), 0) FROM launches WHERE criado_em >= %s AND tipo IN ('despesa', 'saida') AND is_internal_movement = false) AS expenses_30d,
                    (SELECT COALESCE(SUM(balance), 0) FROM pockets) AS pockets_balance,
                    (SELECT COALESCE(SUM(balance), 0) FROM investments) AS investments_balance,
                    (SELECT COALESCE(SUM(due_amount), 0) FROM (
                        SELECT GREATEST(0, total - COALESCE(paid_amount, 0)) AS due_amount
                        FROM credit_bills
                        WHERE status IN ('open', 'closed')
                    ) x) AS cards_due_open
                """,
                (start_30d, start_7d, start_30d, start_7d, start_30d, start_30d, start_30d),
            )
            summary = dict(await cur.fetchone())

            await cur.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE created_at >= %s) AS login_attempts_30d,
                    COUNT(*) FILTER (WHERE created_at >= %s AND success = true) AS login_success_30d,
                    COUNT(*) FILTER (WHERE created_at >= %s) AS login_attempts_7d,
                    COUNT(*) FILTER (WHERE created_at >= %s AND success = true) AS login_success_7d
                FROM auth_login_events
                """,
                (start_30d, start_30d, start_7d, start_7d),
            )
            login_stats = dict(await cur.fetchone())

            await cur.execute(
                """
                WITH days AS (
                    SELECT generate_series(
                        date_trunc('day', %s::timestamptz),
                        date_trunc('day', now()),
                        interval '1 day'
                    )::date AS bucket
                ),
                signups AS (
                    SELECT created_at::date AS bucket, COUNT(*) AS value
                    FROM auth_accounts
                    WHERE created_at >= %s
                    GROUP BY 1
                ),
                logins AS (
                    SELECT created_at::date AS bucket, COUNT(*) FILTER (WHERE success = true) AS value
                    FROM auth_login_events
                    WHERE created_at >= %s
                    GROUP BY 1
                ),
                tx AS (
                    SELECT criado_em::date AS bucket, COUNT(*) AS value
                    FROM launches
                    WHERE criado_em >= %s AND is_internal_movement = false
                    GROUP BY 1
                ),
                receita AS (
                    SELECT criado_em::date AS bucket, COALESCE(SUM(valor), 0) AS value
                    FROM launches
                    WHERE criado_em >= %s AND tipo IN ('receita', 'entrada') AND is_internal_movement = false
                    GROUP BY 1
                ),
                despesa AS (
                    SELECT criado_em::date AS bucket, COALESCE(SUM(valor), 0) AS value
                    FROM launches
                    WHERE criado_em >= %s AND tipo IN ('despesa', 'saida') AND is_internal_movement = false
                    GROUP BY 1
                ),
                ativos AS (
                    SELECT last_activity_at::date AS bucket, COUNT(*) AS value
                    FROM auth_accounts
                    WHERE last_activity_at >= %s
                    GROUP BY 1
                )
                SELECT
                    d.bucket,
                    COALESCE(s.value, 0) AS signups,
                    COALESCE(l.value, 0) AS logins,
                    COALESCE(t.value, 0) AS transactions,
                    COALESCE(r.value, 0) AS revenue,
                    COALESCE(e.value, 0) AS expenses,
                    COALESCE(a.value, 0) AS active_users
                FROM days d
                LEFT JOIN signups s ON s.bucket = d.bucket
                LEFT JOIN logins l ON l.bucket = d.bucket
                LEFT JOIN tx t ON t.bucket = d.bucket
                LEFT JOIN receita r ON r.bucket = d.bucket
                LEFT JOIN despesa e ON e.bucket = d.bucket
                LEFT JOIN ativos a ON a.bucket = d.bucket
                ORDER BY d.bucket
                """,
                (start_window, start_window, start_window, start_window, start_window, start_window, start_window),
            )
            time_series = [dict(row) for row in await cur.fetchall()]

            await cur.execute(
                """
                SELECT
                    DATE_TRUNC('month', criado_em)::date AS month,
                    COUNT(*) FILTER (WHERE is_internal_movement = false) AS transaction_count,
                    COALESCE(SUM(valor) FILTER (WHERE tipo IN ('receita', 'entrada') AND is_internal_movement = false), 0) AS revenue,
                    COALESCE(SUM(valor) FILTER (WHERE tipo IN ('despesa', 'saida') AND is_internal_movement = false), 0) AS expenses
                FROM launches
                -- meses-calendário incluindo o atual (NOW() - 6 months corta
                -- no meio de um 7º mês e o "últimos 6 meses" mostrava 7 barras)
                WHERE criado_em >= DATE_TRUNC('month', NOW()) - INTERVAL '5 months'
                GROUP BY 1
                ORDER BY 1 DESC
                """
            )
            monthly_financials = [dict(row) for row in await cur.fetchall()]

            await cur.execute(
                """
                SELECT
                    a.user_id,
                    a.email,
                    a.email_enc,
                    a.created_at,
                    a.last_activity_at,
                    COALESCE(COUNT(l.id), 0) AS total_transactions,
                    COALESCE(SUM(
                        CASE
                            WHEN l.tipo IN ('receita', 'entrada') AND l.is_internal_movement = false THEN l.valor
                            WHEN l.tipo IN ('despesa', 'saida') AND l.is_internal_movement = false THEN -l.valor
                            ELSE 0
                        END
                    ), 0) AS net_flow
                FROM auth_accounts a
                LEFT JOIN launches l
                    ON l.user_id = a.user_id
                   AND l.criado_em >= %s
                GROUP BY a.user_id, a.email, a.email_enc, a.created_at, a.last_activity_at
                ORDER BY total_transactions DESC, a.created_at DESC
                LIMIT 10
                """,
                (start_30d,),
            )
            top_users = [_decrypt_admin_row(dict(row), admin_user, "render_admin_top_users")
                         for row in await cur.fetchall()]

            await cur.execute(
                """
                SELECT
                    a.user_id,
                    a.email,
                    a.email_enc,
                    a.plan,
                    a.created_at,
                    a.last_activity_at,
                    a.phone_status,
                    a.whatsapp_verified_at,
                    EXISTS (
                        SELECT 1
                        FROM user_identities ui
                        WHERE ui.user_id = a.user_id
                          AND ui.provider = 'whatsapp'
                    ) AS has_whatsapp_identity
                FROM auth_accounts a
                ORDER BY a.created_at DESC
                LIMIT 10
                """
            )
            recent_signups = [_decrypt_admin_row(dict(row), admin_user, "render_admin_recent_signups")
                              for row in await cur.fetchall()]

            await cur.execute(
                """
                SELECT email, email_enc, user_id, success, failure_reason, ip_address, created_at
                FROM auth_login_events
                ORDER BY created_at DESC
                LIMIT 20
                """
            )
            recent_logins = [_decrypt_admin_row(dict(row), admin_user, "render_admin_recent_logins")
                             for row in await cur.fetchall()]

            await cur.execute(
                """
                SELECT id, level, event_type, message, source, user_id, details, created_at
                FROM system_event_logs
                WHERE created_at >= NOW() - INTERVAL '24 hours'
                ORDER BY created_at DESC
                LIMIT 100
                """
            )
            recent_errors = [dict(row) for row in await cur.fetchall()]

            await cur.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours' AND level = 'error') AS backend_errors_24h,
                    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours' AND level = 'warning') AS backend_warnings_24h,
                    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours' AND event_type = 'whatsapp_webhook_received') AS whatsapp_webhooks_24h,
                    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours' AND event_type = 'whatsapp_send_success') AS whatsapp_send_success_24h,
                    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours' AND event_type IN ('whatsapp_send_failed', 'whatsapp_send_exception')) AS whatsapp_send_failures_24h,
                    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours' AND event_type = 'category_ai_classified') AS category_ai_success_24h,
                    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours' AND event_type = 'category_ai_invalid_response') AS category_ai_invalid_response_24h,
                    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours' AND event_type = 'category_ai_error') AS category_ai_errors_24h,
                    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours' AND event_type IN ('category_ai_skipped_no_api_key', 'category_ai_client_unavailable')) AS category_ai_unavailable_24h,
                    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days' AND event_type = 'whatsapp_token_invalid') AS whatsapp_token_invalid_7d,
                    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours' AND event_type = 'whatsapp_queue_drop') AS whatsapp_queue_drop_24h,
                    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days' AND event_type = 'whatsapp_worker_error') AS whatsapp_worker_errors_7d,
                    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days' AND event_type = 'billing_signature_invalid') AS billing_signature_invalid_7d,
                    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days' AND event_type = 'billing_payment_failed') AS billing_payment_failed_7d,
                    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days' AND event_type = 'engagement_loop_error') AS engagement_errors_7d,
                    MAX(created_at) FILTER (WHERE event_type = 'whatsapp_webhook_received') AS last_whatsapp_webhook_at,
                    MAX(created_at) FILTER (WHERE event_type = 'whatsapp_send_success') AS last_whatsapp_send_success_at,
                    MAX(created_at) FILTER (WHERE event_type LIKE 'category_ai_%') AS last_category_ai_at,
                    MAX(created_at) FILTER (WHERE event_type = 'whatsapp_token_invalid') AS last_whatsapp_token_invalid_at,
                    MAX(created_at) FILTER (WHERE event_type = 'billing_webhook_received') AS last_billing_webhook_at
                FROM system_event_logs
                """
            )
            ops_summary = dict(await cur.fetchone())

            await cur.execute(
                """
                SELECT
                    COUNT(*) FILTER (
                        WHERE a.last_activity_at >= NOW() - INTERVAL '24 hours'
                          AND EXISTS (
                               SELECT 1
                               FROM user_identities ui
                               WHERE ui.user_id = a.user_id
                                 AND ui.provider = 'whatsapp'
                          )
                    ) AS whatsapp_real_activity_24h,
                    MAX(a.last_activity_at) FILTER (
                        WHERE EXISTS (
                            SELECT 1
                            FROM user_identities ui
                            WHERE ui.user_id = a.user_id
                              AND ui.provider = 'whatsapp'
                        )
                    ) AS last_whatsapp_real_activity_at,
                    COUNT(*) FILTER (
                        WHERE a.phone_status = 'confirmed'
                           OR a.whatsapp_verified_at IS NOT NULL
                           OR EXISTS (
                               SELECT 1
                               FROM user_identities ui
                               WHERE ui.user_id = a.user_id
                                 AND ui.provider = 'whatsapp'
                           )
                    ) AS whatsapp_connected_users,
                    COUNT(*) FILTER (
                        WHERE a.phone_status = 'pending'
                          AND NOT EXISTS (
                               SELECT 1
                               FROM user_identities ui
                               WHERE ui.user_id = a.user_id
                                 AND ui.provider = 'whatsapp'
                          )
                    ) AS whatsapp_pending_users
                FROM auth_accounts a
                """
            )
            whatsapp_activity = dict(await cur.fetchone())

            await cur.execute(
                """
                SELECT level, event_type, message, source, user_id, details, created_at
                FROM system_event_logs
                WHERE event_type LIKE 'whatsapp_%'
                   OR event_type LIKE 'category_ai_%'
                   OR event_type LIKE 'billing_%'
                   OR event_type LIKE 'engagement_%'
                   OR event_type = 'http_unhandled_exception'
                ORDER BY created_at DESC
                LIMIT 25
                """
            )
            recent_ops = [dict(row) for row in await cur.fetchall()]

            await cur.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE event_type = 'email_sent'   AND created_at >= NOW() - INTERVAL '24 hours') AS sent_24h,
                    COUNT(*) FILTER (WHERE event_type = 'email_failed' AND created_at >= NOW() - INTERVAL '24 hours') AS failed_24h,
                    COUNT(*) FILTER (WHERE event_type = 'email_sent'   AND created_at >= NOW() - INTERVAL '7 days')  AS sent_7d,
                    COUNT(*) FILTER (WHERE event_type = 'email_sent'   AND created_at >= NOW() - INTERVAL '30 days') AS sent_30d
                FROM system_event_logs
                """
            )
            email_stats = dict(await cur.fetchone())

            await cur.execute(
                """
                SELECT id, event_type, message, details, created_at
                FROM system_event_logs
                WHERE event_type IN ('email_sent', 'email_failed')
                ORDER BY created_at DESC
                LIMIT 50
                """
            )
            recent_emails = [dict(row) for row in await cur.fetchall()]

    attempts_30d = int(login_stats.get("login_attempts_30d") or 0)
    attempts_7d = int(login_stats.get("login_attempts_7d") or 0)
    login_success_30d = int(login_stats.get("login_success_30d") or 0)
    login_success_7d = int(login_stats.get("login_success_7d") or 0)

    summary["login_attempts_30d"] = attempts_30d
    summary["login_success_30d"] = login_success_30d
    summary["login_attempts_7d"] = attempts_7d
    summary["login_success_7d"] = login_success_7d
    summary["login_success_rate_30d"] = round((login_success_30d / attempts_30d) * 100, 1) if attempts_30d else 0.0
    summary["login_success_rate_7d"] = round((login_success_7d / attempts_7d) * 100, 1) if attempts_7d else 0.0

    # Open Finance: saúde das conexões (contadores agregados, sem PII). Fail-soft —
    # o painel inteiro não pode cair porque uma contagem de OF falhou.
    try:
        from db.open_finance_state import of_health_counters
        open_finance = await asyncio.to_thread(of_health_counters)
    except Exception as exc:  # noqa: BLE001
        # Chave PRÓPRIA: `erro` já é a CONTAGEM de conexões em erro. As duas
        # semânticas na mesma chave faziam a falha virar tile verde no painel
        # (fInt("relation não existe") = NaN, NaN > 0 = false).
        open_finance = {"error": str(exc)[:200]}

    return {
        "generated_at": now,
        "window_days": days,
        "summary": summary,
        "open_finance": open_finance,
        "ops_summary": {
            **ops_summary,
            **whatsapp_activity,
            "wa_token_configured": bool(os.getenv("WA_TOKEN") or os.getenv("WA_ACCESS_TOKEN")),
            "wa_number_configured": bool(WHATSAPP_NUMBER),
            "wa_phone_number_id_configured": bool(os.getenv("WA_PHONE_NUMBER_ID")),
            "wa_verify_token_configured": bool(os.getenv("WA_VERIFY_TOKEN")),
            "wa_app_secret_configured": bool(os.getenv("WA_APP_SECRET")),
            "stripe_configured": bool(STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET and _STRIPE_PRICE_CONFIGURED),
            "whatsapp_telemetry_inconsistent": bool(
                (whatsapp_activity.get("whatsapp_real_activity_24h") or 0) > 0
                and (ops_summary.get("whatsapp_webhooks_24h") or 0) == 0
            ),
        },
        "time_series": time_series,
        "monthly_financials": monthly_financials,
        "top_users": top_users,
        "recent_signups": recent_signups,
        "recent_logins": recent_logins,
        "recent_errors": recent_errors,
        "recent_ops": recent_ops,
        "email_stats": email_stats,
        "recent_emails": recent_emails,
    }


# ── Painel de usuários (/admin/api/users) ────────────────────────────────────

_USER_STATUSES = ("paying", "trial", "past_due", "canceled", "granted", "free")


def _derive_account_status(row: dict, now: datetime) -> str:
    """Classifica a conta numa categoria única de assinatura.

    A fonte é o par (plan, last_payment_status) mantido pelos webhooks do
    Stripe. Atenção: customer.subscription.deleted volta plan pra 'free'
    ANTES de gravar last_payment_status='canceled' — por isso conta free com
    pagamento cancelado é ex-assinante ('canceled'), não 'free'. Pro sem
    status de pagamento é grant manual ('granted'), mas só enquanto
    plan_expires_at não passou.

    ESPELHO SQL: _ACCOUNT_STATUS_SQL logo abaixo tem de decidir idêntico —
    toda mudança aqui muda lá (o teste de paridade em
    tests/test_admin_users_panel.py compara os dois).
    """
    plan = (row.get("plan") or "free").strip().lower() or "free"
    pay = (row.get("last_payment_status") or "").strip().lower()
    if plan == "free":
        return "canceled" if pay in ("canceled", "incomplete_expired") else "free"
    # Expiração vem ANTES do status de pagamento: plan_service._paid_plan_active
    # trata tier pago vencido como inativo mesmo com status 'active' (webhook
    # perdido) — o painel tem de concordar com o entitlement real.
    expires = row.get("plan_expires_at")
    if expires is not None and expires < now:
        return "canceled"
    if pay == "trialing":
        return "trial"
    if pay == "active":
        return "paying"
    if pay in ("past_due", "unpaid", "incomplete"):
        return "past_due"
    if pay in ("canceled", "incomplete_expired"):
        return "canceled"
    return "granted"


# Espelho em SQL de _derive_account_status, pra filtrar/agregar no banco sem
# trazer a base inteira pro Python. Assume alias `a` pra auth_accounts.
_ACCOUNT_STATUS_SQL = """
    CASE
        WHEN lower(coalesce(nullif(trim(a.plan), ''), 'free')) = 'free' THEN
            CASE WHEN lower(coalesce(a.last_payment_status, ''))
                      IN ('canceled', 'incomplete_expired')
                 THEN 'canceled' ELSE 'free' END
        WHEN a.plan_expires_at IS NOT NULL AND a.plan_expires_at < now() THEN 'canceled'
        WHEN lower(coalesce(a.last_payment_status, '')) = 'trialing' THEN 'trial'
        WHEN lower(coalesce(a.last_payment_status, '')) = 'active' THEN 'paying'
        WHEN lower(coalesce(a.last_payment_status, ''))
             IN ('past_due', 'unpaid', 'incomplete') THEN 'past_due'
        WHEN lower(coalesce(a.last_payment_status, ''))
             IN ('canceled', 'incomplete_expired') THEN 'canceled'
        ELSE 'granted'
    END
"""


_billing_summary_cache: dict[str, Any] = {"fetched_at": None, "data": None}
_BILLING_CACHE_TTL_SECONDS = 300


def _fetch_stripe_billing_sync() -> dict[str, Any]:
    """Resume as assinaturas direto da API do Stripe (fonte da verdade de
    preço): MRR, ticket médio e contagens por status. Valores anuais são
    normalizados pra base mensal (unit_amount/12)."""
    if not STRIPE_SECRET_KEY:
        return {"available": False, "reason": "stripe_not_configured"}
    import stripe

    stripe.api_key = STRIPE_SECRET_KEY
    counts = {"active": 0, "trialing": 0, "past_due": 0, "canceled": 0, "other": 0}
    mrr = 0.0
    trial_mrr = 0.0
    active_amounts: list[float] = []
    scanned = 0
    try:
        subs = stripe.Subscription.list(status="all", limit=100)
        for sub in subs.auto_paging_iter():
            scanned += 1
            if scanned > 1000:  # guarda contra base gigante; hoje são <10
                break
            status = str(getattr(sub, "status", "") or "")
            if status in ("active", "trialing", "past_due"):
                counts[status] += 1
            elif status in ("canceled", "incomplete_expired"):
                counts["canceled"] += 1
            else:
                counts["other"] += 1

            monthly = 0.0
            items = getattr(getattr(sub, "items", None), "data", None) or []
            if items:
                price = getattr(items[0], "price", None)
                unit = getattr(price, "unit_amount", None) if price else None
                recurring = getattr(price, "recurring", None) if price else None
                interval = str(getattr(recurring, "interval", "month") or "month")
                interval_count = int(getattr(recurring, "interval_count", 1) or 1)
                if unit is not None:
                    monthly = (float(unit) / 100.0) / interval_count
                    if interval == "year":
                        monthly /= 12.0
                    elif interval == "week":
                        monthly *= 4.345
                    elif interval == "day":
                        monthly *= 30.0
            if status == "active":
                mrr += monthly
                active_amounts.append(monthly)
            elif status == "trialing":
                trial_mrr += monthly
    except Exception as exc:
        logging.getLogger(__name__).warning("[admin] Stripe billing summary falhou: %s", exc)
        return {"available": False, "reason": str(exc)[:200]}

    return {
        "available": True,
        "subscriptions": counts,
        "mrr": round(mrr, 2),
        "ticket_medio": round(sum(active_amounts) / len(active_amounts), 2) if active_amounts else 0.0,
        "trial_mrr_potencial": round(trial_mrr, 2),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


async def fetch_billing_summary(force: bool = False) -> dict[str, Any]:
    """Wrapper cacheado (5 min) do resumo Stripe — a lista de usuários abre a
    cada clique no card e não deve martelar a API do Stripe.

    O carimbo do cache avança TAMBÉM quando a chamada falha: o TTL vira
    backoff de retry, senão uma queda do Stripe adicionaria a latência da
    API falhando em toda requisição do painel. Payload bom anterior é
    preservado com stale=True (o fetched_at interno dele mostra a idade)."""
    now = datetime.now(timezone.utc)
    cached_at = _billing_summary_cache.get("fetched_at")
    if (
        not force
        and cached_at is not None
        and (now - cached_at).total_seconds() < _BILLING_CACHE_TTL_SECONDS
        and _billing_summary_cache.get("data") is not None
    ):
        return _billing_summary_cache["data"]
    data = await asyncio.to_thread(_fetch_stripe_billing_sync)
    if data.get("available") or _billing_summary_cache.get("data") is None:
        _billing_summary_cache["data"] = data
    else:
        # Falha com payload bom em mãos: preserva os números, marcados stale
        _billing_summary_cache["data"] = {**_billing_summary_cache["data"], "stale": True}
    _billing_summary_cache["fetched_at"] = now
    return _billing_summary_cache["data"]


async def _fetch_checkout_funnel(cur) -> dict[str, int]:
    """Funil de checkout a partir da tabela dedicada checkout_funnel_events,
    correlacionando abertura e conclusão pelo session_id do Stripe (a MESMA
    tentativa). Devolve, em 30d e 7d:

      people_Nd            — pessoas distintas que abriram algum checkout
      sessions_started_Nd  — sessões (tentativas) abertas
      sessions_completed_Nd— dessas, as que concluíram (mesmo session_id)

    A conversão (sessions_completed / sessions_started) é por SESSÃO: um
    ex-assinante que comprou numa sessão e depois reabre e abandona outra tem
    a 2ª sessão contada como não-convertida — sem a correlação por session_id
    isso era impossível de separar (era a raiz dos 3 apontamentos anteriores).
    Viver em tabela própria (não em system_event_logs) também torna a
    telemetria imune ao 'Limpar'/purge do feed.

    Uma sessão sem session_id (raro — Stripe sempre devolve) entra em people
    mas não nas contagens de sessão (COUNT DISTINCT ignora NULL)."""
    await cur.execute(
        """
        SELECT
            COUNT(DISTINCT user_id)    FILTER (WHERE w30)                AS people_30d,
            COUNT(DISTINCT session_id) FILTER (WHERE w30)                AS sessions_started_30d,
            COUNT(DISTINCT session_id) FILTER (WHERE w30 AND converted)  AS sessions_completed_30d,
            COUNT(DISTINCT user_id)    FILTER (WHERE w7)                 AS people_7d,
            COUNT(DISTINCT session_id) FILTER (WHERE w7)                 AS sessions_started_7d,
            COUNT(DISTINCT session_id) FILTER (WHERE w7 AND converted)   AS sessions_completed_7d
        FROM (
            SELECT
                s.user_id,
                s.session_id,
                s.created_at >= NOW() - INTERVAL '30 days' AS w30,
                s.created_at >= NOW() - INTERVAL '7 days'  AS w7,
                (s.session_id IS NOT NULL AND EXISTS (
                    SELECT 1 FROM checkout_funnel_events c
                    WHERE c.kind = 'completed' AND c.session_id = s.session_id
                )) AS converted
            FROM checkout_funnel_events s
            WHERE s.kind = 'started'
              AND s.created_at >= NOW() - INTERVAL '30 days'
        ) per_start
        """
    )
    row = await cur.fetchone()
    return {k: int(v or 0) for k, v in dict(row).items()}


_ADMIN_USERS_HARD_CAP = 2000


async def fetch_admin_users(
    *,
    q: str = "",
    plan: str = "",
    status: str = "",
    page: int = 0,
    per_page: int = 50,
    sort: str = "created_at",
    admin_user: str = "admin",
) -> dict[str, Any]:
    """Lista paginada de contas com classificação de assinatura + agregados.

    Agregados, filtros de status/plano e paginação rodam em SQL sobre a base
    INTEIRA (_ACCOUNT_STATUS_SQL). A única exceção é a busca (q): e-mail é
    cifrado no banco, então o match de substring exige decifrar — esse caminho
    varre no máximo _ADMIN_USERS_HARD_CAP contas mais recentes e devolve
    truncated=True quando bateu no teto.

    Minimização de PII: sem busca, só as rows da página devolvida são
    decifradas. Cada decrypt é auditado (purpose=render_admin_users_list).
    """
    q = (q or "").strip().lower()
    plan = (plan or "").strip().lower()
    status = (status or "").strip().lower()
    if status and status not in _USER_STATUSES:
        status = ""
    page = max(0, int(page or 0))
    per_page = max(1, min(int(per_page or 50), 200))
    order_col = "last_activity_at" if sort == "last_activity_at" else "created_at"
    now = datetime.now(timezone.utc)

    select_cols = f"""
                    a.user_id,
                    a.email,
                    a.email_enc,
                    a.plan,
                    a.plan_expires_at,
                    a.last_payment_status,
                    a.stripe_customer_id,
                    a.trial_started_at,
                    a.plan_selected_at,
                    a.created_at,
                    a.last_activity_at,
                    a.phone_status,
                    a.whatsapp_verified_at,
                    a.deletion_status,
                    a.signup_source,
                    {_ACCOUNT_STATUS_SQL} AS account_status,
                    EXISTS (
                        SELECT 1 FROM user_identities ui
                        WHERE ui.user_id = a.user_id AND ui.provider = 'whatsapp'
                    ) AS has_whatsapp_identity
    """
    order_sql = f"ORDER BY a.{order_col} DESC NULLS LAST, a.user_id DESC"

    clauses: list[str] = []
    params: list[Any] = []
    if plan:
        clauses.append("lower(coalesce(nullif(trim(a.plan), ''), 'free')) = %s")
        params.append(plan)
    if status:
        clauses.append(f"{_ACCOUNT_STATUS_SQL} = %s")
        params.append(status)
    where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    truncated = False
    async with await db_connect() as conn:
        async with conn.cursor() as cur:
            # Agregados: base inteira, sempre, independente dos filtros
            await cur.execute(
                f"""
                SELECT {_ACCOUNT_STATUS_SQL} AS account_status,
                       COUNT(*) AS n,
                       COUNT(*) FILTER (
                           WHERE a.phone_status = 'confirmed'
                              OR EXISTS (
                                  SELECT 1 FROM user_identities ui
                                  WHERE ui.user_id = a.user_id
                                    AND ui.provider = 'whatsapp'
                              )
                       ) AS wa
                FROM auth_accounts a
                GROUP BY 1
                """
            )
            aggregates: dict[str, int] = {s: 0 for s in _USER_STATUSES}
            aggregates["whatsapp_connected"] = 0
            for r in await cur.fetchall():
                aggregates[r["account_status"]] = int(r["n"])
                aggregates["whatsapp_connected"] += int(r["wa"])
            aggregates["total"] = sum(aggregates[s] for s in _USER_STATUSES)

            # Funil de checkout (tabela dedicada checkout_funnel_events):
            # pessoas que abriram × conversão POR SESSÃO (correlação por
            # session_id), em 30d/7d.
            checkout_funnel = await _fetch_checkout_funnel(cur)

            # Origem do cadastro (base inteira): web × app × google × …
            # NULL = contas anteriores à coluna → 'desconhecido'.
            await cur.execute(
                """
                SELECT coalesce(signup_source, 'desconhecido') AS src, COUNT(*) AS n
                FROM auth_accounts
                GROUP BY 1
                """
            )
            by_source = {r["src"]: int(r["n"]) for r in await cur.fetchall()}

            if not q:
                await cur.execute(
                    f"SELECT COUNT(*) AS n FROM auth_accounts a {where_sql}",
                    tuple(params),
                )
                total_filtered = int((await cur.fetchone())["n"])
                await cur.execute(
                    f"""
                    SELECT {select_cols}
                    FROM auth_accounts a
                    {where_sql}
                    {order_sql}
                    LIMIT %s OFFSET %s
                    """,
                    tuple(params) + (per_page, page * per_page),
                )
                page_rows = [dict(r) for r in await cur.fetchall()]
                for row in page_rows:
                    _decrypt_admin_row(row, admin_user, "render_admin_users_list")
            else:
                # Busca: match contra o e-mail decifrado, varrendo as
                # _ADMIN_USERS_HARD_CAP contas mais recentes que passam nos
                # filtros SQL (batch de auditoria em quem chama).
                await cur.execute(
                    f"""
                    SELECT {select_cols}
                    FROM auth_accounts a
                    {where_sql}
                    {order_sql}
                    LIMIT %s
                    """,
                    tuple(params) + (_ADMIN_USERS_HARD_CAP,),
                )
                rows = [dict(r) for r in await cur.fetchall()]
                truncated = len(rows) >= _ADMIN_USERS_HARD_CAP
                for row in rows:
                    _decrypt_admin_row(row, admin_user, "render_admin_users_list")
                rows = [r for r in rows if q in (r.get("email") or "").lower()]
                total_filtered = len(rows)
                page_rows = rows[page * per_page:(page + 1) * per_page]

            tx_by_user: dict[int, int] = {}
            if page_rows:
                await cur.execute(
                    """
                    SELECT user_id, COUNT(*) AS n
                    FROM launches
                    WHERE user_id = ANY(%s)
                      AND criado_em >= NOW() - INTERVAL '30 days'
                      AND is_internal_movement = false
                    GROUP BY user_id
                    """,
                    ([int(r["user_id"]) for r in page_rows],),
                )
                tx_by_user = {int(r["user_id"]): int(r["n"]) for r in await cur.fetchall()}

    for row in page_rows:
        row["tx_30d"] = tx_by_user.get(int(row["user_id"]), 0)

    return {
        "generated_at": now,
        "aggregates": aggregates,
        "checkout_funnel": checkout_funnel,
        "by_source": by_source,
        "billing": await fetch_billing_summary(),
        "users": page_rows,
        "page": page,
        "per_page": per_page,
        "total": total_filtered,
        "truncated": truncated,
        "filters": {"q": q, "plan": plan, "status": status, "sort": order_col},
    }


async def fetch_admin_user_detail(user_id: int, admin_user: str = "admin") -> dict[str, Any] | None:
    """Drill-down de uma conta pro suporte: perfil + assinatura + agregados de
    uso + eventos recentes. LGPD: nada de conteúdo de transação — só contagens
    e datas; e-mail/telefone/nome decifrados com auditoria individual."""
    now = datetime.now(timezone.utc)
    async with await db_connect() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT
                    a.user_id,
                    a.email, a.email_enc,
                    a.phone_e164, a.phone_enc,
                    a.display_name, a.display_name_enc,
                    a.plan, a.plan_expires_at, a.last_payment_status,
                    a.stripe_customer_id, a.trial_started_at, a.plan_selected_at,
                    a.created_at, a.last_activity_at,
                    a.phone_status, a.whatsapp_verified_at, a.whatsapp_updates_opt_out,
                    a.engagement_opt_out, a.tip_email_opt_out, a.insight_email_opt_out,
                    a.deletion_status, a.deletion_requested_at, a.signup_source,
                    a.ai_messages_this_month,
                    EXISTS (
                        SELECT 1 FROM user_identities ui
                        WHERE ui.user_id = a.user_id AND ui.provider = 'whatsapp'
                    ) AS has_whatsapp_identity
                FROM auth_accounts a
                WHERE a.user_id = %s
                """,
                (int(user_id),),
            )
            row = await cur.fetchone()
            if not row:
                return None
            profile = _decrypt_admin_row(dict(row), admin_user, "render_admin_user_detail")
            profile["account_status"] = _derive_account_status(profile, now)

            await cur.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE is_internal_movement = false) AS tx_total,
                    COUNT(*) FILTER (
                        WHERE is_internal_movement = false
                          AND criado_em >= NOW() - INTERVAL '30 days'
                    ) AS tx_30d,
                    MAX(criado_em) FILTER (WHERE is_internal_movement = false) AS last_tx_at
                FROM launches
                WHERE user_id = %s
                """,
                (int(user_id),),
            )
            usage = dict(await cur.fetchone())

            await cur.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM pockets      WHERE user_id = %(uid)s) AS pockets_count,
                    (SELECT COUNT(*) FROM investments  WHERE user_id = %(uid)s) AS investments_count,
                    (SELECT COUNT(*) FROM credit_cards WHERE user_id = %(uid)s) AS cards_count
                """,
                {"uid": int(user_id)},
            )
            usage.update(dict(await cur.fetchone()))

            await cur.execute(
                """
                SELECT level, event_type, message, source, created_at
                FROM system_event_logs
                WHERE user_id = %s
                ORDER BY created_at DESC
                LIMIT 15
                """,
                (int(user_id),),
            )
            recent_events = [dict(r) for r in await cur.fetchall()]

            await cur.execute(
                """
                SELECT success, failure_reason, ip_address, created_at
                FROM auth_login_events
                WHERE user_id = %s
                ORDER BY created_at DESC
                LIMIT 10
                """,
                (int(user_id),),
            )
            recent_logins = [dict(r) for r in await cur.fetchall()]

    return {
        "generated_at": now,
        "profile": profile,
        "usage": usage,
        "recent_events": recent_events,
        "recent_logins": recent_logins,
    }


# Valores aceitos na coluna auth_accounts.plan, na ordem da escada. São os
# MESMOS que o webhook da Stripe grava (_stored_plan_for_price): 'pro' é o
# alias LEGADO do tier Plus (R$ 19,90) e 'pro_max' é o tier Pro. Gravar 'plus'
# daria o mesmo tier no plan_service, mas furaria as queries que ainda casam o
# literal 'pro' (engagement_scheduler) — por isso ele não é oferecido aqui.
ADMIN_PLAN_VALUES: tuple[str, ...] = ("free", "essencial", "pro", "pro_max")
ADMIN_PLAN_MONTHS_MAX = 120


def set_account_plan(
    plan: str,
    months: int = 12,
    *,
    user_id: int | None = None,
    email: str | None = None,
) -> dict | None:
    """Grava plano + validade de UMA conta. Síncrono: chame por asyncio.to_thread.

    Fonte única do que "mudar de plano pelo admin" significa — o /admin/grant-pro
    e o botão do painel de usuários passam os dois por aqui. Descer para 'free'
    zera a validade; qualquer plano pago põe now() + months.

    NÃO fala com a Stripe: se a conta tem assinatura viva, o próximo webhook
    dela volta a mandar no par (plan, plan_expires_at). É grant/ajuste manual.

    Devolve a linha atualizada, ou None se a conta não existe. Entrada inválida
    levanta ValueError.
    """
    from db.connection import get_conn

    plan = (plan or "").strip().lower()
    if plan not in ADMIN_PLAN_VALUES:
        raise ValueError("plano inválido (use " + ", ".join(ADMIN_PLAN_VALUES) + ").")
    if (user_id is None) == (email is None):
        raise ValueError("informe user_id OU email.")
    if plan == "free":
        months = 1                       # ignorado pelo CASE; só tipa o parâmetro
    elif not 1 <= int(months) <= ADMIN_PLAN_MONTHS_MAX:
        raise ValueError(f"months deve estar entre 1 e {ADMIN_PLAN_MONTHS_MAX}.")

    if user_id is not None:
        where, target = "user_id = %(target)s", int(user_id)
    else:
        where, target = "lower(email) = lower(%(target)s)", (email or "").strip()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                update auth_accounts
                   set plan = %(plan)s,
                       plan_expires_at = case
                           when %(plan)s::text = 'free' then null
                           else now() + (%(months)s || ' months')::interval
                       end,
                       -- Plano pago sobre ex-assinante: status terminal
                       -- (canceled/unpaid) viraria 'Cancelado' no painel de
                       -- usuários mesmo com o plano vigente. Status em curso
                       -- (active/trialing/past_due) fica intacto: a relação
                       -- Stripe segue viva e os webhooks continuam donos dele.
                       -- Descer para 'free' não mexe: 'canceled' num
                       -- ex-assinante é a descrição correta.
                       last_payment_status = case
                           when %(plan)s::text <> 'free'
                                and lower(coalesce(last_payment_status, ''))
                                    in ('canceled', 'incomplete_expired', 'unpaid')
                           then 'inactive'
                           else last_payment_status
                       end
                 where {where}
                returning user_id, email, plan, plan_expires_at, last_payment_status
                """,
                {"plan": plan, "months": int(months), "target": target},
            )
            row = cur.fetchone()
        conn.commit()
        return dict(row) if row else None


async def log_admin_startup_warnings() -> None:
    # Coleta todos os avisos primeiro, depois faz UM único insert em batch.
    # Antes eram N chamadas sequenciais a log_system_event(), cada uma abrindo
    # sua própria conexão ao banco — em dev isso custava ~2s desnecessários.
    warnings: list[tuple[str, str, str]] = []

    if not (os.getenv("WA_TOKEN") or os.getenv("WA_ACCESS_TOKEN")):
        warnings.append(("warning", "whatsapp_config_missing", "WA token nao configurado no ambiente."))
    if not os.getenv("WA_PHONE_NUMBER_ID"):
        warnings.append(("warning", "whatsapp_config_missing", "WA_PHONE_NUMBER_ID nao configurado no ambiente."))
    if not os.getenv("WA_VERIFY_TOKEN"):
        warnings.append(("warning", "whatsapp_config_missing", "WA_VERIFY_TOKEN nao configurado no ambiente."))
    if not os.getenv("WA_APP_SECRET"):
        warnings.append(("warning", "whatsapp_config_missing", "WA_APP_SECRET nao configurado no ambiente."))
    if not (STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET and _STRIPE_PRICE_CONFIGURED):
        warnings.append(("warning", "billing_config_incomplete", "Configuracao do Stripe incompleta no ambiente."))

    if not warnings:
        return

    try:
        async with await db_connect() as conn:
            async with conn.cursor() as cur:
                await cur.executemany(
                    """
                    INSERT INTO system_event_logs (level, event_type, message, source, details)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    [(lvl, evt, msg, "startup", Jsonb({})) for lvl, evt, msg in warnings],
                )
            await conn.commit()
    except Exception as exc:
        print(f"[admin] failed to record startup warnings: {exc}", file=sys.stderr)


async def admin_error_logging_middleware(request: Request, call_next):
    try:
        return await call_next(request)
    except HTTPException:
        raise
    except Exception as exc:
        import traceback
        tb_str = traceback.format_exc()
        err_str = str(exc) or exc.__class__.__name__

        # Imprime o traceback completo no stdout pra aparecer nos logs do Railway.
        print(
            f"[unhandled] {request.method} {request.url.path}: {exc.__class__.__name__}: {err_str}\n{tb_str}",
            file=sys.stderr,
            flush=True,
        )

        # detecta timeout de banco → 503 Service Unavailable
        is_timeout = any(kw in err_str.lower() for kw in ("timeout", "connection", "could not connect"))
        status_code = 503 if is_timeout else 500
        user_msg = (
            "Serviço temporariamente indisponível. Tente novamente em instantes."
            if is_timeout
            else "Erro interno do servidor."
        )

        await log_system_event(
            "error",
            "http_unhandled_exception",
            err_str,
            source=f"{request.method} {request.url.path}",
            details={
                "query": dict(request.query_params),
                "status_code": status_code,
                "exc_type": exc.__class__.__name__,
                "traceback": tb_str[-2000:],
            },
        )

        # Navegação (Accept: text/html) recebe página; API segue no JSON de hoje.
        # Import dentro da função de propósito: core/ não importa frontend/ no
        # topo (shared.py roda load_app_env() no import) e este é caminho frio.
        # Qualquer falha aqui cai no JSONResponse — o pior caso é o de hoje.
        try:
            from frontend.routes.shared import wants_html, error_page_response
            if wants_html(request):
                return error_page_response(status_code)
        except Exception:
            pass

        # Vary inline (não pelo helper): se o import acima falhou, o helper também
        # não existe — e este ramo tem que sair de pé de qualquer jeito.
        return JSONResponse(
            status_code=status_code,
            content={"error": user_msg},
            headers={"Vary": "Accept"},
        )


def register_admin_routes(app: FastAPI, frontend_dir: Path, jwt_secret: str, limiter) -> None:
    async def _get_current_admin(
        request: Request,
        creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    ) -> str:
        return await get_current_admin(jwt_secret, request, creds)

    @app.post("/admin/auth/login")
    @limiter.limit("10/minute")
    async def admin_auth_login(request: Request, response: Response):
        # Lê o body manualmente para evitar incompatibilidade entre slowapi +
        # FastAPI 0.100+ + Pydantic v2 ao resolver parâmetros de body dentro de
        # funções de registro de rotas (register_admin_routes).
        if not admin_enabled():
            raise HTTPException(status_code=503, detail="Painel admin não configurado.")

        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Corpo da requisição inválido.")

        username = str(payload.get("username") or "").strip()
        password = str(payload.get("password") or "")

        if not username or not password:
            raise HTTPException(status_code=422, detail="Usuário e senha são obrigatórios.")

        if username != ADMIN_DASHBOARD_USERNAME:
            await log_system_event(
                "warning",
                "admin_login_failed",
                "Tentativa de login admin com usuário inválido.",
                source="admin_auth",
                details={"username": username, "ip": get_remote_address(request)},
            )
            raise HTTPException(status_code=401, detail="Credenciais inválidas.")

        if not _check_admin_password(password):
            await log_system_event(
                "warning",
                "admin_login_failed",
                "Tentativa de login admin com senha inválida.",
                source="admin_auth",
                details={"username": username, "ip": get_remote_address(request)},
            )
            raise HTTPException(status_code=401, detail="Credenciais inválidas.")

        token = _make_admin_jwt(ADMIN_DASHBOARD_USERNAME, jwt_secret)
        _set_admin_cookie(response, token)
        await log_system_event(
            "info",
            "admin_login_success",
            "Login admin realizado com sucesso.",
            source="admin_auth",
            details={"username": ADMIN_DASHBOARD_USERNAME, "ip": get_remote_address(request)},
        )
        return {
            "token": token,
            "username": ADMIN_DASHBOARD_USERNAME,
            "expires_in": int(ADMIN_DASHBOARD_SESSION_HOURS * 3600),
        }

    @app.get("/admin/auth/me")
    async def admin_auth_me(username: str = Depends(_get_current_admin)):
        return {"username": username}

    @app.get("/admin/grant-pro")
    async def admin_grant_pro(
        email: str,
        months: int = 12,
        username: str = Depends(_get_current_admin),
    ):
        """Concede plano Pro a uma conta (por e-mail) por N meses. Uso: conta
        demo do review da Apple / grants manuais.
        Ex.: /admin/grant-pro?email=teste@teste.com

        A escrita é a mesma do painel de usuários (set_account_plan) — 'pro' é
        o alias legado do tier Plus."""
        try:
            row = await asyncio.to_thread(set_account_plan, "pro", months, email=email)
        except ValueError as exc:
            return {"ok": False, "error": str(exc), "email": email}
        if not row:
            return {"ok": False, "error": "conta não encontrada", "email": email}
        return {
            "ok": True,
            "user_id": row["user_id"],
            "email": row["email"],
            "plan": row["plan"],
            "plan_expires_at": str(row["plan_expires_at"]),
        }

    @app.get("/admin/push-test")
    async def admin_push_test(
        user_id: int,
        send: int = 1,
        username: str = Depends(_get_current_admin),
    ):
        """Diagnóstico de push: mostra tokens do usuário e (send=1) dispara um
        push de teste. Roda no serviço Dashboard, onde vivem as envs APNS_*.
        Ex.: /admin/push-test?user_id=88648360  (add &send=0 pra só conferir)."""
        import asyncio

        from db import list_push_tokens
        from core.services import push_service

        tokens = await asyncio.to_thread(list_push_tokens, user_id)
        result = {
            "user_id": user_id,
            "tokens": [
                {
                    "suffix": t["token"][-8:],
                    "platform": t.get("platform"),
                    "environment": t.get("environment"),
                }
                for t in tokens
            ],
            "token_count": len(tokens),
            "apns_configured": push_service.is_configured(),
            "sent": None,
        }
        if send and tokens and push_service.is_configured():
            result["sent"] = await asyncio.to_thread(
                push_service.send_push_to_user,
                user_id,
                "PigBank",
                "Teste de notificação 🐷 — se você recebeu, o push está funcionando!",
                collapse_id="push-test",
            )
        return result

    @app.post("/admin/auth/logout")
    async def admin_auth_logout(response: Response):
        _clear_admin_cookie(response)
        return {"ok": True}

    @app.get("/admin/api/overview")
    async def admin_api_overview(days: int = 30, username: str = Depends(_get_current_admin)):
        data = await fetch_admin_overview(days=days, admin_user=username)
        data["admin_user"] = username
        return JSONResponse(content=_json_safe(data))

    @app.get("/admin/api/users")
    async def admin_api_users(
        q: str = "",
        plan: str = "",
        status: str = "",
        page: int = 0,
        per_page: int = 50,
        sort: str = "created_at",
        username: str = Depends(_get_current_admin),
    ):
        """Painel de usuários: lista paginada + agregados de assinatura + resumo
        Stripe (MRR/ticket médio). Decrypts auditados num único batch."""
        audit = pii_audit_batch()
        audit.__enter__()
        try:
            data = await fetch_admin_users(
                q=q, plan=plan, status=status, page=page,
                per_page=per_page, sort=sort, admin_user=username,
            )
        finally:
            audit.__exit__(None, None, None)
        return JSONResponse(content=_json_safe(data))

    @app.get("/admin/api/users/{user_id}")
    async def admin_api_user_detail(
        user_id: int,
        username: str = Depends(_get_current_admin),
    ):
        audit = pii_audit_batch()
        audit.__enter__()
        try:
            data = await fetch_admin_user_detail(user_id, admin_user=username)
        finally:
            audit.__exit__(None, None, None)
        if data is None:
            raise HTTPException(status_code=404, detail="Conta não encontrada.")
        return JSONResponse(content=_json_safe(data))

    @app.post("/admin/api/users/{user_id}/plan")
    async def admin_api_user_set_plan(
        user_id: int, request: Request, username: str = Depends(_get_current_admin)
    ):
        """Body: {"plan": "free"|"essencial"|"pro"|"pro_max", "months": 12}.

        Upgrade/downgrade manual pelo painel de usuários. Mexe só no banco: a
        assinatura na Stripe continua onde estava e o próximo webhook dela
        volta a mandar no plano — por isso o painel avisa quando a conta tem
        stripe_customer_id. Os gates que leem get_plan_tier passam a valer na
        hora; o que já existe acima do teto do plano novo (bancos de Open
        Finance, agentes) cai na varredura de downgrade, que roda a cada 6h.
        """
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Corpo da requisição inválido.")
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Corpo da requisição inválido.")
        months = payload.get("months")
        try:
            months = 12 if months is None else int(months)
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="months inválido.")
        try:
            row = await asyncio.to_thread(
                set_account_plan, str(payload.get("plan") or ""), months, user_id=user_id
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        if not row:
            raise HTTPException(status_code=404, detail="Conta não encontrada.")
        await log_system_event(
            "info",
            "admin_plan_changed",
            f"Plano da conta {user_id} → {row['plan']} (admin {username}).",
            source="admin",
            user_id=int(user_id),
            details={
                "plan": row["plan"],
                "months": None if row["plan"] == "free" else months,
                "expires_at": _json_safe(row["plan_expires_at"]),
                "admin": username,
            },
        )
        return JSONResponse(content=_json_safe({
            "ok": True,
            "user_id": row["user_id"],
            "plan": row["plan"],
            "plan_expires_at": row["plan_expires_at"],
            "last_payment_status": row["last_payment_status"],
        }))

    @app.delete("/admin/api/events/{event_id}")
    async def admin_delete_event(event_id: int, username: str = Depends(_get_current_admin)):
        async with await db_connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM system_event_logs WHERE id = %s RETURNING id",
                    (event_id,),
                )
                deleted = await cur.fetchone()
            await conn.commit()
        if not deleted:
            raise HTTPException(status_code=404, detail="Evento não encontrado.")
        return {"deleted": event_id}

    @app.delete("/admin/api/events")
    async def admin_bulk_delete_events(
        level: str | None = None,
        before_id: int | None = None,
        username: str = Depends(_get_current_admin),
    ):
        """Bulk delete em system_event_logs. Filtros opcionais combinam com AND:
          - level: 'error' | 'warning' | 'info' (sem filtro = todos os níveis)
          - before_id: deleta eventos com id <= before_id (snapshot do client)

        Use sem filtros pra apagar tudo, ou só `before_id` pra "limpar tudo até este momento".
        """
        clauses, params = [], []
        if level in ("error", "warning", "info"):
            clauses.append("level = %s")
            params.append(level)
        if before_id is not None:
            clauses.append("id <= %s")
            params.append(int(before_id))
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

        async with await db_connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(f"DELETE FROM system_event_logs {where}", tuple(params))
                deleted = cur.rowcount
            await conn.commit()
        return {"deleted": int(deleted or 0), "filters": {"level": level, "before_id": before_id}}

    @app.get("/admin/api/pii-access")
    async def admin_pii_access_log(
        limit: int = 50,
        offset: int = 0,
        actor: str | None = None,
        subject_user_id: int | None = None,
        field: str | None = None,
        username: str = Depends(_get_current_admin),
    ):
        """Lista entries de pii_access_log com paginação e filtros."""
        limit = max(1, min(int(limit or 50), 200))
        offset = max(0, int(offset or 0))

        clauses, params = [], []
        if actor:
            clauses.append("actor ILIKE %s")
            params.append(f"%{actor}%")
        if subject_user_id is not None:
            clauses.append("subject_user_id = %s")
            params.append(int(subject_user_id))
        if field:
            clauses.append("field = %s")
            params.append(field)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

        async with await db_connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(f"SELECT count(*) AS n FROM pii_access_log {where}", tuple(params))
                total = int((await cur.fetchone())["n"])

                await cur.execute(
                    f"""
                    SELECT id, purpose, actor, subject_user_id, field, endpoint, extra, created_at
                    FROM pii_access_log
                    {where}
                    ORDER BY created_at DESC
                    LIMIT %s OFFSET %s
                    """,
                    tuple([*params, limit, offset]),
                )
                rows = [dict(r) for r in await cur.fetchall()]

        return JSONResponse(content=_json_safe({
            "total": total,
            "limit": limit,
            "offset": offset,
            "items": rows,
        }))

    # ── Programa de afiliados ────────────────────────────────────────────────
    def _decrypt_affiliate_row(row: dict, admin_user: str) -> dict:
        row = _decrypt_admin_row(dict(row), admin_user, "render_admin_affiliates")
        if row.get("pix_key_enc"):
            row["pix_key"] = _decrypt_admin_field(
                row["pix_key_enc"], None,
                purpose="render_admin_affiliates", admin_user=admin_user,
                user_id=int(row.get("user_id") or 0), field="pix_key",
            )
        row.pop("pix_key_enc", None)
        return row

    @app.get("/admin/api/affiliates")
    async def admin_affiliates_overview(username: str = Depends(_get_current_admin)):
        """Lista afiliados (com saldos) + pedidos de saque em aberto."""
        from db.affiliates import admin_list_affiliates, admin_list_payouts

        affiliates = await asyncio.to_thread(admin_list_affiliates)
        payouts = await asyncio.to_thread(admin_list_payouts, None, 100)
        return JSONResponse(content=_json_safe({
            "affiliates": [_decrypt_affiliate_row(r, username) for r in affiliates],
            "payouts": [_decrypt_affiliate_row(r, username) for r in payouts],
        }))

    @app.post("/admin/api/affiliates")
    async def admin_affiliate_create(request: Request, username: str = Depends(_get_current_admin)):
        """Cria um afiliado a partir do email de uma conta existente.
        Body: {"email": "...", "code": "OPCIONAL", "commission_bps": 1000}"""
        from db import find_user_id_by_email
        from db.affiliates import DEFAULT_COMMISSION_BPS, create_affiliate

        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Corpo da requisição inválido.")

        email = str(payload.get("email") or "").strip()
        if not email:
            raise HTTPException(status_code=422, detail="Informe o email da conta.")
        user_id = await asyncio.to_thread(find_user_id_by_email, email)
        if not user_id:
            raise HTTPException(status_code=404, detail="Nenhuma conta com esse email.")

        code = (payload.get("code") or None)
        bps = int(payload.get("commission_bps") or DEFAULT_COMMISSION_BPS)
        try:
            affiliate = await asyncio.to_thread(create_affiliate, int(user_id), code, bps)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        await log_system_event(
            "info",
            "affiliate_created",
            f"Afiliado criado pelo admin (code={affiliate['code']}).",
            source="affiliates",
            user_id=int(user_id),
            details={"affiliate_id": affiliate["id"], "commission_bps": bps},
        )
        return JSONResponse(content=_json_safe({"ok": True, "affiliate": {
            "id": affiliate["id"], "code": affiliate["code"],
            "status": affiliate["status"], "commission_bps": affiliate["commission_bps"],
        }}))

    @app.post("/admin/api/affiliates/{affiliate_id}/status")
    async def admin_affiliate_set_status(
        affiliate_id: int, request: Request, username: str = Depends(_get_current_admin)
    ):
        """Body: {"status": "active" | "disabled"}. Desativado para de acumular
        comissão nova; o saldo já acumulado continua sacável."""
        from db.affiliates import set_affiliate_status

        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Corpo da requisição inválido.")
        status = str(payload.get("status") or "")
        try:
            ok = await asyncio.to_thread(set_affiliate_status, affiliate_id, status)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        if not ok:
            raise HTTPException(status_code=404, detail="Afiliado não encontrado.")
        await log_system_event(
            "info",
            "affiliate_status_changed",
            f"Afiliado {affiliate_id} → {status}.",
            source="affiliates",
            details={"affiliate_id": affiliate_id, "status": status},
        )
        return {"ok": True, "status": status}

    @app.get("/admin/api/affiliates/payouts/{payout_id}/pix")
    async def admin_affiliate_payout_pix(
        payout_id: int, username: str = Depends(_get_current_admin)
    ):
        """Pix copia e cola + QR pra pagar este saque.

        Nenhuma chamada a banco: o BR Code é montado localmente com a chave do
        afiliado e o valor do saque já embutido — o admin escaneia e confirma,
        sem digitar chave nem valor (que é onde mora o erro).
        """
        from core.services.pix_brcode import build_pix_brcode, qr_svg_data_url
        from db.affiliates import get_payout

        row = await asyncio.to_thread(get_payout, payout_id)
        if not row:
            raise HTTPException(status_code=404, detail="Saque não encontrado.")

        payout = _decrypt_affiliate_row(row, username)  # registra o acesso à PII
        pix_key = (payout.get("pix_key") or "").strip()
        if not pix_key:
            raise HTTPException(
                status_code=422, detail="Este saque não tem chave Pix registrada."
            )
        try:
            brcode = build_pix_brcode(pix_key, amount_cents=int(payout["amount_cents"]))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

        return {
            "brcode": brcode,
            "qr": qr_svg_data_url(brcode),
            "amount_cents": int(payout["amount_cents"]),
            "pix_key": pix_key,
            "code": payout.get("code"),
        }

    @app.post("/admin/api/affiliates/payouts/{payout_id}/paid")
    async def admin_affiliate_payout_paid(
        payout_id: int, request: Request, username: str = Depends(_get_current_admin)
    ):
        """Marca o saque como pago (Pix já feito por fora). Body: {"note": "..."}"""
        from db.affiliates import mark_payout_paid

        try:
            payload = await request.json()
        except Exception:
            payload = {}
        note = (payload.get("note") or "").strip() or None
        ok = await asyncio.to_thread(mark_payout_paid, payout_id, note)
        if not ok:
            raise HTTPException(status_code=404, detail="Saque não encontrado ou já processado.")
        await log_system_event(
            "info",
            "affiliate_payout_paid",
            f"Saque {payout_id} marcado como pago.",
            source="affiliates",
            details={"payout_id": payout_id},
        )
        return {"ok": True}

    @app.post("/admin/api/affiliates/payouts/{payout_id}/reject")
    async def admin_affiliate_payout_reject(
        payout_id: int, request: Request, username: str = Depends(_get_current_admin)
    ):
        """Rejeita o saque e devolve as comissões pro saldo. Body: {"note": "..."}"""
        from db.affiliates import reject_payout

        try:
            payload = await request.json()
        except Exception:
            payload = {}
        note = (payload.get("note") or "").strip() or None
        ok = await asyncio.to_thread(reject_payout, payout_id, note)
        if not ok:
            raise HTTPException(status_code=404, detail="Saque não encontrado ou já processado.")
        await log_system_event(
            "warning",
            "affiliate_payout_rejected",
            f"Saque {payout_id} rejeitado.",
            source="affiliates",
            details={"payout_id": payout_id, "note": note},
        )
        return {"ok": True}

    @app.post("/admin/api/affiliates/commissions/{commission_id}/reverse")
    async def admin_affiliate_commission_reverse(
        commission_id: int, username: str = Depends(_get_current_admin)
    ):
        """Estorna uma comissão (fatura reembolsada/chargeback no Stripe)."""
        from db.affiliates import reverse_commission

        ok = await asyncio.to_thread(reverse_commission, commission_id)
        if not ok:
            raise HTTPException(
                status_code=409,
                detail="Comissão não encontrada, já paga ou dentro de um saque em andamento.",
            )
        await log_system_event(
            "warning",
            "affiliate_commission_reversed",
            f"Comissão {commission_id} estornada.",
            source="affiliates",
            details={"commission_id": commission_id},
        )
        return {"ok": True}

    @app.get("/admin")
    async def serve_admin_dashboard(request: Request):
        try:
            _resolve_admin_username(jwt_secret, request)
        except HTTPException:
            return RedirectResponse("/admin/login", status_code=303)
        return FileResponse(
            frontend_dir / "admin-dashboard.html",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/admin/login")
    async def serve_admin_login():
        return FileResponse(
            frontend_dir / "admin-login.html",
            headers={"Cache-Control": "no-store"},
        )
