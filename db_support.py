from __future__ import annotations

import copy
import logging
import os
import secrets
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

import psycopg

from utils_phone import normalize_phone_e164, phone_lookup_candidates

from core.crypto import (
    PiiAccessContext,
    decrypt_pii_optional,
    encrypt_pii_optional,
    hash_pii_optional,
)


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
        select id, tipo, valor, alvo, nota, categoria, source, criado_em, is_internal_movement
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
          and is_internal_movement = false
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


def set_daily_report_hour_impl(get_conn, ensure_user, user_id: int, hour: int, minute: int = 0) -> None:
    """Habilita o relatório diário e define o horário de envio."""
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into daily_report_prefs(user_id, enabled, hour, minute)
                values (%s, true, %s, %s)
                on conflict (user_id) do update
                  set enabled = true,
                      hour    = excluded.hour,
                      minute  = excluded.minute
                """,
                (user_id, hour, minute),
            )
        conn.commit()


def set_weekly_report_enabled_impl(get_conn, ensure_user, user_id: int, enabled: bool) -> None:
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into daily_report_prefs(user_id, weekly_enabled)
                values (%s, %s)
                on conflict (user_id) do update set weekly_enabled=excluded.weekly_enabled
                """,
                (user_id, enabled),
            )
        conn.commit()


def set_monthly_report_enabled_impl(get_conn, ensure_user, user_id: int, enabled: bool) -> None:
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into daily_report_prefs(user_id, monthly_enabled)
                values (%s, %s)
                on conflict (user_id) do update set monthly_enabled=excluded.monthly_enabled
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
                select enabled, hour, minute, weekly_enabled, monthly_enabled
                from daily_report_prefs
                where user_id=%s
                """,
                (user_id,),
            )
            row = cur.fetchone()
            if not row:
                return {"enabled": True, "hour": 9, "minute": 0, "weekly_enabled": True, "monthly_enabled": True}

            try:
                return {
                    "enabled": bool(row["enabled"]),
                    "hour": int(row["hour"]),
                    "minute": int(row["minute"]),
                    "weekly_enabled": bool(row["weekly_enabled"]),
                    "monthly_enabled": bool(row["monthly_enabled"]),
                }
            except Exception:
                return {
                    "enabled": bool(row[0]),
                    "hour": int(row[1]),
                    "minute": int(row[2]),
                    "weekly_enabled": bool(row[3]),
                    "monthly_enabled": bool(row[4]),
                }


def list_users_with_daily_report_enabled_impl(get_conn, hour: int | None = None, minute: int | None = None) -> list[int]:
    """
    Retorna IDs de todos os usuários com relatório diário habilitado.
    Se hour/minute forem passados, filtra também pelo horário configurado
    (usado pelo scheduler do Discord que roda em horário fixo).
    O loop do WhatsApp chama sem argumentos e faz a comparação de horário
    por conta própria, permitindo horários por usuário.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            if hour is not None and minute is not None:
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
            else:
                cur.execute(
                    """
                    select u.id
                    from users u
                    left join daily_report_prefs p on p.user_id=u.id
                    where coalesce(p.enabled, true) = true
                    order by u.id asc
                    """,
                )
            rows = cur.fetchall() or []
            out = []
            for r in rows:
                try:
                    out.append(int(r["id"]))
                except Exception:
                    out.append(int(r[0]))
            return out


def _list_users_with_flag_enabled(get_conn, column: str) -> list[int]:
    # column é controlado internamente (nunca vem do usuário) → seguro interpolar
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                select u.id
                from users u
                left join daily_report_prefs p on p.user_id=u.id
                where coalesce(p.{column}, true) = true
                order by u.id asc
                """,
            )
            rows = cur.fetchall() or []
            out = []
            for r in rows:
                try:
                    out.append(int(r["id"]))
                except Exception:
                    out.append(int(r[0]))
            return out


def list_users_with_weekly_report_enabled_impl(get_conn) -> list[int]:
    return _list_users_with_flag_enabled(get_conn, "weekly_enabled")


def list_users_with_monthly_report_enabled_impl(get_conn) -> list[int]:
    return _list_users_with_flag_enabled(get_conn, "monthly_enabled")


def list_identities_by_user_impl(get_conn, user_id: int) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select provider, external_id, external_id_enc
                from user_identities
                where user_id=%s
                """,
                (user_id,),
            )
            rows = cur.fetchall() or []
            out = []
            for r in rows:
                try:
                    provider = r["provider"]
                    enc = r.get("external_id_enc")
                    if enc:
                        ext = decrypt_pii_optional(
                            enc,
                            ctx=PiiAccessContext(
                                purpose="list_identities",
                                actor=f"user:{user_id}",
                                subject_user_id=user_id,
                                field=f"{provider}_id",
                            ),
                        )
                    else:
                        ext = r["external_id"]
                    out.append({"provider": provider, "external_id": ext})
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


