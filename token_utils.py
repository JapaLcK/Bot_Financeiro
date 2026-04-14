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


def make_dashboard_token(user_id: int, hours: float = 2) -> str:
    """
    Generate a short-lived signed token for dashboard access.
    """
    import jwt

    payload = {
        "sub": str(user_id),
        "type": "dashboard",
        "exp": datetime.now(timezone.utc) + timedelta(hours=hours),
    }
    return jwt.encode(payload, _require_jwt_secret(), algorithm="HS256")


def decode_dashboard_token(token: str):
    """
    Decode and validate a dashboard token.
    Returns user_id (int) on success, None on failure/expiry.
    """
    if not token:
        return None

    try:
        import jwt
        payload = jwt.decode(token, _require_jwt_secret(), algorithms=["HS256"])
        if payload.get("type") != "dashboard":
            return None
        return int(payload["sub"])
    except Exception:
        return None
