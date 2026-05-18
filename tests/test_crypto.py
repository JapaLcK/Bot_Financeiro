"""
tests/test_crypto.py — Testes do módulo core/crypto.py.

Testes que NÃO tocam o banco. O insert em pii_access_log é mockado.
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from core import crypto
from core.crypto import (
    PiiAccessContext,
    decrypt_pii,
    decrypt_pii_optional,
    encrypt_pii,
    encrypt_pii_optional,
    hash_pii,
    hash_pii_optional,
    normalize_pii,
)


@pytest.fixture(autouse=True)
def _setup_keys(monkeypatch):
    """Gera chaves de teste e reseta cache entre testes."""
    monkeypatch.setenv("PII_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("PII_HASH_PEPPER", "test-pepper-of-at-least-32-characters-long")
    # Desabilita audit pra não tentar tocar no banco
    monkeypatch.setenv("PII_AUDIT_DISABLED", "1")
    crypto._reset_cache_for_tests()
    yield
    crypto._reset_cache_for_tests()


# ── Normalização ────────────────────────────────────────────────────────────────

def test_normalize_email_lowercases_and_trims():
    assert normalize_pii("  Lucas@GMAIL.com  ", "email") == "lucas@gmail.com"


def test_normalize_phone_only_strips():
    assert normalize_pii("  +5511999998888  ", "phone") == "+5511999998888"


def test_normalize_external_id_only_strips():
    assert normalize_pii("  88648360  ", "external_id") == "88648360"


def test_normalize_none_becomes_empty():
    assert normalize_pii(None, "email") == ""


# ── Hash determinístico ────────────────────────────────────────────────────────

def test_hash_pii_is_deterministic():
    h1 = hash_pii("lucas@gmail.com", kind="email")
    h2 = hash_pii("lucas@gmail.com", kind="email")
    assert h1 == h2


def test_hash_pii_normalizes_email_before_hashing():
    """Mesmo email com casing/whitespace diferente deve produzir mesmo hash."""
    h1 = hash_pii("LUCAS@gmail.com", kind="email")
    h2 = hash_pii(" lucas@gmail.com ", kind="email")
    assert h1 == h2


def test_hash_pii_different_inputs_produce_different_hashes():
    h1 = hash_pii("lucas@gmail.com", kind="email")
    h2 = hash_pii("outro@gmail.com", kind="email")
    assert h1 != h2


def test_hash_pii_returns_64_char_hex():
    h = hash_pii("lucas@gmail.com", kind="email")
    assert len(h) == 64
    int(h, 16)  # vira inteiro hex válido


def test_hash_pii_changes_with_different_pepper(monkeypatch):
    h1 = hash_pii("lucas@gmail.com", kind="email")
    monkeypatch.setenv("PII_HASH_PEPPER", "DIFFERENT-pepper-with-at-least-32-chars-too")
    crypto._reset_cache_for_tests()
    h2 = hash_pii("lucas@gmail.com", kind="email")
    assert h1 != h2


def test_hash_pii_empty_raises():
    with pytest.raises(ValueError):
        hash_pii("", kind="email")


def test_hash_pii_optional_returns_none_for_empty():
    assert hash_pii_optional(None, kind="phone") is None
    assert hash_pii_optional("", kind="phone") is None
    assert hash_pii_optional("+5511", kind="phone") is not None


# ── Cifragem / descifragem ─────────────────────────────────────────────────────

def test_encrypt_decrypt_roundtrip():
    plain = "lucas@gmail.com"
    ct = encrypt_pii(plain)
    out = decrypt_pii(ct, ctx=PiiAccessContext(
        purpose="test", actor="test", subject_user_id=1, field="email"
    ))
    assert out == plain


def test_encrypt_is_non_deterministic():
    """Cifrar 2x o mesmo valor deve gerar blobs diferentes (IV aleatório)."""
    ct1 = encrypt_pii("lucas@gmail.com")
    ct2 = encrypt_pii("lucas@gmail.com")
    assert ct1 != ct2


def test_encrypt_includes_version_prefix():
    ct = encrypt_pii("lucas@gmail.com")
    assert ct.startswith("v1:")


def test_decrypt_works_with_or_without_prefix():
    """Defensivo: blobs sem prefixo (legados) devem ser tratáveis como v1."""
    plain = "lucas@gmail.com"
    ct = encrypt_pii(plain)
    blob_only = ct.removeprefix("v1:")
    out = decrypt_pii(blob_only, ctx=PiiAccessContext(
        purpose="test", actor="test", subject_user_id=1, field="email"
    ))
    assert out == plain


def test_decrypt_with_wrong_key_raises(monkeypatch):
    ct = encrypt_pii("lucas@gmail.com")
    # Troca a chave e força recarregar
    monkeypatch.setenv("PII_ENCRYPTION_KEY", Fernet.generate_key().decode())
    crypto._reset_cache_for_tests()
    with pytest.raises(RuntimeError, match="Falha ao decifrar"):
        decrypt_pii(ct, ctx=PiiAccessContext(
            purpose="test", actor="test", subject_user_id=1, field="email"
        ))


def test_encrypt_pii_optional_returns_none_for_empty():
    assert encrypt_pii_optional(None) is None
    assert encrypt_pii_optional("") is None
    assert encrypt_pii_optional("lucas@gmail.com") is not None


def test_decrypt_pii_optional_returns_none_for_empty():
    ctx = PiiAccessContext(purpose="t", actor="t", subject_user_id=1)
    assert decrypt_pii_optional(None, ctx=ctx) is None
    assert decrypt_pii_optional("", ctx=ctx) is None


def test_encrypt_none_raises():
    with pytest.raises(ValueError):
        encrypt_pii(None)  # type: ignore[arg-type]


# ── Sanity de chaves ausentes ──────────────────────────────────────────────────

def test_missing_pepper_raises(monkeypatch):
    monkeypatch.delenv("PII_HASH_PEPPER", raising=False)
    crypto._reset_cache_for_tests()
    with pytest.raises(RuntimeError, match="PII_HASH_PEPPER"):
        hash_pii("x@y.com", kind="email")


def test_missing_encryption_key_raises(monkeypatch):
    monkeypatch.delenv("PII_ENCRYPTION_KEY", raising=False)
    crypto._reset_cache_for_tests()
    with pytest.raises(RuntimeError, match="PII_ENCRYPTION_KEY"):
        encrypt_pii("x@y.com")


def test_short_pepper_raises(monkeypatch):
    monkeypatch.setenv("PII_HASH_PEPPER", "too-short")
    crypto._reset_cache_for_tests()
    with pytest.raises(RuntimeError, match="muito curta"):
        hash_pii("x@y.com", kind="email")


# ── Audit log (mockado) ────────────────────────────────────────────────────────

def test_decrypt_records_access_when_audit_enabled(monkeypatch):
    """Quando audit ligado, decrypt_pii chama _record_access que tenta inserir."""
    monkeypatch.delenv("PII_AUDIT_DISABLED", raising=False)

    ct = encrypt_pii("lucas@gmail.com")
    ctx = PiiAccessContext(
        purpose="render_admin",
        actor="admin:lucas",
        subject_user_id=88648360,
        field="email",
        endpoint="/admin/users/88648360",
    )

    with patch("core.crypto._record_access") as mock_record:
        plain = decrypt_pii(ct, ctx=ctx)
        assert plain == "lucas@gmail.com"
        mock_record.assert_called_once_with(ctx)


def test_audit_disabled_skips_recording(monkeypatch):
    """PII_AUDIT_DISABLED=1 deve fazer _record_access ser no-op."""
    monkeypatch.setenv("PII_AUDIT_DISABLED", "1")
    ctx = PiiAccessContext(
        purpose="migration", actor="system:migration", subject_user_id=1
    )
    # _record_access deve sair cedo sem tocar no banco — não deve levantar
    crypto._record_access(ctx)
