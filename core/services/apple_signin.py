"""
core/services/apple_signin.py — Verifica o identity token do "Entrar com a Apple".

O app gera um nonce cru, manda à Apple o SHA-256 dele em hex minúsculo e ao
backend o cru. A Apple põe o hash na claim `nonce`; conferir o hash do cru aqui
faz um identity token vazado (log, Sentry) não bastar sozinho.

Nunca vai para o log: o token, o nonce, o `sub` ou o e-mail. Só o motivo.
"""
import hashlib
import hmac
import logging

import jwt
from jwt import PyJWKClient, PyJWKClientConnectionError

logger = logging.getLogger(__name__)

APPLE_ISSUER = "https://appleid.apple.com"
# Espelho do `ID_BASE` + sufixo de produção ("") do `app/app.config.ts`
# (conferido em `tests/test_auth_apple_token.py`). Só o `aud` de produção:
# dev e staging não têm a capability no App ID, e aceitar o Expo Go
# (`host.exp.Exponent`) deixaria entrar com um token obtido em qualquer projeto
# Expo Go de terceiros.
# ponytail: quando houver staging com Apple, uma env só no staging.
APPLE_BUNDLE_ID = "com.pigbankai.mobile"

# ponytail: um `kid` inventado força uma busca na Apple por requisição; o teto
# é o rate limit da rota (10/min por IP). Cache negativo só se aparecer no log.
_jwks = PyJWKClient("https://appleid.apple.com/auth/keys", timeout=5)


class AppleTokenInvalido(Exception):
    """O token não prova nada: assinatura, claims ou nonce não conferem."""


class AppleIndisponivel(Exception):
    """Não deu para buscar as chaves públicas da Apple."""


def _verdadeiro(valor) -> bool:
    # A Apple manda booleano OU a string "true"/"false".
    return valor is True or valor == "true"


def verificar_identity_token(token: str, nonce_cru: str) -> dict:
    """Síncrona (a rota chama em `asyncio.to_thread`). Devolve
    `{sub, email, email_verified}` ou levanta uma das duas exceções."""
    try:
        chave = _jwks.get_signing_key_from_jwt(token).key
        # Algoritmo FIXO, nunca o do cabeçalho: fecha `alg: none` e a confusão
        # HS256 com a chave pública. `leeway` é folga de relógio (o PyJWT recusa
        # `iat` no futuro), não de validade: o token da Apple vive minutos.
        claims = jwt.decode(
            token,
            chave,
            algorithms=["RS256"],
            audience=APPLE_BUNDLE_ID,
            issuer=APPLE_ISSUER,
            leeway=30,
            options={"require": ["iss", "aud", "exp", "iat", "sub", "nonce"]},
        )
    # A de conexão é SUBCLASSE de `PyJWKClientError` (um `PyJWTError`): vem antes.
    except PyJWKClientConnectionError:
        logger.warning("Apple: JWKS indisponível")
        raise AppleIndisponivel() from None
    except jwt.PyJWTError as exc:
        logger.warning("Apple: identity token recusado (%s)", type(exc).__name__)
        raise AppleTokenInvalido() from None

    nonce = claims["nonce"]
    esperado = hashlib.sha256(nonce_cru.encode()).hexdigest()
    if not isinstance(nonce, str) or not hmac.compare_digest(nonce.encode(), esperado.encode()):
        logger.warning("Apple: identity token recusado (nonce)")
        raise AppleTokenInvalido()

    sub = claims["sub"]
    if not isinstance(sub, str) or not sub:
        logger.warning("Apple: identity token recusado (sub)")
        raise AppleTokenInvalido()

    email = claims.get("email")
    email = (email.strip().lower() or None) if isinstance(email, str) else None
    return {
        "sub": sub,
        "email": email,
        "email_verified": _verdadeiro(claims.get("email_verified")),
    }
