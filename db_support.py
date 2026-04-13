from __future__ import annotations

import logging
import os
import random
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from utils_phone import normalize_phone_e164, phone_lookup_candidates


def get_launches_by_period_impl(
    get_conn: Callable[[], Any],
    ensure_user: Callable[[int], None],
    user_id: int,
    start_date: date,
    end_date: date,
):
    ensure_user(user_id)

    start_dt = datetime.combine(start_date, datetime.min.time())
    end_excl = datetime.combine(end_date + timedelta(days=1), datetime.min.time())

    sql = """
        select id, tipo, valor, alvo, nota, categoria, source, criado_em
        from launches
        where user_id=%s
        and criado_em >= %s
        and criado_em < %s
        order by criado_em asc, id asc
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (user_id, start_dt, end_excl))
            return cur.fetchall()


def get_summary_by_period_impl(
    get_conn: Callable[[], Any],
    ensure_user: Callable[[int], None],
    user_id: int,
    start_date: date,
    end_date: date,
):
    ensure_user(user_id)

    start_dt = datetime.combine(start_date, datetime.min.time())
    end_excl = datetime.combine(end_date + timedelta(days=1), datetime.min.time())

    sql = """
        select tipo, coalesce(sum(valor), 0) as total
        from launches
        where user_id=%s
          and criado_em >= %s
          and criado_em < %s
        group by tipo
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (user_id, start_dt, end_excl))
            rows = cur.fetchall()

    out = {"receita": 0.0, "despesa": 0.0, "aporte_investimento": 0.0}
    for row in rows:
        try:
            tipo = row["tipo"]
            total = row["total"]
        except Exception:
            tipo, total = row

        if tipo in out:
            out[tipo] = float(total or 0)

    return out


def set_daily_report_enabled_impl(get_conn, ensure_user, user_id: int, enabled: bool) -> None:
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into daily_report_prefs(user_id, enabled)
                values (%s, %s)
                on conflict (user_id) do update set enabled=excluded.enabled
                """,
                (user_id, enabled),
            )
        conn.commit()


def get_daily_report_prefs_impl(get_conn, ensure_user, user_id: int) -> dict:
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select enabled, hour, minute
                from daily_report_prefs
                where user_id=%s
                """,
                (user_id,),
            )
            row = cur.fetchone()
            if not row:
                return {"enabled": True, "hour": 9, "minute": 0}

            try:
                return {"enabled": bool(row["enabled"]), "hour": int(row["hour"]), "minute": int(row["minute"])}
            except Exception:
                return {"enabled": bool(row[0]), "hour": int(row[1]), "minute": int(row[2])}


def list_users_with_daily_report_enabled_impl(get_conn, hour: int = 9, minute: int = 0) -> list[int]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select u.id
                from users u
                left join daily_report_prefs p on p.user_id=u.id
                where coalesce(p.enabled, true) = true
                  and coalesce(p.hour, 9) = %s
                  and coalesce(p.minute, 0) = %s
                order by u.id asc
                """,
                (hour, minute),
            )
            rows = cur.fetchall() or []
            out = []
            for r in rows:
                try:
                    out.append(int(r["id"]))
                except Exception:
                    out.append(int(r[0]))
            return out


def list_identities_by_user_impl(get_conn, user_id: int) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select provider, external_id
                from user_identities
                where user_id=%s
                """,
                (user_id,),
            )
            rows = cur.fetchall() or []
            out = []
            for r in rows:
                try:
                    out.append({"provider": r["provider"], "external_id": r["external_id"]})
                except Exception:
                    out.append({"provider": r[0], "external_id": r[1]})
            return out


def mark_daily_report_sent_impl(get_conn, ensure_user, user_id: int, sent_date) -> None:
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into daily_report_prefs(user_id, last_sent_date)
                values (%s, %s)
                on conflict (user_id)
                do update set last_sent_date=excluded.last_sent_date
                """,
                (user_id, sent_date),
            )
        conn.commit()


def was_daily_report_sent_today_impl(get_conn, ensure_user, user_id: int, today) -> bool:
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select last_sent_date from daily_report_prefs where user_id=%s",
                (user_id,),
            )
            row = cur.fetchone()
            if not row:
                return False
            try:
                return row["last_sent_date"] == today
            except Exception:
                return row[0] == today


def get_last_ofx_import_end_date_impl(get_conn, ensure_user, user_id: int):
    ensure_user(user_id)
    sql = """
        select max(dt_end) as last_dt_end
        from ofx_imports
        where user_id = %s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (user_id,))
            row = cur.fetchone()
            if not row:
                return None

            try:
                return row["last_dt_end"]
            except Exception:
                return row[0]


