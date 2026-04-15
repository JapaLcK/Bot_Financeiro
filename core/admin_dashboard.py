from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import bcrypt
import jwt as pyjwt
import psycopg
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel
from slowapi.util import get_remote_address

from config.env import load_app_env


load_app_env()

DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()
WHATSAPP_NUMBER = os.getenv("WHATSAPP_NUMBER", "")
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_ID_PRO = os.getenv("STRIPE_PRICE_ID_PRO", "")
ADMIN_DASHBOARD_USERNAME = (os.getenv("ADMIN_DASHBOARD_USERNAME") or "admin").strip()
ADMIN_DASHBOARD_PASSWORD = os.getenv("ADMIN_DASHBOARD_PASSWORD", "")
ADMIN_DASHBOARD_PASSWORD_HASH = os.getenv("ADMIN_DASHBOARD_PASSWORD_HASH", "")
ADMIN_DASHBOARD_SESSION_HOURS = float(os.getenv("ADMIN_DASHBOARD_SESSION_HOURS", "12"))

_bearer = HTTPBearer(auto_error=False)


async def db_connect():
    return await psycopg.AsyncConnection.connect(DATABASE_URL, row_factory=dict_row)


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
        async with await db_connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO auth_login_events (
                        user_id, email, success, ip_address, user_agent, failure_reason
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (user_id, (email or "").strip().lower() or None, success, ip_address, user_agent, failure_reason),
                )
            await conn.commit()
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


def _check_admin_password(password: str) -> bool:
    if ADMIN_DASHBOARD_PASSWORD_HASH:
        try:
            return bcrypt.checkpw(
                password.encode("utf-8"),
                ADMIN_DASHBOARD_PASSWORD_HASH.encode("utf-8"),
            )
        except Exception:
            return False
    return bool(ADMIN_DASHBOARD_PASSWORD) and password == ADMIN_DASHBOARD_PASSWORD


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


