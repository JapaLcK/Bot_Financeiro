"""Verificação do identity token da Apple — `core/services/apple_signin.py`.

Só a rede da Apple é dublê (`apple_de_mentira`): a assinatura, as claims e o
nonce são conferidos de verdade pelo PyJWT.

Controle negativo (medido; comando e resultado no corpo do PR): tirar a
checagem do nonce → V9 vermelho; `verify_aud: False` → V6; inverter a ordem
dos `except` → V10; `algorithms` lido do cabeçalho → V4; `leeway=0` → V8b.
Controle positivo: V1 e V8b.
"""
import base64
import hashlib
import hmac
import json
import re
import time
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from _apoio_auth_app import APAGA, KID, NONCE_CRU, apple_de_mentira
from core.services.apple_signin import (
    APPLE_BUNDLE_ID,
    AppleIndisponivel,
    AppleTokenInvalido,
    verificar_identity_token,
)

_CONFIG_DO_APP = Path(__file__).resolve().parent.parent / "app" / "app.config.ts"


@pytest.fixture
def apple(monkeypatch):
    return apple_de_mentira(monkeypatch)


def _b64(dados: bytes) -> str:
    return base64.urlsafe_b64encode(dados).rstrip(b"=").decode()


def _assinado_a_mao(cabecalho: dict, corpo: dict, segredo: bytes | None) -> str:
    """O PyJWT se recusa a assinar HS256 com PEM (e com razão); o atacante não."""
    base = f"{_b64(json.dumps(cabecalho).encode())}.{_b64(json.dumps(corpo).encode())}"
    assinatura = hmac.new(segredo, base.encode(), hashlib.sha256).digest() if segredo else b""
    return f"{base}.{_b64(assinatura)}"


def test_v1_token_valido_devolve_as_claims(apple):
    claims = verificar_identity_token(apple.emitir(email=" Apple@Example.COM "), NONCE_CRU)
    assert claims == {
        "sub": "000123.apple-de-teste.0456",
        "email": "apple@example.com",
        "email_verified": True,
    }


def test_v2_mesmo_kid_outra_chave(apple):
    outra = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(AppleTokenInvalido):
        verificar_identity_token(apple.emitir(chave_de=outra), NONCE_CRU)


def test_v3_kid_desconhecido_forca_um_refresh_e_recusa(apple):
    with pytest.raises(AppleTokenInvalido):
        verificar_identity_token(apple.emitir(kid="kid-inventado"), NONCE_CRU)
    assert apple.buscas == 2


def test_v4_hs256_com_a_chave_publica_e_alg_none(apple):
    corpo = jwt.decode(apple.emitir(), options={"verify_signature": False})
    pem = apple.chave.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    hs256 = _assinado_a_mao({"alg": "HS256", "kid": KID, "typ": "JWT"}, corpo, pem)
    nenhum = _assinado_a_mao({"alg": "none", "kid": KID, "typ": "JWT"}, corpo, None)
    for token in (hs256, nenhum):
        with pytest.raises(AppleTokenInvalido):
            verificar_identity_token(token, NONCE_CRU)


@pytest.mark.parametrize(
    "claims",
    [
        {"iss": "https://accounts.google.com"},
        {"aud": "host.exp.Exponent"},
        {"aud": f"{APPLE_BUNDLE_ID}.dev"},
        {"exp": -60},
        {"iat": +120},
        {"sub": ""},
        {"sub": APAGA},
    ],
    ids=["v5-iss", "v6-aud-expo-go", "v6-aud-dev", "v7-exp", "v8-iat-futuro", "sub-vazio", "sub-ausente"],
)
def test_v5_a_v8_claims_erradas(apple, claims):
    # `exp`/`iat` são deslocamentos a partir de AGORA, calculados na execução:
    # na coleta, uma suíte longa levaria o `iat` para dentro do leeway.
    claims = {k: int(time.time()) + v if k in ("exp", "iat") else v for k, v in claims.items()}
    with pytest.raises(AppleTokenInvalido):
        verificar_identity_token(apple.emitir(**claims), NONCE_CRU)


def test_v8b_iat_dez_segundos_no_futuro_passa_pelo_leeway(apple):
    token = apple.emitir(iat=int(time.time()) + 10)
    assert verificar_identity_token(token, NONCE_CRU)["sub"]


@pytest.mark.parametrize(
    "nonce",
    [APAGA, NONCE_CRU, hashlib.sha256(b"outro-nonce-cru-qualquer").hexdigest()],
    ids=["ausente", "cru-sem-hash", "hash-de-outro"],
)
def test_v9_nonce_errado(apple, nonce):
    with pytest.raises(AppleTokenInvalido):
        verificar_identity_token(apple.emitir(nonce=nonce), NONCE_CRU)


def test_v10_jwks_fora_e_indisponivel_nao_invalido(apple):
    apple.fora = True
    with pytest.raises(AppleIndisponivel):
        verificar_identity_token(apple.emitir(), NONCE_CRU)


@pytest.mark.parametrize(
    "claims, verificado",
    [
        ({"email_verified": True}, True),
        ({"email_verified": "true"}, True),
        ({"email_verified": False}, False),
        ({"email_verified": "false"}, False),
        ({"email_verified": APAGA}, False),
    ],
    ids=["bool-true", "str-true", "bool-false", "str-false", "ausente"],
)
def test_v11_booleanos_da_apple(apple, claims, verificado):
    resultado = verificar_identity_token(apple.emitir(**claims), NONCE_CRU)
    assert resultado["email_verified"] is verificado


def test_bundle_id_espelha_o_app_config_de_producao():
    fonte = _CONFIG_DO_APP.read_text(encoding="utf-8")
    id_base = re.search(r'const ID_BASE = "([^"]+)"', fonte)
    producao = re.search(r'production: \{ sufixoId: "([^"]*)"', fonte)
    assert id_base and producao, "o formato do app.config.ts mudou: ajuste as buscas"
    assert APPLE_BUNDLE_ID == id_base.group(1) + producao.group(1)
