"""
db/google_auth.py — Login social (Google e Apple). O nome e as tabelas vêm do
Google; a Apple usa as mesmas funções com `provider=PROVIDER_APPLE`.

Estrutura:
  auth_identities          → vínculo permanente (user_id ↔ provider, provider_sub)
  pending_google_signups   → pré-cadastro: aguarda nome+telefone do usuário
"""
import secrets
from datetime import datetime, timedelta, timezone

from utils_phone import normalize_phone_e164, phone_lookup_candidates

from core.crypto import encrypt_pii_optional, hash_pii_optional
from core.pg_text import tem_veneno

from .connection import get_conn
from .users import create_link_code, get_or_create_canonical_user


PROVIDER_GOOGLE = "google"
PROVIDER_APPLE = "apple"
PENDING_SIGNUP_TTL_MINUTES = 30

# Os dois começam com "Cadastro expirado": é o que o app usa para voltar ao
# formulário de entrada.
_CADASTRO_EXPIRADO = {
    PROVIDER_GOOGLE: "Cadastro expirado. Inicie novamente o login com Google.",
    PROVIDER_APPLE: "Cadastro expirado. Entre com a Apple de novo.",
}


# ──────────────────────────────────────────────────────────────────────────────
# Lookups
# ──────────────────────────────────────────────────────────────────────────────

def find_user_by_google_sub(sub: str, provider: str = PROVIDER_GOOGLE) -> int | None:
    """Retorna user_id se já existe um vínculo (provider, sub)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select user_id from auth_identities where provider=%s and provider_sub=%s",
            (provider, sub),
        )
        row = cur.fetchone()
    return int(row["user_id"]) if row else None


def find_user_id_by_email(email: str) -> int | None:
    email = (email or "").strip().lower()
    if not email:
        return None
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select user_id from auth_accounts where email_hash=%s",
            (hash_pii_optional(email, kind="email"),),
        )
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


def conta_sem_credencial(user_id: int) -> bool:
    """True se a conta não tem senha (`''` conta como sem, igual a
    `auth_account_has_password`) NEM identidade Google/Apple: só entra pelo link
    do e-mail. Dois usos: o gate do PR 4 (`password_required` em
    `frontend/routes/shared.py::exigir_credencial`, `precisa_criar_senha` do
    /auth/me, guarda do bot) e a espera do e-book do PR 3
    (`core/services/ebook_entrega.py`, só entrega depois da prova do e-mail).
    Sem linha em auth_accounts → False: o só-WhatsApp não tem linha e o bot não
    pode bloqueá-lo; o e-book não entrega nesse caso porque não acha e-mail.
    Erro de banco sobe (é segurança, não UX)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select 1 from auth_accounts a
            where a.user_id = %s and (a.password_hash is null or a.password_hash = '')
              and not exists (select 1 from auth_identities i where i.user_id = a.user_id)
            """,
            (int(user_id),),
        )
        return cur.fetchone() is not None


def email_has_password(email: str) -> bool:
    """True se existe conta com senha para este email."""
    email = (email or "").strip().lower()
    if not email:
        return False
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select 1 from auth_accounts where email_hash=%s and password_hash is not null",
            (hash_pii_optional(email, kind="email"),),
        )
        return cur.fetchone() is not None


# ──────────────────────────────────────────────────────────────────────────────
# Vinculação de identidade Google a uma conta existente
# ──────────────────────────────────────────────────────────────────────────────

def link_google_identity(
    user_id: int, sub: str, email: str, provider: str = PROVIDER_GOOGLE
) -> None:
    email = (email or "").strip().lower() or None
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into auth_identities (user_id, provider, provider_sub,
                                              email, email_enc)
                values (%s, %s, %s, %s, %s)
                on conflict (provider, provider_sub) do update
                set user_id = excluded.user_id,
                    email = coalesce(excluded.email, auth_identities.email),
                    email_enc = coalesce(excluded.email_enc, auth_identities.email_enc)
                """,
                (int(user_id), provider, sub, email,
                 encrypt_pii_optional(email)),
            )
        conn.commit()


# ──────────────────────────────────────────────────────────────────────────────
# Pre-cadastro: usuário novo, aguarda nome+telefone
# ──────────────────────────────────────────────────────────────────────────────

