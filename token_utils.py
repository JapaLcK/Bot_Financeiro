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
