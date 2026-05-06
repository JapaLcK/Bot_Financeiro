"""
db/google_auth.py — Login social (Google OAuth).

Estrutura:
  auth_identities          → vínculo permanente (user_id ↔ provider, provider_sub)
  pending_google_signups   → pré-cadastro: aguarda nome+telefone do usuário
"""
import secrets
from datetime import datetime, timedelta, timezone

from utils_phone import normalize_phone_e164, phone_lookup_candidates

from .connection import get_conn
from .users import create_link_code, get_or_create_canonical_user


PROVIDER_GOOGLE = "google"
PENDING_SIGNUP_TTL_MINUTES = 30


# ──────────────────────────────────────────────────────────────────────────────
# Lookups
# ──────────────────────────────────────────────────────────────────────────────

def find_user_by_google_sub(sub: str) -> int | None:
    """Retorna user_id se já existe um vínculo (provider=google, sub=...)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select user_id from auth_identities where provider=%s and provider_sub=%s",
            (PROVIDER_GOOGLE, sub),
        )
        row = cur.fetchone()
    return int(row["user_id"]) if row else None


def find_user_id_by_email(email: str) -> int | None:
    email = (email or "").strip().lower()
    if not email:
        return None
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select user_id from auth_accounts where email=%s", (email,))
        row = cur.fetchone()
    return int(row["user_id"]) if row else None


def auth_account_has_password(user_id: int) -> bool:
    """True se a conta tem senha. False se foi criada só via OAuth."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select password_hash from auth_accounts where user_id=%s",
            (int(user_id),),
        )
        row = cur.fetchone()
    return bool(row and row["password_hash"])


def email_has_password(email: str) -> bool:
    """True se existe conta com senha para este email."""
    email = (email or "").strip().lower()
    if not email:
        return False
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select 1 from auth_accounts where email=%s and password_hash is not null",
            (email,),
        )
        return cur.fetchone() is not None


# ──────────────────────────────────────────────────────────────────────────────
# Vinculação de identidade Google a uma conta existente
# ──────────────────────────────────────────────────────────────────────────────

def link_google_identity(user_id: int, sub: str, email: str) -> None:
    email = (email or "").strip().lower() or None
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into auth_identities (user_id, provider, provider_sub, email)
                values (%s, %s, %s, %s)
                on conflict (provider, provider_sub) do update
                set user_id = excluded.user_id,
                    email = coalesce(excluded.email, auth_identities.email)
                """,
                (int(user_id), PROVIDER_GOOGLE, sub, email),
            )
        conn.commit()


# ──────────────────────────────────────────────────────────────────────────────
# Pre-cadastro: usuário novo, aguarda nome+telefone
# ──────────────────────────────────────────────────────────────────────────────

def create_pending_google_signup(sub: str, email: str, name_hint: str | None) -> str:
    """Cria registro pendente e devolve token de uso único (URL-safe)."""
    email = (email or "").strip().lower()
    name_hint = (name_hint or "").strip() or None
    token = f"gso_{secrets.token_urlsafe(24)}"
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=PENDING_SIGNUP_TTL_MINUTES)

    with get_conn() as conn:
        with conn.cursor() as cur:
            # invalida pendentes anteriores do mesmo sub pra evitar acúmulo
            cur.execute(
                "delete from pending_google_signups where provider=%s and provider_sub=%s",
                (PROVIDER_GOOGLE, sub),
            )
            cur.execute(
                """
                insert into pending_google_signups
                  (token, provider, provider_sub, email, name_hint, expires_at)
                values (%s, %s, %s, %s, %s, %s)
                """,
                (token, PROVIDER_GOOGLE, sub, email, name_hint, expires_at),
            )
        conn.commit()

    return token


def get_pending_google_signup(token: str) -> dict | None:
    if not token:
        return None
    now = datetime.now(timezone.utc)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select provider_sub, email, name_hint, expires_at
            from pending_google_signups
            where token = %s
            """,
            (token,),
        )
        row = cur.fetchone()
    if not row or row["expires_at"] < now:
        return None
    return {
        "provider_sub": row["provider_sub"],
        "email": row["email"],
        "name_hint": row["name_hint"],
    }


def consume_pending_google_signup(
    token: str,
    name: str,
    phone_raw: str,
) -> dict:
    """
    Finaliza o cadastro: cria auth_account (sem senha), grava auth_identities
    e devolve {user_id, email, link_code}.

    Lança ValueError com mensagem amigável se algo falhar.
    """
    pending = get_pending_google_signup(token)
    if not pending:
        raise ValueError("Cadastro expirado. Inicie novamente o login com Google.")

    name = (name or "").strip()
    if len(name) < 2 or len(name) > 50:
        raise ValueError("O nome deve ter entre 2 e 50 caracteres.")

    try:
        normalized_phone = normalize_phone_e164(phone_raw)
    except ValueError as e:
        raise ValueError(str(e))
    phone_candidates = phone_lookup_candidates(normalized_phone)

    email = pending["email"]
    sub = pending["provider_sub"]

    # Verifica colisão de telefone com outras contas
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select user_id from auth_accounts where phone_e164 = any(%s)",
            (phone_candidates,),
        )
        if cur.fetchone():
            raise ValueError("Este número de WhatsApp já está em uso por outra conta.")

    # user_id determinístico baseado no email — bate com create_email_verification
    user_id = get_or_create_canonical_user("email", email)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into auth_accounts
                  (user_id, email, password_hash, phone_e164, display_name, phone_status)
                values (%s, %s, NULL, %s, %s, 'pending')
                on conflict (email) do update
                set phone_e164 = coalesce(auth_accounts.phone_e164, excluded.phone_e164),
                    display_name = coalesce(auth_accounts.display_name, excluded.display_name)
                """,
                (user_id, email, normalized_phone, name),
            )
            cur.execute(
                """
                insert into auth_identities (user_id, provider, provider_sub, email)
                values (%s, %s, %s, %s)
                on conflict (provider, provider_sub) do update
                set user_id = excluded.user_id, email = excluded.email
                """,
                (user_id, PROVIDER_GOOGLE, sub, email),
            )
            cur.execute("delete from pending_google_signups where token = %s", (token,))
        conn.commit()

    link_code = create_link_code(user_id, minutes_valid=15)

    return {"user_id": user_id, "email": email, "link_code": link_code}


def cleanup_expired_pending_signups() -> int:
    """Remove pendências expiradas (chamável por job de manutenção)."""
    now = datetime.now(timezone.utc)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "delete from pending_google_signups where expires_at < %s",
                (now,),
            )
            removed = cur.rowcount
        conn.commit()
    return int(removed or 0)
