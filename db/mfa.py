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

import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import pyotp
from cryptography.fernet import Fernet, InvalidToken

from .connection import get_conn

logger = logging.getLogger(__name__)

_BACKUP_CODE_COUNT = 10
_BACKUP_CODE_LENGTH = 10  # caracteres (alfanumerico legivel)
_CHALLENGE_TTL_MINUTES = 5
_CHALLENGE_MAX_ATTEMPTS = 5
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


def _provisioning_uri(email: str, secret: str, issuer: str = "PigBank") -> str:
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

    # O convite da /home some sozinho enquanto o MFA esta ligado, mas `disable_mfa`
    # apaga a linha e o left join volta a `enabled = NULL` — sem marcar aqui, quem
    # ativou e depois desligou volta a ser convidado em TODA carga da /home, para
    # sempre. Marcar na ATIVACAO grava um fato; marcar no clique de "Ativar agora"
    # gravava uma intencao e punia quem abandonava o setup no meio (por isso o
    # botao nao marca mais — frontend/home.html).
    #
    # O erro e ENGOLIDO de proposito, e isto nao e defensivo demais: o commit
    # acima ja ligou o MFA, e `backup_codes` so existe em texto puro dentro deste
    # `return` — sao os 10 codigos mostrados UMA vez, a unica saida de quem perde
    # o celular. Deixar esta cauda propagar troca "o convite reaparece" (o pior
    # caso dela falhar) por "MFA ligado, codigos perdidos, 409 na retentativa" —
    # escrita nova depois do ponto sem volta, a classe do `mark_bill_paid`
    # (CLAUDE.md §1). O endpoint so trata ValueError, entao qualquer outra virava
    # 500 em cima de uma ativacao que ja aconteceu.
    try:
        mark_mfa_onboarding_shown(user_id)
    except Exception as exc:  # nunca derruba a resposta do enable
        logger.warning("mfa onboarding mark falhou user=%s: %s", user_id, exc)

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


def _verify_totp(cur, user_id: int, code: str) -> bool:
    """Confere o TOTP no cursor dado; o commit e de quem chama."""
    code = (code or "").strip().replace(" ", "")
    if not code or not code.isdigit() or len(code) != 6:
        return False
    cur.execute(
        "select secret_encrypted, enabled from user_mfa where user_id = %s",
        (user_id,),
    )
    row = cur.fetchone()
    if not row or not row["enabled"]:
        return False
    secret = _decrypt_secret(row["secret_encrypted"])
    ok = pyotp.TOTP(secret).verify(code, valid_window=_TOTP_WINDOW)
    if ok:
        cur.execute(
            "update user_mfa set last_used_at = now() where user_id = %s",
            (user_id,),
        )
    return ok


def _consume_backup_code(cur, user_id: int, code: str) -> bool:
    """Marca o backup como usado no cursor dado; o commit e de quem chama."""
    code = (code or "").strip()
    if not code:
        return False
    cur.execute(
        """
        select id, code_hash from user_mfa_backup_codes
        where user_id = %s and used_at is null
        for update
        """,
        (user_id,),
    )
    for row in cur.fetchall():
        if _check_backup_code(code, row["code_hash"]):
            cur.execute(
                "update user_mfa_backup_codes set used_at = now() where id = %s",
                (row["id"],),
            )
            return True
    return False


def verify_totp(user_id: int, code: str) -> bool:
    """Valida codigo TOTP (sem consumir nada). True se OK."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            ok = _verify_totp(cur, user_id, code)
        conn.commit()
    return ok


def consume_backup_code(user_id: int, code: str) -> bool:
    """Consome um codigo de backup (single-use). True se valido e nao usado."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            ok = _consume_backup_code(cur, user_id, code)
        conn.commit()
    return ok


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


def reserve_login_challenge_attempt(token: str) -> dict | None:
    """Gasta uma tentativa do challenge ANTES de conferir o codigo.

    Retorna {'user_id', 'restantes'} ou None se o challenge nao existe, venceu,
    ja foi usado ou esgotou as tentativas. Um UPDATE so: o WHERE e reavaliado
    na linha travada, entao tentativas simultaneas nunca passam do teto.
    """
    if not token:
        return None
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                update mfa_login_challenges
                set attempts = attempts + 1
                where token = %s
                  and used_at is null
                  and expires_at > now()
                  and attempts < %s
                returning user_id, attempts
                """,
                (token, _CHALLENGE_MAX_ATTEMPTS),
            )
            row = cur.fetchone()
        conn.commit()
    if not row:
        return None
    return {
        "user_id": int(row["user_id"]),
        "restantes": _CHALLENGE_MAX_ATTEMPTS - int(row["attempts"]),
    }


def consume_login_challenge_with_code(token: str, code: str, use_backup: bool) -> bool | None:
    """Confere o codigo e consome o challenge NUMA transacao, com ele travado.

    None: challenge morto (vencido, inexistente ou ja consumido por outro
    pedido). False: codigo errado, nada gasto. True: challenge consumido.
    O backup so e gasto no mesmo commit que consome o challenge: separado, o
    pedido que perdia a corrida para um TOTP certo gastava o backup e dava 400.
    """
    if not token:
        return None
    # Os `return` de dentro do `with` também fecham a transação: o `with` da
    # conexão commita ao sair (ou faz rollback na exceção) e solta o `for update`.
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select user_id from mfa_login_challenges
                where token = %s and used_at is null and expires_at > now()
                for update
                """,
                (token,),
            )
            row = cur.fetchone()
            if not row:
                return None
            user_id = int(row["user_id"])
            conferir = _consume_backup_code if use_backup else _verify_totp
            if not conferir(cur, user_id, code):
                return False
            cur.execute(
                "update mfa_login_challenges set used_at = now() where token = %s",
                (token,),
            )
        conn.commit()
    return True


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
    from db_support import invalidate_auth_user_cache
    invalidate_auth_user_cache(user_id)