async def get_current_admin(
    jwt_secret: str,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    if not admin_enabled():
        raise HTTPException(status_code=503, detail="Painel admin não configurado.")
    if not creds:
        raise HTTPException(status_code=401, detail="Token admin não fornecido.")
    payload = _decode_jwt(creds.credentials, jwt_secret)
    if not payload or payload.get("type") != "admin":
        raise HTTPException(status_code=401, detail="Token admin inválido ou expirado.")
    return str(payload["sub"])


class AdminLoginBody(BaseModel):
    username: str
    password: str


async def fetch_admin_overview(days: int = 30) -> dict[str, Any]:
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
                WHERE criado_em >= NOW() - INTERVAL '6 months'
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
                GROUP BY a.user_id, a.email, a.created_at, a.last_activity_at
                ORDER BY total_transactions DESC, a.created_at DESC
                LIMIT 10
                """,
                (start_30d,),
            )
            top_users = [dict(row) for row in await cur.fetchall()]

            await cur.execute(
                """
                SELECT
                    a.user_id,
                    a.email,
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
            recent_signups = [dict(row) for row in await cur.fetchall()]

            await cur.execute(
                """
                SELECT email, user_id, success, failure_reason, ip_address, created_at
                FROM auth_login_events
                ORDER BY created_at DESC
                LIMIT 20
                """
            )
            recent_logins = [dict(row) for row in await cur.fetchall()]

            await cur.execute(
                """
                SELECT level, event_type, message, source, user_id, created_at
                FROM system_event_logs
                ORDER BY created_at DESC
                LIMIT 20
                """
            )
            recent_errors = [dict(row) for row in await cur.fetchall()]

            await cur.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours' AND level = 'error') AS backend_errors_24h,
                    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours' AND event_type = 'whatsapp_webhook_received') AS whatsapp_webhooks_24h,
                    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours' AND event_type = 'whatsapp_send_success') AS whatsapp_send_success_24h,
                    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours' AND event_type IN ('whatsapp_send_failed', 'whatsapp_send_exception')) AS whatsapp_send_failures_24h,
                    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days' AND event_type = 'whatsapp_token_invalid') AS whatsapp_token_invalid_7d,
                    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours' AND event_type = 'whatsapp_queue_drop') AS whatsapp_queue_drop_24h,
                    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days' AND event_type = 'whatsapp_worker_error') AS whatsapp_worker_errors_7d,
                    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days' AND event_type = 'billing_signature_invalid') AS billing_signature_invalid_7d,
                    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days' AND event_type = 'billing_payment_failed') AS billing_payment_failed_7d,
                    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days' AND event_type = 'engagement_loop_error') AS engagement_errors_7d,
                    MAX(created_at) FILTER (WHERE event_type = 'whatsapp_webhook_received') AS last_whatsapp_webhook_at,
                    MAX(created_at) FILTER (WHERE event_type = 'whatsapp_send_success') AS last_whatsapp_send_success_at,
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
                SELECT level, event_type, message, source, user_id, created_at
                FROM system_event_logs
                WHERE event_type LIKE 'whatsapp_%'
                   OR event_type LIKE 'billing_%'
                   OR event_type LIKE 'engagement_%'
                   OR event_type = 'http_unhandled_exception'
                ORDER BY created_at DESC
                LIMIT 25
                """
            )
            recent_ops = [dict(row) for row in await cur.fetchall()]

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

    return {
        "generated_at": now,
        "window_days": days,
        "summary": summary,
        "ops_summary": {
            **ops_summary,
            **whatsapp_activity,
            "wa_token_configured": bool(os.getenv("WA_TOKEN") or os.getenv("WA_ACCESS_TOKEN")),
            "wa_number_configured": bool(WHATSAPP_NUMBER),
            "wa_phone_number_id_configured": bool(os.getenv("WA_PHONE_NUMBER_ID")),
            "wa_verify_token_configured": bool(os.getenv("WA_VERIFY_TOKEN")),
            "wa_app_secret_configured": bool(os.getenv("WA_APP_SECRET")),
            "stripe_configured": bool(STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET and STRIPE_PRICE_ID_PRO),
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
    }


async def log_admin_startup_warnings() -> None:
    if not (os.getenv("WA_TOKEN") or os.getenv("WA_ACCESS_TOKEN")):
        await log_system_event(
            "warning",
            "whatsapp_config_missing",
            "WA token nao configurado no ambiente.",
            source="startup",
        )
    if not os.getenv("WA_PHONE_NUMBER_ID"):
        await log_system_event(
            "warning",
            "whatsapp_config_missing",
            "WA_PHONE_NUMBER_ID nao configurado no ambiente.",
            source="startup",
        )
    if not os.getenv("WA_VERIFY_TOKEN"):
        await log_system_event(
            "warning",
            "whatsapp_config_missing",
            "WA_VERIFY_TOKEN nao configurado no ambiente.",
            source="startup",
        )
    if not os.getenv("WA_APP_SECRET"):
        await log_system_event(
            "warning",
            "whatsapp_config_missing",
            "WA_APP_SECRET nao configurado no ambiente.",
            source="startup",
        )
    if not (STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET and STRIPE_PRICE_ID_PRO):
        await log_system_event(
            "warning",
            "billing_config_incomplete",
            "Configuracao do Stripe incompleta no ambiente.",
            source="startup",
        )


async def admin_error_logging_middleware(request: Request, call_next):
    try:
        return await call_next(request)
    except HTTPException:
        raise
    except Exception as exc:
        await log_system_event(
            "error",
            "http_unhandled_exception",
            str(exc),
            source=f"{request.method} {request.url.path}",
            details={"query": dict(request.query_params)},
        )
        raise


def register_admin_routes(app: FastAPI, frontend_dir: Path, jwt_secret: str, limiter) -> None:
    async def _get_current_admin(
        creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    ) -> str:
        return await get_current_admin(jwt_secret, creds)

    @app.post("/admin/auth/login")
    @limiter.limit("10/minute")
    async def admin_auth_login(request: Request, body: AdminLoginBody):
        if not admin_enabled():
            raise HTTPException(status_code=503, detail="Painel admin não configurado.")

        if body.username.strip() != ADMIN_DASHBOARD_USERNAME:
            await log_system_event(
                "warning",
                "admin_login_failed",
                "Tentativa de login admin com usuário inválido.",
                source="admin_auth",
                details={"username": body.username.strip(), "ip": get_remote_address(request)},
            )
            raise HTTPException(status_code=401, detail="Credenciais inválidas.")

        if not _check_admin_password(body.password):
            await log_system_event(
                "warning",
                "admin_login_failed",
                "Tentativa de login admin com senha inválida.",
                source="admin_auth",
                details={"username": body.username.strip(), "ip": get_remote_address(request)},
            )
            raise HTTPException(status_code=401, detail="Credenciais inválidas.")

        token = _make_admin_jwt(ADMIN_DASHBOARD_USERNAME, jwt_secret)
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

    @app.get("/admin/api/overview")
    async def admin_api_overview(days: int = 30, username: str = Depends(_get_current_admin)):
        data = await fetch_admin_overview(days=days)
        data["admin_user"] = username
        return JSONResponse(content=_json_safe(data))

    @app.get("/admin")
    async def serve_admin_dashboard():
        return FileResponse(frontend_dir / "admin-dashboard.html")

    @app.get("/admin/login")
    async def serve_admin_login():
        return FileResponse(frontend_dir / "admin-login.html")
