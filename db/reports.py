"""
db/reports.py — Relatório diário, auth, dashboard e engajamento.

As funções delegam para db_support para manter lógica de negócio isolada.
"""
import db_support as _db_support

from utils_phone import normalize_phone_e164, phone_lookup_candidates

from .connection import get_conn
from .users import (
    ensure_user,
    get_or_create_canonical_user,
    create_link_code,
    merge_users,
    get_or_create_canonical_user,
    _hash_password,
    _check_password,
)


# ──────────────────────────────────────────────────────────────────────────────
# Relatório diário
# ──────────────────────────────────────────────────────────────────────────────

def set_daily_report_enabled(user_id: int, enabled: bool) -> None:
    return _db_support.set_daily_report_enabled_impl(get_conn, ensure_user, user_id, enabled)


def set_daily_report_hour(user_id: int, hour: int, minute: int = 0) -> None:
    return _db_support.set_daily_report_hour_impl(get_conn, ensure_user, user_id, hour, minute)


def get_daily_report_prefs(user_id: int) -> dict:
    return _db_support.get_daily_report_prefs_impl(get_conn, ensure_user, user_id)


def list_users_with_daily_report_enabled(hour: int | None = None, minute: int | None = None) -> list[int]:
    return _db_support.list_users_with_daily_report_enabled_impl(get_conn, hour, minute)


def list_identities_by_user(user_id: int) -> list[dict]:
    return _db_support.list_identities_by_user_impl(get_conn, user_id)


def mark_daily_report_sent(user_id: int, sent_date) -> None:
    return _db_support.mark_daily_report_sent_impl(get_conn, ensure_user, user_id, sent_date)


def claim_daily_report_send(user_id: int, sent_date) -> bool:
    return _db_support.claim_daily_report_send_impl(get_conn, ensure_user, user_id, sent_date)


def was_daily_report_sent_today(user_id: int, today) -> bool:
    return _db_support.was_daily_report_sent_today_impl(get_conn, ensure_user, user_id, today)


def get_last_ofx_import_end_date(user_id: int):
    return _db_support.get_last_ofx_import_end_date_impl(get_conn, ensure_user, user_id)


# ──────────────────────────────────────────────────────────────────────────────
# Auth (email/senha)
# ──────────────────────────────────────────────────────────────────────────────

def register_auth_user(email: str, password: str) -> dict:
    return _db_support.register_auth_user_impl(
        get_conn, get_or_create_canonical_user, create_link_code, _hash_password, email, password
    )


def login_auth_user(email: str, password: str) -> dict | None:
    return _db_support.login_auth_user_impl(get_conn, _check_password, email, password)


def get_auth_user(user_id: int) -> dict | None:
    return _db_support.get_auth_user_impl(get_conn, user_id)


def auto_link_auth_user(target_user_id: int, current_user_id: int) -> int:
    if int(target_user_id) == int(current_user_id):
        return int(target_user_id)
    if get_auth_user(int(target_user_id)) is not None:
        return int(target_user_id)
    merge_users(int(current_user_id), int(target_user_id))
    return int(target_user_id)


# ──────────────────────────────────────────────────────────────────────────────
# Dashboard short links
# ──────────────────────────────────────────────────────────────────────────────

def create_dashboard_session(user_id: int, hours: float = 5 / 60) -> str:
    return _db_support.create_dashboard_session_impl(get_conn, user_id, hours)


def get_dashboard_session(code: str) -> int | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select user_id from dashboard_sessions where code = %s and expires_at > now()",
                (code,),
            )
            row = cur.fetchone()
    return row["user_id"] if row else None


def consume_dashboard_session(code: str) -> int | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "delete from dashboard_sessions where code = %s and expires_at > now() returning user_id",
                (code,),
            )
            row = cur.fetchone()
        conn.commit()
    return row["user_id"] if row else None


# ──────────────────────────────────────────────────────────────────────────────
# Billing / planos
# ──────────────────────────────────────────────────────────────────────────────

def update_user_plan(user_id: int, plan: str, expires_at=None) -> None:
    return _db_support.update_user_plan_impl(get_conn, user_id, plan, expires_at)


def get_user_by_stripe_customer(stripe_customer_id: str) -> int | None:
    return _db_support.get_user_by_stripe_customer_impl(get_conn, stripe_customer_id)


def set_stripe_customer(user_id: int, stripe_customer_id: str) -> None:
    return _db_support.set_stripe_customer_impl(get_conn, user_id, stripe_customer_id)


