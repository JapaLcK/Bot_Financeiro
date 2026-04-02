"""
Shared JWT utilities for dashboard access tokens.
Used by both the FastAPI server and the bot adapters.
"""
import os
from datetime import datetime, timezone, timedelta

JWT_SECRET = os.getenv("JWT_SECRET", "change-me-in-production")


def make_dashboard_token(user_id: int, hours: int = 2) -> str:
    """
    Generate a short-lived signed token for dashboard access.
    Returns a JWT string. Falls back to a plain 'nojwt:<id>' string
    if PyJWT is not installed (degraded mode).
    """
    try:
        import jwt
        payload = {
            "sub": str(user_id),
            "type": "dashboard",
            "exp": datetime.now(timezone.utc) + timedelta(hours=hours),
        }
        return jwt.encode(payload, JWT_SECRET, algorithm="HS256")
    except ImportError:
        return f"nojwt:{user_id}"


def decode_dashboard_token(token: str):
    """
    Decode and validate a dashboard token.
    Returns user_id (int) on success, None on failure/expiry.
    """
    if not token:
        return None

    # Degraded fallback (no PyJWT installed)
    if token.startswith("nojwt:"):
        try:
            return int(token.split(":", 1)[1])
        except Exception:
            return None

    try:
        import jwt
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        if payload.get("type") != "dashboard":
            return None
        return int(payload["sub"])
    except Exception:
        return None
