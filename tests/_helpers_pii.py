"""
tests/_helpers_pii.py — helpers pra inserir rows com PII dual-state
(claro + cifrado/hash) nos testes.

Antes da cifragem column-level, testes inseriam direto via SQL preenchendo
só as colunas em claro (`email`, `phone_e164`, `external_id`). Agora os
lookups da app usam `email_hash`, `phone_hash`, `external_id_hash` —
inserts incompletos viram orfãos invisíveis.

Use estas helpers em vez de SQL cru quando o teste precisa montar o estado
direto no banco (sem ir pelo signup real).
"""
from __future__ import annotations

from core.crypto import encrypt_pii_optional, hash_pii_optional


def insert_auth_account_pii(
    cur,
    user_id: int,
    email: str,
    *,
    phone: str | None = None,
    name: str | None = None,
    password_hash: str = "hash",
    plan: str = "free",
    phone_status: str = "pending",
) -> None:
    """Insere uma row em auth_accounts populando claro + hash + cifrado."""
    cur.execute(
        """
        insert into auth_accounts
          (user_id, email, password_hash, phone_e164, display_name, phone_status, plan,
           email_hash, email_enc, phone_hash, phone_enc, display_name_enc)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            user_id, email, password_hash, phone, name, phone_status, plan,
            hash_pii_optional(email, kind="email") if email else None,
            encrypt_pii_optional(email),
            hash_pii_optional(phone, kind="phone") if phone else None,
            encrypt_pii_optional(phone),
            encrypt_pii_optional(name),
        ),
    )


def bind_identity_pii(
    cur,
    provider: str,
    external_id: str,
    user_id: int,
) -> None:
    """Insere/atualiza uma row em user_identities populando claro + hash + cifrado.
    Usa ON CONFLICT (provider, external_id) — mesma semântica do bind_identity real."""
    cur.execute(
        """
        insert into user_identities
          (provider, external_id, user_id, external_id_hash, external_id_enc)
        values (%s, %s, %s, %s, %s)
        on conflict (provider, external_id) do update
        set user_id = excluded.user_id,
            external_id_hash = excluded.external_id_hash,
            external_id_enc = excluded.external_id_enc
        """,
        (
            provider, external_id, user_id,
            hash_pii_optional(external_id, kind="external_id"),
            encrypt_pii_optional(external_id),
        ),
    )