def register_auth_user_impl(
    get_conn,
    get_or_create_canonical_user,
    create_link_code,
    hash_password,
    email: str,
    password: str,
) -> dict:
    email = email.strip().lower()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select user_id from auth_accounts where email=%s", (email,))
            if cur.fetchone():
                raise ValueError("Este e-mail já está cadastrado.")

    user_id = get_or_create_canonical_user("email", email)
    password_hash = hash_password(password)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into auth_accounts (user_id, email, password_hash)
                values (%s, %s, %s)
                on conflict (email) do nothing
                """,
                (user_id, email, password_hash),
            )
        conn.commit()

    link_code = create_link_code(user_id, minutes_valid=15)

    try:
        from core.services.email_service import send_welcome_email

        dashboard_url = os.getenv("DASHBOARD_URL", "")
        send_welcome_email(email, link_code, dashboard_url)
    except Exception as email_exc:
        logging.getLogger(__name__).warning(
            "Falha ao enviar e-mail de boas-vindas para <%s>: %s", email, email_exc
        )

    return {"user_id": user_id, "link_code": link_code}


def login_auth_user_impl(get_conn, check_password, email: str, password: str) -> dict | None:
    email = email.strip().lower()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select user_id, password_hash, plan, plan_expires_at, phone_e164, phone_status from auth_accounts where email=%s",
                (email,),
            )
            row = cur.fetchone()

    if not row:
        return None
    if not check_password(password, row["password_hash"]):
        return None

    return {
        "user_id": int(row["user_id"]),
        "email": email,
        "plan": row["plan"],
        "plan_expires_at": row["plan_expires_at"],
        "phone_e164": row["phone_e164"],
        "phone_status": row["phone_status"],
    }


def get_auth_user_impl(get_conn, user_id: int) -> dict | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select email, plan, plan_expires_at, created_at, phone_e164, phone_status, phone_confirmed_at, whatsapp_verified_at from auth_accounts where user_id=%s",
                (user_id,),
            )
            return cur.fetchone()


def create_dashboard_session_impl(get_conn, user_id: int, hours: int = 2) -> str:
    code = secrets.token_urlsafe(6)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=hours)

    with get_conn() as conn:
        with conn.cursor() as cur:
            for _ in range(3):
                try:
                    cur.execute(
                        "insert into dashboard_sessions (code, user_id, expires_at) values (%s, %s, %s)",
                        (code, user_id, expires_at),
                    )
                    break
                except Exception:
                    code = secrets.token_urlsafe(6)
        conn.commit()

    return code


def get_dashboard_session_impl(get_conn, code: str) -> int | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                delete from dashboard_sessions
                where code = %s and expires_at > now()
                returning user_id
                """,
                (code,),
            )
            row = cur.fetchone()
        conn.commit()
    return row["user_id"] if row else None


def update_user_plan_impl(get_conn, user_id: int, plan: str, expires_at=None) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update auth_accounts set plan = %s, plan_expires_at = %s where user_id = %s",
                (plan, expires_at, user_id),
            )
        conn.commit()


def get_user_by_stripe_customer_impl(get_conn, stripe_customer_id: str) -> int | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select user_id from auth_accounts where stripe_customer_id = %s",
                (stripe_customer_id,),
            )
            row = cur.fetchone()
    return row["user_id"] if row else None


def set_stripe_customer_impl(get_conn, user_id: int, stripe_customer_id: str) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update auth_accounts set stripe_customer_id = %s where user_id = %s",
                (stripe_customer_id, user_id),
            )
        conn.commit()