def claim_daily_report_send_impl(get_conn, ensure_user, user_id: int, sent_date) -> bool:
    """
    Tenta reservar atomicamente o envio do report diário para `sent_date`.

    Retorna True apenas para o primeiro processo que conseguir gravar a data.
    Se outro processo já tiver reservado/enviado o mesmo dia, retorna False.
    """
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into daily_report_prefs(user_id, last_sent_date)
                values (%s, %s)
                on conflict (user_id) do update
                   set last_sent_date = excluded.last_sent_date
                 where daily_report_prefs.last_sent_date is distinct from excluded.last_sent_date
                returning user_id
                """,
                (user_id, sent_date),
            )
            row = cur.fetchone()
        conn.commit()
    return row is not None


def claim_weekly_report_send_impl(get_conn, ensure_user, user_id: int, period_date) -> bool:
    """Reserva atomicamente o envio do resumo semanal para `period_date` (a segunda-feira da semana).

    Retorna True apenas para o primeiro processo que gravar a data — evita
    envio duplicado quando há múltiplas instâncias/reinícios.
    """
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into daily_report_prefs(user_id, last_weekly_sent_date)
                values (%s, %s)
                on conflict (user_id) do update
                   set last_weekly_sent_date = excluded.last_weekly_sent_date
                 where daily_report_prefs.last_weekly_sent_date is distinct from excluded.last_weekly_sent_date
                returning user_id
                """,
                (user_id, period_date),
            )
            row = cur.fetchone()
        conn.commit()
    return row is not None


def claim_monthly_report_send_impl(get_conn, ensure_user, user_id: int, period_date) -> bool:
    """Reserva atomicamente o envio do resumo mensal para `period_date` (o dia 1 do mês).

    Mesma lógica do semanal: só o primeiro processo consegue gravar a data.
    """
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into daily_report_prefs(user_id, last_monthly_sent_date)
                values (%s, %s)
                on conflict (user_id) do update
                   set last_monthly_sent_date = excluded.last_monthly_sent_date
                 where daily_report_prefs.last_monthly_sent_date is distinct from excluded.last_monthly_sent_date
                returning user_id
                """,
                (user_id, period_date),
            )
            row = cur.fetchone()
        conn.commit()
    return row is not None


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
            cur.execute(
                "select user_id from auth_accounts where email_hash=%s",
                (hash_pii_optional(email, kind="email"),),
            )
            if cur.fetchone():
                raise ValueError("Este e-mail já está cadastrado.")

    user_id = get_or_create_canonical_user("email", email)
    password_hash = hash_password(password)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into auth_accounts (user_id, email, password_hash,
                                           email_hash, email_enc)
                values (%s, %s, %s, %s, %s)
                on conflict (email) do nothing
                """,
                (user_id, email, password_hash,
                 hash_pii_optional(email, kind="email"),
                 encrypt_pii_optional(email)),
            )
        conn.commit()
    invalidate_auth_user_cache(user_id)

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
                "select user_id, password_hash, plan, plan_expires_at, phone_e164, phone_status from auth_accounts where email_hash=%s",
                (hash_pii_optional(email, kind="email"),),
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