def create_pending_google_signup(
    sub: str, email: str, name_hint: str | None, provider: str = PROVIDER_GOOGLE
) -> str:
    """Cria registro pendente e devolve token de uso único (URL-safe)."""
    email = (email or "").strip().lower()
    name_hint = (name_hint or "").strip() or None
    token = f"gso_{secrets.token_urlsafe(24)}"
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=PENDING_SIGNUP_TTL_MINUTES)

    with get_conn() as conn:
        with conn.cursor() as cur:
            # invalida pendentes anteriores do mesmo sub pra evitar acúmulo, e
            # herda o nome deles: a Apple manda o nome só na 1ª autorização, e
            # "Voltar" seguido de tocar na Apple de novo não pode apagá-lo.
            # ponytail: só herda se a limpeza ainda não podou o pendente anterior;
            # depois disso a pessoa digita o nome.
            cur.execute(
                "delete from pending_google_signups where provider=%s and provider_sub=%s"
                " returning name_hint",
                (provider, sub),
            )
            name_hint = name_hint or next(
                (r["name_hint"] for r in cur.fetchall() if r["name_hint"]), None
            )
            cur.execute(
                """
                insert into pending_google_signups
                  (token, provider, provider_sub, email, name_hint, expires_at,
                   email_hash, email_enc, name_hint_enc)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (token, provider, sub, email, name_hint, expires_at,
                 hash_pii_optional(email, kind="email"),
                 encrypt_pii_optional(email),
                 encrypt_pii_optional(name_hint)),
            )
        conn.commit()

    return token


def get_pending_google_signup(token: str, provider: str = PROVIDER_GOOGLE) -> dict | None:
    # Idem `consume_data_export_token`: o token vem do path da rota ANÔNIMA
    # `/auth/google/pending/{token}` e envenenado dava 500 em vez do 404 de
    # "cadastro expirado ou inválido" (#321). Guarda aqui, não na rota, porque
    # `consume_pending_google_signup` também passa por esta função.
    if not token or tem_veneno(token):
        return None
    now = datetime.now(timezone.utc)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select provider_sub, email, name_hint, expires_at
            from pending_google_signups
            where token = %s and provider = %s
            """,
            (token, provider),
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
    source: str = "google",
    provider: str = PROVIDER_GOOGLE,
) -> dict:
    """
    Finaliza o cadastro: cria auth_account (sem senha), grava auth_identities
    e devolve {user_id, email, link_code}.

    Lança ValueError com mensagem amigável se algo falhar.
    """
    pending = get_pending_google_signup(token, provider)
    if not pending:
        raise ValueError(_CADASTRO_EXPIRADO[provider])

    # O e-mail ganhou conta depois do pendente (outra aba, outro provedor): o
    # `on conflict (email)` abaixo fundiria o cadastro nela e a rota abriria
    # sessão sem MFA. Recomeçar leva ao vínculo por e-mail, que passa pelo
    # `_concluir_login`. A corrida entre esta busca e o insert é recusada lá
    # embaixo, pelo `inserir_conta_nova`.
    if find_user_id_by_email(pending["email"]):
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("delete from pending_google_signups where token = %s", (token,))
            conn.commit()
        raise ValueError(_CADASTRO_EXPIRADO[provider])

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

    # Telefone de outra conta: descarta em silêncio e segue, sem dizer "em uso"
    # (enumeraria números de WhatsApp) — igual ao `create_email_verification_impl`.
    # A conta nasce sem WhatsApp e vincula depois pelo `whatsapp_link` (#585).
    with get_conn() as conn, conn.cursor() as cur:
        phone_hashes = [hash_pii_optional(c, kind="phone") for c in phone_candidates if c]
        cur.execute(
            "select user_id from auth_accounts where phone_hash = any(%s)",
            (phone_hashes,),
        )
        if cur.fetchone():
            normalized_phone = None

    # user_id determinístico baseado no email — bate com create_email_verification
    user_id = get_or_create_canonical_user("email", email)

    # Corrida (outra conta grava o número depois da busca): o helper desfaz e
    # grava sem telefone. `conn` é o do `with` logo abaixo.
    def _gravar(normalized_phone):
        with conn.cursor() as cur:
            if not inserir_conta_nova(cur, user_id=user_id, email=email, password_hash=None,
                                      phone_e164=normalized_phone, display_name=name, source=source):
                raise ValueError(_CADASTRO_EXPIRADO[provider])
            cur.execute(
                """
                insert into auth_identities (user_id, provider, provider_sub,
                                              email, email_enc)
                values (%s, %s, %s, %s, %s)
                on conflict (provider, provider_sub) do update
                set user_id = excluded.user_id,
                    email = excluded.email,
                    email_enc = excluded.email_enc
                """,
                (user_id, provider, sub, email,
                 encrypt_pii_optional(email)),
            )
            cur.execute("delete from pending_google_signups where token = %s", (token,))

    from db_support import (
        gravar_descartando_telefone_disputado, inserir_conta_nova, invalidate_auth_user_cache,
    )
    with get_conn() as conn:
        gravar_descartando_telefone_disputado(conn, _gravar, normalized_phone)
        conn.commit()
    invalidate_auth_user_cache(user_id)

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
