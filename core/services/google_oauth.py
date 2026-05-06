"""
core/services/google_oauth.py — Cliente OAuth 2.0 do Google (server-side).

Fluxo:
  1) build_authorization_url(state) → URL pra redirecionar o usuário ao Google
  2) exchange_code_for_tokens(code) → POST /token, devolve id_token
  3) verify_id_token(id_token) → valida assinatura/aud/iss/exp e retorna claims
"""
import logging
import os
import urllib.parse

import httpx
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

logger = logging.getLogger(__name__)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

DEFAULT_SCOPES = ("openid", "email", "profile")


class GoogleOAuthError(Exception):
    """Erro genérico no fluxo OAuth do Google."""


def _required_env(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise GoogleOAuthError(f"Variável de ambiente obrigatória não definida: {name}")
    return value


def get_client_id() -> str:
    return _required_env("GOOGLE_CLIENT_ID")


def get_client_secret() -> str:
    return _required_env("GOOGLE_CLIENT_SECRET")


def get_redirect_uri() -> str:
    return _required_env("GOOGLE_REDIRECT_URI")


def is_configured() -> bool:
    """True se as 3 envs estão presentes."""
    for name in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI"):
        if not (os.getenv(name) or "").strip():
            return False
    return True


def build_authorization_url(state: str, scopes: tuple[str, ...] = DEFAULT_SCOPES) -> str:
    """
    Monta a URL pro Google. O `state` é usado pra CSRF (compara contra cookie no callback).
    `prompt=select_account` força a tela de escolha de conta mesmo se houver sessão única.
    """
    params = {
        "client_id": get_client_id(),
        "redirect_uri": get_redirect_uri(),
        "response_type": "code",
        "scope": " ".join(scopes),
        "access_type": "online",
        "include_granted_scopes": "true",
        "prompt": "select_account",
        "state": state,
    }
    return f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"


async def exchange_code_for_tokens(code: str) -> dict:
    """Troca o authorization code pelos tokens. Retorna o JSON do Google."""
    data = {
        "code": code,
        "client_id": get_client_id(),
        "client_secret": get_client_secret(),
        "redirect_uri": get_redirect_uri(),
        "grant_type": "authorization_code",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                GOOGLE_TOKEN_URL,
                data=data,
                headers={"Accept": "application/json"},
            )
    except httpx.HTTPError as exc:
        logger.warning("Falha de rede no token exchange do Google: %s", exc)
        raise GoogleOAuthError("Falha ao contatar o Google. Tente novamente.") from exc

    if resp.status_code != 200:
        logger.warning(
            "Token exchange do Google retornou %s: %s",
            resp.status_code, resp.text[:300],
        )
        raise GoogleOAuthError("Não foi possível concluir o login com Google.")

    body = resp.json()
    if "id_token" not in body:
        raise GoogleOAuthError("Resposta do Google sem id_token.")
    return body


def verify_id_token(token: str) -> dict:
    """
    Valida assinatura, aud, iss e exp.
    Retorna o dicionário de claims (sub, email, email_verified, name, picture, ...).
    """
    try:
        claims = google_id_token.verify_oauth2_token(
            token,
            google_requests.Request(),
            audience=get_client_id(),
        )
    except ValueError as exc:
        logger.warning("ID token inválido do Google: %s", exc)
        raise GoogleOAuthError("Token de identidade inválido.") from exc

    issuer = claims.get("iss")
    if issuer not in ("accounts.google.com", "https://accounts.google.com"):
        raise GoogleOAuthError("Emissor do token não confiável.")

    if not claims.get("sub"):
        raise GoogleOAuthError("Token sem identificador de usuário.")

    return claims