# ─── Cache TTL do get_auth_user ──────────────────────────────────────────────
# Uma abertura de dashboard lia auth_accounts ~17× (4×/auth/me, 4×/history,
# 4×/dashboard-profile, 4×/data frio, 2×/expenses/daily). TTL curto por
# user_id + invalidação nos writers de auth_accounts — mesmo desenho do
# dashboard_current_cache (frontend/routes/shared.py).
#
# Exceções DELIBERADAS de invalidação (caminho quente, colunas que o SELECT
# do get_auth_user não serve — invalidar ali zeraria o hit rate a cada
# mensagem): db/reports.py::update_last_activity e
# db/ai_chat.py::get_usage_this_month/increment_usage. Se alguma dessas
# colunas entrar no SELECT abaixo, a exceção dela cai junto.
# Cross-process (bot.py do Discord): sem invalidação remota; o TTL de 10 s é
# o teto do atraso.
AUTH_USER_CACHE_TTL_SECONDS = 10
# Teto de entradas: o dict guarda PII decifrada (email/telefone/nome) e sem
# teto uma entrada expirada só saía da memória quando o MESMO usuário fosse
# consultado de novo. No insert acima do teto: poda expirados; se ainda
# estourar, despeja o mais antigo. Dois limites aceitos de propósito: a poda
# só roda NO INSERT (processo ocioso retém até 512 entradas expiradas até a
# próxima leitura), e a ordem de despejo é por timestamp de INSERT, não de
# uso — com TTL de 10 s, despejar um usuário ativo custa ≤10 s de cache.
# ponytail: varredura O(n) no teto (512) — ordenar por timestamp se o teto
# crescer 10x; poda por tempo (task periódica) se o ocioso incomodar.
AUTH_USER_CACHE_MAX = 512
_auth_user_cache: dict[int, tuple[float, dict | None]] = {}
# get_auth_user roda em THREADS (dezenas de `asyncio.to_thread(get_auth_user,
# ...)` no monólito), então poda+evict+insert — que ITERAM o dict — podem
# correr com um insert/clear de outra thread e levantar "dictionary changed
# size during iteration" numa request de auth legítima. O lock protege só as
# seções COMPOSTAS; o lookup segue fora dele (dict.get é atômico sob o GIL e
# o caminho quente não paga contenção).
_auth_user_cache_lock = threading.Lock()


def invalidate_auth_user_cache(user_id: int | None = None) -> None:
    """Chame após QUALQUER escrita em auth_accounts (None = limpa tudo)."""
    with _auth_user_cache_lock:
        if user_id is None:
            _auth_user_cache.clear()
        else:
            _auth_user_cache.pop(int(user_id), None)


def get_auth_user_impl(get_conn, user_id: int) -> dict | None:
    hit = _auth_user_cache.get(int(user_id))
    if hit and time.monotonic() - hit[0] < AUTH_USER_CACHE_TTL_SECONDS:
        # deepcopy nos dois sentidos: caller que mutar o dict não pode
        # envenenar o cache (plan/plan_expires_at decidem acesso pago).
        return copy.deepcopy(hit[1])
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select email, email_enc, display_name, display_name_enc,
                       plan, plan_expires_at, created_at, phone_e164, phone_enc,
                       phone_status, phone_confirmed_at, whatsapp_verified_at,
                       engagement_opt_out, tip_email_opt_out, insight_email_opt_out,
                       whatsapp_updates_opt_out, stripe_customer_id, last_payment_status,
                       trial_started_at, plan_selected_at
                from auth_accounts
                where user_id=%s
                """,
                (user_id,),
            )
            row = cur.fetchone()
    if not row:
        return None

    # Decifra PII (cai pro claro se _enc ausente — defensivo durante transição)
    if row.get("email_enc"):
        row["email"] = decrypt_pii_optional(
            row["email_enc"],
            ctx=PiiAccessContext(purpose="get_auth_user", actor=f"user:{user_id}",
                                 subject_user_id=user_id, field="email"),
        )
    if row.get("phone_enc"):
        row["phone_e164"] = decrypt_pii_optional(
            row["phone_enc"],
            ctx=PiiAccessContext(purpose="get_auth_user", actor=f"user:{user_id}",
                                 subject_user_id=user_id, field="phone"),
        )
    if row.get("display_name_enc"):
        row["display_name"] = decrypt_pii_optional(
            row["display_name_enc"],
            ctx=PiiAccessContext(purpose="get_auth_user", actor=f"user:{user_id}",
                                 subject_user_id=user_id, field="name"),
        )
    now = time.monotonic()
    with _auth_user_cache_lock:  # poda+evict ITERAM o dict: ver comentário do lock
        if len(_auth_user_cache) >= AUTH_USER_CACHE_MAX:
            cutoff = now - AUTH_USER_CACHE_TTL_SECONDS
            for uid in [u for u, (ts, _) in _auth_user_cache.items() if ts < cutoff]:
                _auth_user_cache.pop(uid, None)
            while len(_auth_user_cache) >= AUTH_USER_CACHE_MAX:
                oldest = min(_auth_user_cache, key=lambda u: _auth_user_cache[u][0])
                _auth_user_cache.pop(oldest, None)
        _auth_user_cache[int(user_id)] = (now, copy.deepcopy(row))
    return row


def create_dashboard_session_impl(get_conn, user_id: int, hours: float = 2) -> str:
    expires_at = datetime.now(timezone.utc) + timedelta(hours=hours)

    with get_conn() as conn:
        for _ in range(5):
            code = secrets.token_urlsafe(24)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "insert into dashboard_sessions (code, user_id, expires_at) values (%s, %s, %s)",
                        (code, user_id, expires_at),
                    )
                conn.commit()
                return code
            except psycopg.errors.UniqueViolation:
                conn.rollback()
                continue
            except Exception:
                conn.rollback()
                raise

    raise RuntimeError("Falha ao criar sessão temporária do dashboard.")


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
    invalidate_auth_user_cache(user_id)


def mark_plan_selected_impl(get_conn, user_id: int) -> None:
    """Marca que o usuário já escolheu um plano no cadastro (Grátis ou pago),
    liberando o acesso ao dashboard. Idempotente: só grava na primeira vez
    (where plan_selected_at is null) pra preservar o timestamp original."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update auth_accounts set plan_selected_at = now() "
                "where user_id = %s and plan_selected_at is null",
                (user_id,),
            )
        conn.commit()
    invalidate_auth_user_cache(user_id)


