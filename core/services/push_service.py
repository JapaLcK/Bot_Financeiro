"""
core/services/push_service.py — Envio de push notification pro app iOS (APNs).

DORMENTE até as envs do APNs existirem. Sem elas, `send_push_to_user` só loga e
retorna 0 — seguro pra deployar antes de gerar a chave.

Autenticação por token (APNs provider token, JWT ES256 assinado com a chave .p8
do Apple Developer) — não precisa de certificado. O token é cacheado ~50min
(APNs aceita até 60min; renova antes).

Envs necessárias pra ligar:
- APNS_KEY_ID      : Key ID da APNs Auth Key (.p8) no portal Apple
- APNS_TEAM_ID     : Team ID da conta Apple Developer (= DEVELOPMENT_TEAM)
- APNS_AUTH_KEY    : conteúdo do .p8 (PEM). Aceita quebras de linha reais ou
                     literais "\\n" (facilita colar como env var de uma linha)
- APNS_TOPIC       : bundle id, default "com.pigbankai.app"

REGRA APPLE (Anexo 1 §4 do contrato): o texto do push NÃO pode conter valor,
saldo ou qualquer dado financeiro. Mande texto genérico ("Você tem uma novidade
no PigBank") e deixe o detalhe pra dentro do app. `send_push_to_user` não impõe
isso — quem chama é responsável pelo conteúdo.
"""
from __future__ import annotations

import json
import logging
import os
import time

import httpx
import jwt as pyjwt

from db import list_push_tokens, delete_push_token

logger = logging.getLogger(__name__)

_APNS_HOSTS = {
    "production": "https://api.push.apple.com",
    "sandbox": "https://api.sandbox.push.apple.com",
}

# Cache do provider token (JWT). APNs aceita reuso por até 60min.
_token_cache: dict = {"jwt": None, "iat": 0.0}
_TOKEN_TTL = 50 * 60


def _auth_key_pem() -> str | None:
    raw = (os.getenv("APNS_AUTH_KEY") or "").strip()
    if not raw:
        return None
    # Permite colar o .p8 como env de uma linha com "\n" literais.
    return raw.replace("\\n", "\n")


def is_configured() -> bool:
    return bool(
        os.getenv("APNS_KEY_ID")
        and os.getenv("APNS_TEAM_ID")
        and _auth_key_pem()
    )


def _provider_token() -> str | None:
    """JWT ES256 assinado com a .p8, cacheado ~50min."""
    now = time.time()
    if _token_cache["jwt"] and (now - _token_cache["iat"]) < _TOKEN_TTL:
        return _token_cache["jwt"]
    key_id = (os.getenv("APNS_KEY_ID") or "").strip()
    team_id = (os.getenv("APNS_TEAM_ID") or "").strip()
    pem = _auth_key_pem()
    if not (key_id and team_id and pem):
        return None
    try:
        token = pyjwt.encode(
            {"iss": team_id, "iat": int(now)},
            pem,
            algorithm="ES256",
            headers={"alg": "ES256", "kid": key_id},
        )
    except Exception as e:  # chave malformada
        logger.error("APNs: falha ao assinar provider token: %s", e)
        return None
    _token_cache["jwt"] = token
    _token_cache["iat"] = now
    return token


def _topic() -> str:
    return (os.getenv("APNS_TOPIC") or "com.pigbankai.app").strip()


def send_push_to_user(
    user_id: int,
    title: str,
    body: str,
    *,
    data: dict | None = None,
    collapse_id: str | None = None,
) -> int:
    """Envia um push pra todos os aparelhos do usuário.

    Retorna a quantidade de entregas aceitas pelo APNs. Limpa tokens que o APNs
    reporta como inválidos (410 Unregistered / BadDeviceToken). Se o APNs não
    estiver configurado, loga e retorna 0 (dormente).
    """
    if not is_configured():
        logger.info("APNs dormente (envs ausentes) — push pra user %s não enviado.", user_id)
        return 0
    provider = _provider_token()
    if not provider:
        return 0

    tokens = list_push_tokens(user_id)
    if not tokens:
        return 0

    payload = {"aps": {"alert": {"title": title, "body": body}, "sound": "default"}}
    if data:
        payload.update(data)
    body_json = json.dumps(payload)
    topic = _topic()

    sent = 0
    try:
        with httpx.Client(http2=True, timeout=10.0) as client:
            for row in tokens:
                token = row["token"]
                env = row.get("environment") or "production"
                host = _APNS_HOSTS.get(env, _APNS_HOSTS["production"])
                headers = {
                    "authorization": f"bearer {provider}",
                    "apns-topic": topic,
                    "apns-push-type": "alert",
                    "apns-priority": "10",
                }
                if collapse_id:
                    headers["apns-collapse-id"] = collapse_id[:64]
                try:
                    resp = client.post(
                        f"{host}/3/device/{token}", headers=headers, content=body_json
                    )
                except Exception as e:
                    logger.warning("APNs: erro de rede pro token …%s: %s", token[-6:], e)
                    continue
                if resp.status_code == 200:
                    sent += 1
                elif resp.status_code in (400, 410):
                    reason = ""
                    try:
                        reason = (resp.json() or {}).get("reason", "")
                    except Exception:
                        pass
                    if resp.status_code == 410 or reason in ("BadDeviceToken", "Unregistered"):
                        delete_push_token(token)
                        logger.info("APNs: token inválido removido (…%s, %s)", token[-6:], reason)
                    else:
                        logger.warning("APNs 400 pro token …%s: %s", token[-6:], reason)
                else:
                    logger.warning("APNs %s pro token …%s", resp.status_code, token[-6:])
    except Exception as e:
        logger.error("APNs: falha geral no envio pra user %s: %s", user_id, e)
    return sent
