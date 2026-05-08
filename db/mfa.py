"""
db/mfa.py — Autenticacao em 2 etapas (TOTP).

Cobre:
- Gestao do segredo TOTP por usuario (criptografado at rest com Fernet).
- Codigos de backup (10 por geracao, hash bcrypt, single-use).
- Challenge token entre login (senha OK) e validacao TOTP.
- Verificacao de codigo TOTP com janela de tolerancia.

Variavel de ambiente:
- MFA_ENCRYPTION_KEY: chave Fernet (32 bytes urlsafe-base64). Sem ela,
  o modulo levanta no primeiro uso. Gere com:
  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import pyotp
from cryptography.fernet import Fernet, InvalidToken

from .connection import get_conn


_BACKUP_CODE_COUNT = 10
_BACKUP_CODE_LENGTH = 10  # caracteres (alfanumerico legivel)
_CHALLENGE_TTL_MINUTES = 5
_TOTP_WINDOW = 1  # tolera +/- 30s de skew


_fernet_cache: Fernet | None = None


def _get_fernet() -> Fernet:
    """Carrega Fernet a partir de MFA_ENCRYPTION_KEY (cacheado)."""
    global _fernet_cache
    if _fernet_cache is not None:
        return _fernet_cache
    raw = (os.getenv("MFA_ENCRYPTION_KEY") or "").strip()
    if not raw:
        raise RuntimeError(
            "MFA_ENCRYPTION_KEY nao configurada. Gere com:\n"
            "  python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\""
        )
    try:
        _fernet_cache = Fernet(raw.encode())
    except (ValueError, TypeError) as exc:
        raise RuntimeError(f"MFA_ENCRYPTION_KEY invalida: {exc}") from exc
    return _fernet_cache


def _encrypt_secret(secret: str) -> str:
    return _get_fernet().encrypt(secret.encode()).decode()


def _decrypt_secret(encrypted: str) -> str:
    try:
        return _get_fernet().decrypt(encrypted.encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError("Falha ao decifrar segredo MFA — chave incorreta?") from exc


def _generate_secret() -> str:
    """Gera segredo TOTP de 160 bits (padrao recomendado, base32)."""
    return pyotp.random_base32()


def _provisioning_uri(email: str, secret: str, issuer: str = "PigBank AI") -> str:
    """Gera URI otpauth:// para o QR code."""
    return pyotp.totp.TOTP(secret).provisioning_uri(name=email, issuer_name=issuer)


def _generate_backup_codes() -> list[str]:
    """Retorna lista de codigos legiveis (formato XXXX-XXXX, alfanum)."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # sem 0/O/1/I para legibilidade
    codes = []
    for _ in range(_BACKUP_CODE_COUNT):
        raw = "".join(secrets.choice(alphabet) for _ in range(_BACKUP_CODE_LENGTH))
        codes.append(f"{raw[:5]}-{raw[5:]}")
    return codes


def _hash_backup_code(code: str) -> str:
    """Bcrypt-hash do codigo de backup (apos normalizar). Mais lento que SHA mas
    a quantidade de codigos e baixa, e o custo amortiza a protecao a brute force."""
    normalized = code.replace("-", "").replace(" ", "").upper()
    return bcrypt.hashpw(normalized.encode(), bcrypt.gensalt()).decode()


def _check_backup_code(code: str, hashed: str) -> bool:
    normalized = code.replace("-", "").replace(" ", "").upper()
    try:
        return bcrypt.checkpw(normalized.encode(), hashed.encode())
    except ValueError:
        return False


# ──────────────────────────────────────────────────────────────────────────────
# API publica
# ──────────────────────────────────────────────────────────────────────────────

def get_mfa_status(user_id: int) -> dict:
    """Retorna {'enabled': bool, 'has_pending_secret': bool, 'backup_codes_remaining': int}."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select enabled from user_mfa where user_id = %s",
                (user_id,),
            )
            row = cur.fetchone()
            enabled = bool(row and row["enabled"])
            has_pending = bool(row and not row["enabled"])

            cur.execute(
                """
                select count(*) as remaining
                from user_mfa_backup_codes
                where user_id = %s and used_at is null
                """,
                (user_id,),
            )
            remaining = int(cur.fetchone()["remaining"] or 0)
    return {
        "enabled": enabled,
        "has_pending_secret": has_pending,
        "backup_codes_remaining": remaining if enabled else 0,
    }


def setup_secret(user_id: int, email: str) -> dict:
    """Gera novo segredo (sobrescreve pendente se existir, mas nao sobrescreve
    se MFA ja esta ativado). Retorna o secret + provisioning URI para QR code."""
    secret = _generate_secret()
    encrypted = _encrypt_secret(secret)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select enabled from user_mfa where user_id = %s for update",
                (user_id,),
            )
            existing = cur.fetchone()
            if existing and existing["enabled"]:
                raise ValueError("MFA_ALREADY_ENABLED")

            cur.execute(
                """
                insert into user_mfa (user_id, secret_encrypted, enabled)
                values (%s, %s, false)
                on conflict (user_id) do update
                  set secret_encrypted = excluded.secret_encrypted,
                      enabled = false,
                      activated_at = null
                """,
                (user_id, encrypted),
            )
        conn.commit()

    return {
        "secret": secret,
        "uri": _provisioning_uri(email, secret),
    }