def set_payment_status_impl(get_conn, user_id: int, status: str) -> None:
    """
    Atualiza last_payment_status. Valores esperados (alinhados com o ciclo do
    Stripe Subscription): inactive, trialing, active, past_due, canceled, unpaid.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update auth_accounts set last_payment_status = %s where user_id = %s",
                (status, user_id),
            )
        conn.commit()
    invalidate_auth_user_cache(user_id)


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
    invalidate_auth_user_cache(user_id)


class AccountAlreadyExistsError(Exception):
    """Cadastro tentado com e-mail/telefone que já pertence a uma conta.

    Carrega o `existing_user_id` pra que o endpoint avise o dono da conta por
    e-mail (out-of-band) e responda de forma GENÉRICA — sem revelar ao visitante
    que a conta existe (anti-enumeração). `reason` ∈ {email, email_google, phone}.
    """
    def __init__(self, reason: str, existing_user_id: int | None = None):
        super().__init__(reason)
        self.reason = reason
        self.existing_user_id = existing_user_id


def create_email_verification_impl(
    get_conn,
    hash_password,
    email: str,
    password: str,
    phone_e164: str,
    minutes_valid: int = 15,
    display_name: str | None = None,
) -> str:
    email = email.strip().lower()
    normalized_phone = normalize_phone_e164(phone_e164)
    phone_candidates = phone_lookup_candidates(normalized_phone)
    display_name = (display_name or "").strip() or None

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select user_id, password_hash from auth_accounts where email_hash = %s",
                (hash_pii_optional(email, kind="email"),),
            )
            existing = cur.fetchone()
            if existing:
                # Anti-enumeração: não vaza "já existe" pro visitante. O endpoint
                # trata AccountAlreadyExistsError respondendo genericamente e
                # avisando o dono por e-mail.
                reason = "email_google" if existing["password_hash"] is None else "email"
                raise AccountAlreadyExistsError(reason, existing_user_id=existing["user_id"])
            _phone_hashes = [hash_pii_optional(c, kind="phone") for c in phone_candidates if c]
            cur.execute("select user_id from auth_accounts where phone_hash = any(%s)", (_phone_hashes,))
            phone_row = cur.fetchone()
            if phone_row:
                # Telefone já em uso por outra conta. NÃO revela isso ao
                # cadastrante: se a gente parasse aqui (ou não mandasse o código),
                # a presença/ausência do e-mail de verificação enumeraria números
                # de WhatsApp (o cadastrante controla o e-mail submetido). Em vez
                # disso segue o fluxo normal — manda o código pro e-mail dele — e
                # apenas DESCARTA o telefone disputado: a conta nasce sem WhatsApp
                # vinculado (dá pra vincular outro número depois). A colisão de
                # telefone fica indistinguível até o e-mail ser verificado.
                normalized_phone = None

    password_hash = hash_password(password)
    # Código de verificação precisa ser imprevisível (brute-force de 6 dígitos):
    # secrets (CSPRNG) em vez de random (Mersenne Twister, previsível).
    code = f"{secrets.randbelow(1_000_000):06d}"
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=minutes_valid)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update email_verification_codes set used_at = now() where email_hash = %s and used_at is null",
                (hash_pii_optional(email, kind="email"),),
            )
            cur.execute(
                """
                insert into email_verification_codes
                  (email, code, password_hash, phone_e164, display_name, expires_at,
                   email_hash, email_enc, phone_hash, phone_enc, display_name_enc)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (email, code, password_hash, normalized_phone, display_name, expires_at,
                 hash_pii_optional(email, kind="email"),
                 encrypt_pii_optional(email),
                 hash_pii_optional(normalized_phone, kind="phone"),
                 encrypt_pii_optional(normalized_phone),
                 encrypt_pii_optional(display_name)),
            )
        conn.commit()

    return code


