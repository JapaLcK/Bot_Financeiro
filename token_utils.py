"""
Shared JWT utilities for dashboard access tokens.
Used by both the FastAPI server and the bot adapters.
"""
import os
from datetime import datetime, timezone, timedelta


def _require_jwt_secret() -> str:
    secret = (os.getenv("JWT_SECRET") or "").strip()
    if not secret:
        raise RuntimeError("JWT_SECRET não está definido.")
    return secret


# Pedaços que todo valor de dev/CI/doc do repo carrega: casar por marca evita
# manter uma lista literal que envelhece a cada exemplo novo (§0.7). Não é
# chamada pelo `_require_jwt_secret` (roda por requisição): só no boot de prod.
_MARCAS_DE_PLACEHOLDER = ("secret", "test", "troque", "bytes", "changeme")


def motivo_jwt_secret_fraco(secret: str) -> str | None:
    if len(secret) < 32:
        return "tem menos de 32 caracteres"
    if any(m in secret.lower() for m in _MARCAS_DE_PLACEHOLDER):
        return "parece valor de desenvolvimento/documentação"
    return None


def make_dashboard_token(user_id: int, hours: float = 2, *, jti: str | None = None) -> str:
    """
    Generate a short-lived signed token for dashboard access.

    When `jti` is provided, the token is bound to a row in `auth_sessions` and
    can be revoked individually (via `_resolve_dashboard_user_id`). Tokens
    without `jti` are stateless legacy tokens and are grandfathered until they
    expire naturally.
    """
    import jwt

    payload = {
        "sub": str(user_id),
        "type": "dashboard",
        "exp": datetime.now(timezone.utc) + timedelta(hours=hours),
    }
    if jti:
        payload["jti"] = jti
    return jwt.encode(payload, _require_jwt_secret(), algorithm="HS256")


def decode_dashboard_token(token: str):
    """
    Decode and validate a dashboard token.
    Returns user_id (int) on success, None on failure/expiry.

    Convenience wrapper around `decode_dashboard_token_full` for callers that
    only need the user_id (e.g. bot adapters that don't track sessions).
    """
    payload = decode_dashboard_token_full(token)
    return int(payload["user_id"]) if payload else None


def decode_dashboard_token_full(token: str):
    """
    Decode and validate a dashboard token.
    Returns {"user_id": int, "jti": str | None} on success, None on failure.
    """
    if not token:
        return None

    try:
        import jwt
        payload = jwt.decode(token, _require_jwt_secret(), algorithms=["HS256"])
        if payload.get("type") != "dashboard":
            return None
        return {
            "user_id": int(payload["sub"]),
            "jti": payload.get("jti"),
        }
    except Exception:
        return None