def verify_and_enable(user_id: int, code: str) -> list[str]:
    """Valida o primeiro codigo TOTP e ativa MFA. Retorna codigos de backup
    em texto puro (mostrar ao usuario uma unica vez)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select secret_encrypted, enabled from user_mfa where user_id = %s for update",
                (user_id,),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError("MFA_NOT_INITIALIZED")
            if row["enabled"]:
                raise ValueError("MFA_ALREADY_ENABLED")

            secret = _decrypt_secret(row["secret_encrypted"])
            totp = pyotp.TOTP(secret)
            if not totp.verify(code.strip(), valid_window=_TOTP_WINDOW):
                raise ValueError("MFA_CODE_INVALID")

            cur.execute(
                """
                update user_mfa
                set enabled = true, activated_at = now()
                where user_id = %s
                """,
                (user_id,),
            )

            backup_codes = _generate_backup_codes()
            cur.execute(
                "delete from user_mfa_backup_codes where user_id = %s",
                (user_id,),
            )
            for code_plain in backup_codes:
                cur.execute(
                    """
                    insert into user_mfa_backup_codes (user_id, code_hash)
                    values (%s, %s)
                    """,
                    (user_id, _hash_backup_code(code_plain)),
                )
        conn.commit()

    return backup_codes


def regenerate_backup_codes(user_id: int) -> list[str]:
    """Substitui todos os backup codes do usuario. Retorna os novos em texto puro."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select enabled from user_mfa where user_id = %s",
                (user_id,),
            )
            row = cur.fetchone()
            if not row or not row["enabled"]:
                raise ValueError("MFA_NOT_ENABLED")

            cur.execute(
                "delete from user_mfa_backup_codes where user_id = %s",
                (user_id,),
            )
            backup_codes = _generate_backup_codes()
            for code_plain in backup_codes:
                cur.execute(
                    """
                    insert into user_mfa_backup_codes (user_id, code_hash)
                    values (%s, %s)
                    """,
                    (user_id, _hash_backup_code(code_plain)),
                )
        conn.commit()
    return backup_codes


def verify_totp(user_id: int, code: str) -> bool:
    """Valida codigo TOTP (sem consumir nada). True se OK."""
    code = (code or "").strip().replace(" ", "")
    if not code or not code.isdigit() or len(code) != 6:
        return False
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select secret_encrypted, enabled from user_mfa where user_id = %s",
                (user_id,),
            )
            row = cur.fetchone()
            if not row or not row["enabled"]:
                return False
            secret = _decrypt_secret(row["secret_encrypted"])
            totp = pyotp.TOTP(secret)
            ok = totp.verify(code, valid_window=_TOTP_WINDOW)
            if ok:
                cur.execute(
                    "update user_mfa set last_used_at = now() where user_id = %s",
                    (user_id,),
                )
                conn.commit()
            return ok


def consume_backup_code(user_id: int, code: str) -> bool:
    """Consome um codigo de backup (single-use). True se valido e nao usado."""
    code = (code or "").strip()
    if not code:
        return False
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, code_hash from user_mfa_backup_codes
                where user_id = %s and used_at is null
                for update
                """,
                (user_id,),
            )
            rows = cur.fetchall()
            for row in rows:
                if _check_backup_code(code, row["code_hash"]):
                    cur.execute(
                        "update user_mfa_backup_codes set used_at = now() where id = %s",
                        (row["id"],),
                    )
                    conn.commit()
                    return True
    return False


def disable_mfa(user_id: int) -> None:
    """Apaga totalmente o estado MFA do usuario. Sem revogacao de challenges
    pendentes — eles expiram em 5min naturalmente."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("delete from user_mfa where user_id = %s", (user_id,))
            cur.execute("delete from user_mfa_backup_codes where user_id = %s", (user_id,))
        conn.commit()


# ──────────────────────────────────────────────────────────────────────────────
# Challenge token (entre /auth/login e /auth/mfa/verify)
# ──────────────────────────────────────────────────────────────────────────────

def create_login_challenge(user_id: int) -> str:
    """Gera token unico para o passo MFA do login. Expira em 5min."""
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=_CHALLENGE_TTL_MINUTES)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into mfa_login_challenges (token, user_id, expires_at)
                values (%s, %s, %s)
                """,
                (token, user_id, expires_at),
            )
        conn.commit()
    return token


def consume_login_challenge(token: str) -> int | None:
    """Marca o challenge como usado e retorna user_id se valido. None caso contrario."""
    if not token:
        return None
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                update mfa_login_challenges
                set used_at = now()
                where token = %s
                  and used_at is null
                  and expires_at > now()
                returning user_id
                """,
                (token,),
            )
            row = cur.fetchone()
        conn.commit()
    return int(row["user_id"]) if row else None


def cleanup_expired_challenges() -> int:
    """Limpeza periodica. Retorna quantos foram apagados."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "delete from mfa_login_challenges where expires_at < now() - interval '1 day'"
            )
            count = cur.rowcount
        conn.commit()
    return count or 0


# ──────────────────────────────────────────────────────────────────────────────
# Onboarding (tela de incentivo apos primeiro login)
# ──────────────────────────────────────────────────────────────────────────────

def should_show_mfa_onboarding(user_id: int) -> bool:
    """True se o usuario ainda nao viu a tela de onboarding E nao tem MFA ativado."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select aa.mfa_onboarding_shown_at, m.enabled
                from auth_accounts aa
                left join user_mfa m on m.user_id = aa.user_id
                where aa.user_id = %s
                """,
                (user_id,),
            )
            row = cur.fetchone()
            if not row:
                return False
            already_shown = row["mfa_onboarding_shown_at"] is not None
            mfa_enabled = bool(row["enabled"])
            return not already_shown and not mfa_enabled


def mark_mfa_onboarding_shown(user_id: int) -> None:
    """Grava timestamp atual em auth_accounts.mfa_onboarding_shown_at."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                update auth_accounts
                set mfa_onboarding_shown_at = now()
                where user_id = %s and mfa_onboarding_shown_at is null
                """,
                (user_id,),
            )
        conn.commit()