def confirm_email_verification_impl(
    get_conn,
    get_or_create_canonical_user,
    create_link_code,
    email: str,
    code: str,
    source: str = "web",
) -> dict:
    email = email.strip().lower()
    now = datetime.now(timezone.utc)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, password_hash, phone_e164, display_name, expires_at, used_at
                from email_verification_codes
                where email_hash = %s and code = %s
                order by created_at desc
                limit 1
                """,
                (hash_pii_optional(email, kind="email"), code),
            )
            row = cur.fetchone()

    if not row:
        raise ValueError("Código inválido. Verifique e tente novamente.")
    if row["used_at"] is not None:
        raise ValueError("Este código já foi utilizado. Faça o cadastro novamente.")
    if row["expires_at"] < now:
        raise ValueError("Código expirado. Faça o cadastro novamente.")

    password_hash = row["password_hash"]
    # phone pode ser NULL: cadastro com número já em uso descarta o telefone
    # (anti-enumeração) e cria a conta sem WhatsApp vinculado.
    phone_e164 = normalize_phone_e164(row["phone_e164"]) if row["phone_e164"] else None
    display_name = (row.get("display_name") or "").strip() or None
    verification_id = row["id"]
    user_id = get_or_create_canonical_user("email", email)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into auth_accounts
                  (user_id, email, password_hash, phone_e164, display_name, phone_status,
                   email_hash, email_enc, phone_hash, phone_enc, display_name_enc, signup_source)
                values (%s, %s, %s, %s, %s, 'pending', %s, %s, %s, %s, %s, %s)
                on conflict (email) do update
                set user_id = excluded.user_id,
                    password_hash = excluded.password_hash,
                    phone_e164 = coalesce(auth_accounts.phone_e164, excluded.phone_e164),
                    display_name = coalesce(auth_accounts.display_name, excluded.display_name),
                    phone_status = case
                        when auth_accounts.phone_e164 is null and excluded.phone_e164 is not null then 'pending'
                        else auth_accounts.phone_status
                    end,
                    email_hash = coalesce(auth_accounts.email_hash, excluded.email_hash),
                    email_enc = coalesce(auth_accounts.email_enc, excluded.email_enc),
                    phone_hash = coalesce(auth_accounts.phone_hash, excluded.phone_hash),
                    phone_enc = coalesce(auth_accounts.phone_enc, excluded.phone_enc),
                    display_name_enc = coalesce(auth_accounts.display_name_enc, excluded.display_name_enc),
                    -- Preserva a origem da 1ª criação em re-registro do mesmo e-mail
                    signup_source = coalesce(auth_accounts.signup_source, excluded.signup_source)
                """,
                (user_id, email, password_hash, phone_e164, display_name,
                 hash_pii_optional(email, kind="email"),
                 encrypt_pii_optional(email),
                 hash_pii_optional(phone_e164, kind="phone"),
                 encrypt_pii_optional(phone_e164),
                 encrypt_pii_optional(display_name),
                 source),
            )
            cur.execute(
                "update email_verification_codes set used_at = now() where id = %s",
                (verification_id,),
            )
        conn.commit()
    invalidate_auth_user_cache(user_id)

    link_code = create_link_code(user_id, minutes_valid=15)

    try:
        from core.services.email_service import send_welcome_email

        dashboard_url = os.getenv("DASHBOARD_URL", "")
        send_welcome_email(email, link_code, dashboard_url)
    except Exception as exc:
        logging.getLogger(__name__).warning("Falha ao enviar email de boas-vindas para <%s>: %s", email, exc)

    return {"user_id": user_id, "link_code": link_code}


