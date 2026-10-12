"""Token do link de retomada (docs/plano-remarketing.md §3).

Fernet com chave derivada de `JWT_SECRET` e rótulo PRÓPRIO (o mesmo padrão do `_cipher` de
`core/services/agent_chat.py`): cada uso do segredo ganha a sua chave, então um token de
descadastro, de conversa de agente ou um JWT nunca decifra aqui, e vice-versa. Não é JWT e
nunca vira sessão: só o `GET /retomar` de `frontend/routes/retomada.py` o lê.
O cookie `pb_retomada` leva um token PRÓPRIO (`cookie=True`: rótulo diferente, validade de
2 h, emitido pelo `/retomar` depois de todas as guardas): o token do link nunca serve de cookie.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os

from cryptography.fernet import Fernet, InvalidToken

TTL_S = 7 * 86400
COOKIE_MAX_AGE = 2 * 3600   # fonte única: Max-Age do cookie e validade do token do cookie
ETAPAS = (1, 2, 3)
_ROTULO = b"pigbank:retomada:v1"
_ROTULO_COOKIE = b"pigbank:retomada:cookie:v1"


def _cipher(cookie: bool = False) -> Fernet:
    segredo = os.getenv("JWT_SECRET", "").strip()
    if not segredo:
        raise RuntimeError("JWT_SECRET ausente")
    chave = hmac.new(segredo.encode(), _ROTULO_COOKIE if cookie else _ROTULO, hashlib.sha256).digest()
    return Fernet(base64.urlsafe_b64encode(chave))


def emitir_token(user_id: int, etapa: int, *, cookie: bool = False) -> str:
    if type(etapa) is not int or etapa not in ETAPAS:
        raise ValueError("etapa fora de 1..3")
    corpo = json.dumps({"u": int(user_id), "e": etapa}).encode()
    return _cipher(cookie).encrypt(corpo).decode()


def ler_token(token: str | None, *, cookie: bool = False) -> tuple[int, int] | None:
    """(user_id, etapa) ou None: adulterado, vencido, de outro domínio ou malformado."""
    if not token:
        return None
    try:
        dados = json.loads(_cipher(cookie).decrypt(token.encode(), ttl=COOKIE_MAX_AGE if cookie else TTL_S))
        uid, etapa = dados["u"], dados["e"]
    except (InvalidToken, ValueError, KeyError, TypeError, UnicodeError):
        return None
    if type(uid) is not int or type(etapa) is not int or etapa not in ETAPAS:
        return None
    return uid, etapa


def mascarar_email(email: str) -> str:
    """`l•••@dominio`: o servidor mascara, o cliente nunca vê o e-mail inteiro."""
    local, _, dominio = (email or "").partition("@")
    return f"{local[:1]}•••@{dominio}"