# ──────────────────────────────────────────────────────────────────────────────
# Verificação de email / reset de senha
# ──────────────────────────────────────────────────────────────────────────────

def create_email_verification(
    email: str,
    password: str,
    phone: str,
    minutes_valid: int = 15,
    display_name: str | None = None,
) -> str:
    phone_e164 = normalize_phone_e164(phone)
    return _db_support.create_email_verification_impl(
        get_conn, _hash_password, email, password, phone_e164, minutes_valid,
        display_name=display_name,
    )


def confirm_email_verification(email: str, code: str) -> dict:
    return _db_support.confirm_email_verification_impl(
        get_conn, get_or_create_canonical_user, create_link_code, email, code
    )


def attempt_whatsapp_phone_link(wa_id: str, current_user_id: int | None = None) -> dict:
    try:
        wa_phone = normalize_phone_e164(wa_id)
        wa_candidates = phone_lookup_candidates(wa_id)
    except ValueError:
        return {"status": "invalid_phone"}

    current_user_id = (
        int(current_user_id)
        if current_user_id is not None
        else get_or_create_canonical_user("whatsapp", wa_id)
    )

    # Delega toda a lógica de match/merge ao db_support
    return _db_support.attempt_whatsapp_phone_link_impl(
        get_conn, merge_users, wa_phone, wa_candidates, current_user_id
    )


def create_password_reset_token(email: str, minutes_valid: int = 30) -> str | None:
    return _db_support.create_password_reset_token_impl(get_conn, email, minutes_valid)


def consume_password_reset_token(token: str, new_password: str) -> bool:
    return _db_support.consume_password_reset_token_impl(get_conn, _hash_password, token, new_password)


# ──────────────────────────────────────────────────────────────────────────────
# Engajamento
# ──────────────────────────────────────────────────────────────────────────────

def update_last_activity(user_id: int) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE auth_accounts SET last_activity_at = now() WHERE user_id = %s",
                (user_id,),
            )
        conn.commit()


def get_users_for_engagement() -> list[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT user_id, email, last_activity_at, last_tip_sent_at,
                       last_insight_sent_at, last_reengagement_sent_at, engagement_opt_out,
                       tip_email_opt_out, insight_email_opt_out
                FROM auth_accounts
                WHERE engagement_opt_out = false
                ORDER BY last_activity_at DESC NULLS LAST
                """
            )
            return cur.fetchall()


def mark_reengagement_sent(user_id: int) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE auth_accounts SET last_reengagement_sent_at = now() WHERE user_id = %s",
                (user_id,),
            )
        conn.commit()


def mark_tip_sent(user_id: int) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE auth_accounts SET last_tip_sent_at = now() WHERE user_id = %s",
                (user_id,),
            )
        conn.commit()


def mark_insight_sent(user_id: int) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE auth_accounts SET last_insight_sent_at = now() WHERE user_id = %s",
                (user_id,),
            )
        conn.commit()


def set_engagement_opt_out(user_id: int, opt_out: bool) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE auth_accounts
                SET engagement_opt_out = %s,
                    tip_email_opt_out = %s,
                    insight_email_opt_out = %s
                WHERE user_id = %s
                """,
                (opt_out, opt_out, opt_out, user_id),
            )
        conn.commit()


def set_tip_email_opt_out(user_id: int, opt_out: bool) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE auth_accounts SET tip_email_opt_out = %s WHERE user_id = %s",
                (opt_out, user_id),
            )
        conn.commit()


def set_insight_email_opt_out(user_id: int, opt_out: bool) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE auth_accounts SET insight_email_opt_out = %s WHERE user_id = %s",
                (opt_out, user_id),
            )
        conn.commit()


def set_whatsapp_updates_opt_out(user_id: int, opt_out: bool) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE auth_accounts SET whatsapp_updates_opt_out = %s WHERE user_id = %s",
                (opt_out, user_id),
            )
        conn.commit()


def sync_engagement_opt_out(user_id: int) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE auth_accounts
                SET engagement_opt_out = tip_email_opt_out AND insight_email_opt_out
                WHERE user_id = %s
                """,
                (user_id,),
            )
        conn.commit()


def get_user_by_email(email: str) -> dict | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_id, email, engagement_opt_out FROM auth_accounts WHERE email = %s",
                (email,),
            )
            return cur.fetchone()