def attempt_whatsapp_phone_link_impl(
    get_conn,
    merge_users,
    wa_phone: str,
    wa_candidates: list[str],
    current_user_id: int,
) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            wa_hashes = [hash_pii_optional(c, kind="phone") for c in wa_candidates if c]
            cur.execute(
                """
                select user_id, phone_e164
                from auth_accounts
                where phone_hash = any(%s)
                """,
                (wa_hashes,),
            )
            matches = cur.fetchall() or []

            cur.execute(
                """
                select external_id
                from user_identities
                where provider = 'whatsapp' and user_id = %s
                limit 1
                """,
                (current_user_id,),
            )
            existing_current_wa = cur.fetchone()

    if not matches:
        return {"status": "no_match", "wa_phone": wa_phone}

    if len(matches) > 1:
        return {"status": "multiple_accounts", "wa_phone": wa_phone}

    target_user_id = int(matches[0]["user_id"])

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select external_id, external_id_enc
                from user_identities
                where provider = 'whatsapp' and user_id = %s
                limit 1
                """,
                (target_user_id,),
            )
            target_wa = cur.fetchone()

            if target_wa:
                if target_wa.get("external_id_enc"):
                    target_wa_id = decrypt_pii_optional(
                        target_wa["external_id_enc"],
                        ctx=PiiAccessContext(
                            purpose="whatsapp_link_check",
                            actor="system:whatsapp_link",
                            subject_user_id=target_user_id,
                            field="whatsapp_id",
                        ),
                    )
                else:
                    target_wa_id = target_wa["external_id"]
            else:
                target_wa_id = None
            same_phone_alias = False
            if target_wa_id:
                try:
                    target_candidates = set(phone_lookup_candidates(target_wa_id))
                except ValueError:
                    target_candidates = {target_wa_id}
                same_phone_alias = bool(target_candidates.intersection(set(wa_candidates)))

            if target_wa and target_wa_id != wa_phone and not same_phone_alias:
                return {
                    "status": "account_has_other_whatsapp",
                    "wa_phone": wa_phone,
                }

    final_user_id = target_user_id
    if int(current_user_id) != target_user_id:
        merge_users(int(current_user_id), target_user_id)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into user_identities (provider, external_id, user_id,
                                              external_id_hash, external_id_enc)
                values ('whatsapp', %s, %s, %s, %s)
                on conflict (provider, external_id)
                do update set user_id = excluded.user_id,
                              external_id_hash = coalesce(excluded.external_id_hash, user_identities.external_id_hash),
                              external_id_enc = coalesce(excluded.external_id_enc, user_identities.external_id_enc)
                """,
                (wa_phone, target_user_id,
                 hash_pii_optional(wa_phone, kind="external_id"),
                 encrypt_pii_optional(wa_phone)),
            )
            cur.execute(
                """
                update auth_accounts
                set phone_status = 'confirmed',
                    phone_confirmed_at = coalesce(phone_confirmed_at, now()),
                    whatsapp_verified_at = now()
                where user_id = %s
                """,
                (target_user_id,),
            )
        conn.commit()
    invalidate_auth_user_cache(target_user_id)

    return {
        "status": "already_linked" if current_user_id == target_user_id and existing_current_wa else "linked",
        "user_id": int(final_user_id),
        "wa_phone": wa_phone,
    }


def create_password_reset_token_impl(get_conn, email: str, minutes_valid: int = 30) -> str | None:
    email = email.strip().lower()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select user_id from auth_accounts where email_hash = %s",
                (hash_pii_optional(email, kind="email"),),
            )
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


def consume_password_reset_token_impl(get_conn, hash_password, token: str, new_password: str) -> int | None:
    """
    Consome o token de reset e atualiza a senha. Retorna o user_id (truthy) em
    sucesso ou None em falha (token invalido/expirado/ja usado).
    Callers continuam podendo usar `if ok:` graças à truthiness de int positivo.
    """
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
        return None
    if row["used_at"] is not None:
        return None
    if row["expires_at"] < now:
        return None

    user_id = row["user_id"]
    new_hash = hash_password(new_password)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                # password_changed_at: invalida tokens legados sem jti emitidos
                # antes do reset (os com jti já são revogados via sessão).
                "update auth_accounts set password_hash = %s, password_changed_at = %s where user_id = %s",
                (new_hash, now, user_id),
            )
            cur.execute(
                "update password_reset_tokens set used_at = %s where token = %s",
                (now, token),
            )
        conn.commit()
    invalidate_auth_user_cache(user_id)

    return user_id


def get_password_changed_at_impl(get_conn, user_id: int):
    """Timestamp do último reset de senha (ou None). Usado pra invalidar tokens
    legados sem jti emitidos antes do reset."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select password_changed_at from auth_accounts where user_id = %s",
                (int(user_id),),
            )
            row = cur.fetchone()
    return row["password_changed_at"] if row else None