def create_email_verification_impl(
    get_conn,
    hash_password,
    email: str,
    password: str,
    phone_e164: str,
    minutes_valid: int = 15,
) -> str:
    email = email.strip().lower()
    normalized_phone = normalize_phone_e164(phone_e164)
    phone_candidates = phone_lookup_candidates(normalized_phone)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select user_id from auth_accounts where email = %s", (email,))
            if cur.fetchone():
                raise ValueError("Este e-mail já está cadastrado.")
            cur.execute("select user_id from auth_accounts where phone_e164 = any(%s)", (phone_candidates,))
            if cur.fetchone():
                raise ValueError("Este número de WhatsApp já está em uso por outra conta.")

    password_hash = hash_password(password)
    code = f"{random.randint(0, 999999):06d}"
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=minutes_valid)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update email_verification_codes set used_at = now() where email = %s and used_at is null",
                (email,),
            )
            cur.execute(
                """
                insert into email_verification_codes (email, code, password_hash, phone_e164, expires_at)
                values (%s, %s, %s, %s, %s)
                """,
                (email, code, password_hash, normalized_phone, expires_at),
            )
        conn.commit()

    return code


def confirm_email_verification_impl(
    get_conn,
    get_or_create_canonical_user,
    create_link_code,
    email: str,
    code: str,
) -> dict:
    email = email.strip().lower()
    now = datetime.now(timezone.utc)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, password_hash, phone_e164, expires_at, used_at
                from email_verification_codes
                where email = %s and code = %s
                order by created_at desc
                limit 1
                """,
                (email, code),
            )
            row = cur.fetchone()

    if not row:
        raise ValueError("Código inválido. Verifique e tente novamente.")
    if row["used_at"] is not None:
        raise ValueError("Este código já foi utilizado. Faça o cadastro novamente.")
    if row["expires_at"] < now:
        raise ValueError("Código expirado. Faça o cadastro novamente.")

    password_hash = row["password_hash"]
    phone_e164 = normalize_phone_e164(row["phone_e164"])
    verification_id = row["id"]
    user_id = get_or_create_canonical_user("email", email)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into auth_accounts (user_id, email, password_hash, phone_e164, phone_status)
                values (%s, %s, %s, %s, 'pending')
                on conflict (email) do update
                set user_id = excluded.user_id,
                    password_hash = excluded.password_hash,
                    phone_e164 = coalesce(auth_accounts.phone_e164, excluded.phone_e164),
                    phone_status = case
                        when auth_accounts.phone_e164 is null and excluded.phone_e164 is not null then 'pending'
                        else auth_accounts.phone_status
                    end
                """,
                (user_id, email, password_hash, phone_e164),
            )
            cur.execute(
                "update email_verification_codes set used_at = now() where id = %s",
                (verification_id,),
            )
        conn.commit()

    link_code = create_link_code(user_id, minutes_valid=15)

    try:
        from core.services.email_service import send_welcome_email

        dashboard_url = os.getenv("DASHBOARD_URL", "")
        send_welcome_email(email, link_code, dashboard_url)
    except Exception as exc:
        logging.getLogger(__name__).warning("Falha ao enviar email de boas-vindas para <%s>: %s", email, exc)

    return {"user_id": user_id, "link_code": link_code}


def create_password_reset_token_impl(get_conn, email: str, minutes_valid: int = 30) -> str | None:
    email = email.strip().lower()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select user_id from auth_accounts where email = %s", (email,))
            row = cur.fetchone()

    if not row:
        return None

    user_id = row["user_id"]
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=minutes_valid)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into password_reset_tokens (token, user_id, expires_at)
                values (%s, %s, %s)
                """,
                (token, user_id, expires_at),
            )
        conn.commit()

    return token


def consume_password_reset_token_impl(get_conn, hash_password, token: str, new_password: str) -> bool:
    now = datetime.now(timezone.utc)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select user_id, expires_at, used_at
                from password_reset_tokens
                where token = %s
                """,
                (token,),
            )
            row = cur.fetchone()

    if not row:
        return False
    if row["used_at"] is not None:
        return False
    if row["expires_at"] < now:
        return False

    user_id = row["user_id"]
    new_hash = hash_password(new_password)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update auth_accounts set password_hash = %s where user_id = %s",
                (new_hash, user_id),
            )
            cur.execute(
                "update password_reset_tokens set used_at = %s where token = %s",
                (now, token),
            )
        conn.commit()

    return True
