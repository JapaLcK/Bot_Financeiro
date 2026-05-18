"""
core/crypto.py — Cifragem column-level e hash determinístico de PII.

Padrão usado por bancos digitais BR (Nubank, Inter) pra atender LGPD art. 46
sem perder a capacidade de fazer lookup por email/telefone/external_id:

    email_hash  (HMAC-SHA256 + pepper)  → indexável, irreversível, pra WHERE
    email_enc   (Fernet com versão v1)  → reversível só com a chave, pra display/envio

Uso típico:

    from core.crypto import encrypt_pii, decrypt_pii, hash_pii, PiiAccessContext

    # ── WRITE (signup) ────────────────────────────────────────────────────────
    cur.execute(
        "insert into auth_accounts(email_hash, email_enc, ...) values (%s, %s, ...)",
        (hash_pii(email, kind='email'), encrypt_pii(email), ...)
    )

    # ── LOOKUP por valor (login, bot recebe msg) ──────────────────────────────
    cur.execute(
        "select user_id, email_enc from auth_accounts where email_hash = %s",
        (hash_pii(email_submetido, kind='email'),),
    )

    # ── DISPLAY / envio (sempre com contexto pra auditoria) ───────────────────
    email = decrypt_pii(
        row['email_enc'],
        ctx=PiiAccessContext(
            purpose='send_trial_email',
            actor='system:engagement_scheduler',
            subject_user_id=row['user_id'],
            field='email',
        ),
    )

Variáveis de ambiente obrigatórias:
  - PII_ENCRYPTION_KEY  → chave Fernet (urlsafe-base64, 32 bytes). ROTACIONÁVEL.
  - PII_HASH_PEPPER     → segredo HMAC (mín 32 chars). NUNCA ROTACIONAR
                          (todos os hashes existentes ficariam inúteis).

Gerar ambas:
  python -m scripts.generate_pii_keys

Rotação futura: defina PII_ENCRYPTION_KEY_V2 e re-cifre os dados; manter v1
em paralelo até a migração terminar.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import sys
import threading
from dataclasses import dataclass
from typing import Literal

from cryptography.fernet import Fernet, InvalidToken
from psycopg.types.json import Jsonb


PiiKind = Literal["email", "phone", "external_id", "name", "generic"]

_CURRENT_VERSION = "v1"

_fernet_cache: dict[str, Fernet] = {}
_pepper_cache: bytes | None = None
_cache_lock = threading.Lock()


# ──────────────────────────────────────────────────────────────────────────────
# Carregamento de chaves (cacheado)
# ──────────────────────────────────────────────────────────────────────────────

def _load_pepper() -> bytes:
    global _pepper_cache
    if _pepper_cache is not None:
        return _pepper_cache
    with _cache_lock:
        if _pepper_cache is not None:
            return _pepper_cache
        raw = (os.getenv("PII_HASH_PEPPER") or "").strip()
        if not raw:
            raise RuntimeError(
                "PII_HASH_PEPPER não configurada. Gere com:\n"
                "  python -m scripts.generate_pii_keys"
            )
        if len(raw) < 32:
            raise RuntimeError(
                "PII_HASH_PEPPER muito curta (mínimo 32 caracteres)."
            )
        _pepper_cache = raw.encode("utf-8")
        return _pepper_cache


def _load_fernet(version: str = _CURRENT_VERSION) -> Fernet:
    if version in _fernet_cache:
        return _fernet_cache[version]
    with _cache_lock:
        if version in _fernet_cache:
            return _fernet_cache[version]
        env_name = (
            "PII_ENCRYPTION_KEY"
            if version == _CURRENT_VERSION
            else f"PII_ENCRYPTION_KEY_{version.upper()}"
        )
        raw = (os.getenv(env_name) or "").strip()
        if not raw:
            raise RuntimeError(
                f"{env_name} não configurada. Gere com:\n"
                f"  python -m scripts.generate_pii_keys"
            )
        try:
            f = Fernet(raw.encode("ascii"))
        except (ValueError, TypeError) as exc:
            raise RuntimeError(f"{env_name} inválida: {exc}") from exc
        _fernet_cache[version] = f
        return f


def _reset_cache_for_tests() -> None:
    """Não usar em produção — só testes que setam env dinamicamente."""
    global _pepper_cache
    with _cache_lock:
        _fernet_cache.clear()
        _pepper_cache = None


# ──────────────────────────────────────────────────────────────────────────────
# Normalização (impacta o hash — manter consistente entre write e lookup)
# ──────────────────────────────────────────────────────────────────────────────

def normalize_pii(plain: str, kind: PiiKind) -> str:
    """Normaliza antes de hash/encrypt. Indispensável pro hash bater no lookup."""
    if plain is None:
        return ""
    s = str(plain).strip()
    if kind == "email":
        return s.lower()
    # phone: assume já E.164 (utils_phone.normalize_e164 cuida disso a montante)
    # external_id, name, generic: só strip
    return s


# ──────────────────────────────────────────────────────────────────────────────
# Hash determinístico (HMAC-SHA256 com pepper)
# ──────────────────────────────────────────────────────────────────────────────

def hash_pii(plain: str, *, kind: PiiKind = "generic") -> str:
    """HMAC-SHA256(plain_normalizado, PEPPER) → 64-char hex.

    Determinístico, indexável, irreversível. Use sempre em WHERE de lookup.
    O pepper NÃO ROTACIONA — se for trocado, todos os hashes existentes
    deixam de bater.
    """
    if not plain:
        raise ValueError("hash_pii recebeu string vazia")
    pepper = _load_pepper()
    normalized = normalize_pii(plain, kind).encode("utf-8")
    return hmac.new(pepper, normalized, hashlib.sha256).hexdigest()


def hash_pii_optional(plain: str | None, *, kind: PiiKind = "generic") -> str | None:
    if plain is None or plain == "":
        return None
    return hash_pii(plain, kind=kind)


# ──────────────────────────────────────────────────────────────────────────────
# Cifragem (Fernet com versionamento)
# ──────────────────────────────────────────────────────────────────────────────

def encrypt_pii(plain: str) -> str:
    """Cifra `plain` com Fernet e retorna string `v<N>:<base64>`.

    Não-determinístico (cifrar 2x produz blobs diferentes). Para lookup,
    use `hash_pii` em paralelo.
    """
    if plain is None:
        raise ValueError("encrypt_pii recebeu None — use encrypt_pii_optional")
    if not isinstance(plain, str):
        raise TypeError(f"encrypt_pii espera str, recebeu {type(plain).__name__}")
    f = _load_fernet()
    ct = f.encrypt(plain.encode("utf-8"))
    return f"{_CURRENT_VERSION}:{ct.decode('ascii')}"


def encrypt_pii_optional(plain: str | None) -> str | None:
    if plain is None or plain == "":
        return None
    return encrypt_pii(plain)


# ──────────────────────────────────────────────────────────────────────────────
# Descifragem + audit log
# ──────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class PiiAccessContext:
    """Contexto registrado a cada decrypt_pii. Vai pra pii_access_log."""
    purpose: str          # 'login', 'send_email', 'render_admin', 'bot_message', etc
    actor: str            # 'system', 'admin:lucas', 'user:88648360', 'webhook:stripe'
    subject_user_id: int  # de QUEM são os dados que estão sendo decifrados
    field: str = "?"      # 'email', 'phone', 'discord_id', 'name', etc
    endpoint: str | None = None
    extra: dict | None = None


def _parse_versioned(ct: str) -> tuple[str, str]:
    """'v1:gAAA...' → ('v1', 'gAAA...'). Sem prefixo, assume versão atual."""
    if ":" not in ct:
        return _CURRENT_VERSION, ct
    version, _, blob = ct.partition(":")
    return version, blob


def decrypt_pii(ct: str, *, ctx: PiiAccessContext) -> str:
    """Decifra e REGISTRA o acesso em pii_access_log.

    Use SEMPRE com `ctx` descritivo — o ctx é o que vai pro audit. Se for
    uma operação puramente sistêmica (ex: webhook do Stripe enviando email
    transacional), use actor='system:<componente>' pra o painel poder
    filtrar acessos humanos vs automáticos.
    """
    if ct is None:
        raise ValueError("decrypt_pii recebeu None — use decrypt_pii_optional")
    version, blob = _parse_versioned(ct)
    f = _load_fernet(version)
    try:
        plain = f.decrypt(blob.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError(
            f"Falha ao decifrar PII (versão {version}) — chave incorreta?"
        ) from exc

    _record_access(ctx)
    return plain


def decrypt_pii_optional(ct: str | None, *, ctx: PiiAccessContext) -> str | None:
    if ct is None or ct == "":
        return None
    return decrypt_pii(ct, ctx=ctx)


def _record_access(ctx: PiiAccessContext) -> None:
    """Insere uma linha em pii_access_log. Falha silenciosa — audit NUNCA
    pode bloquear o fluxo principal."""
    if os.getenv("PII_AUDIT_DISABLED", "").strip() in {"1", "true", "True"}:
        return
    try:
        from db.connection import get_conn
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                insert into pii_access_log
                  (purpose, actor, subject_user_id, field, endpoint, extra)
                values (%s, %s, %s, %s, %s, %s)
                """,
                (
                    (ctx.purpose or "?")[:120],
                    (ctx.actor or "?")[:160],
                    int(ctx.subject_user_id),
                    (ctx.field or "?")[:60],
                    (ctx.endpoint or None) and ctx.endpoint[:200],
                    Jsonb(ctx.extra) if ctx.extra else None,
                ),
            )
            conn.commit()
    except Exception as exc:
        print(f"[crypto] failed to record pii access: {exc}", file=sys.stderr)


# ──────────────────────────────────────────────────────────────────────────────
# Versão (exposta pra debug e migrations)
# ──────────────────────────────────────────────────────────────────────────────

def current_version() -> str:
    return _CURRENT_VERSION
