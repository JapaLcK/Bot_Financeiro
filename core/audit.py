"""
core/audit.py — Audit log estruturado de acoes sensiveis.

Distinto de core/observability.py (system_event_logs):
  - system_event_logs : WARN/ERROR de sistema, logs operacionais (ler no admin)
  - audit_events      : acoes sensiveis na conta de um usuario (forense)

Eventos suportados estao em AuditEvent. Use-os em vez de strings cruas para
evitar typos.

Falha sempre silenciosa: a auditoria nao pode quebrar o fluxo principal
(uma queda de DB durante o registro nao deve impedir uma troca de senha).
"""
from __future__ import annotations

import sys
from typing import Any

from psycopg.types.json import Jsonb

from db.connection import get_conn


class AuditEvent:
    PASSWORD_RESET_COMPLETED = "password_reset_completed"
    EMAIL_CHANGED = "email_changed"
    MFA_ENABLED = "mfa_enabled"
    MFA_DISABLED = "mfa_disabled"
    MFA_BACKUP_CODES_REGENERATED = "mfa_backup_codes_regenerated"
    OPEN_FINANCE_CONNECTED = "open_finance_connected"
    OPEN_FINANCE_DISCONNECTED = "open_finance_disconnected"
    LOGIN_FROM_NEW_IP = "login_from_new_ip"


def _client_ip(request: Any) -> str | None:
    if request is None:
        return None
    headers = getattr(request, "headers", None)
    if headers is not None:
        fwd = (headers.get("x-forwarded-for") or "").split(",")[0].strip()
        if fwd:
            return fwd
    client = getattr(request, "client", None)
    return getattr(client, "host", None) if client else None


def _user_agent(request: Any) -> str | None:
    if request is None:
        return None
    headers = getattr(request, "headers", None)
    if headers is None:
        return None
    ua = (headers.get("user-agent") or "").strip()
    return ua[:512] or None


def record_audit_event(
    user_id: int,
    event: str,
    *,
    request: Any = None,
    ip: str | None = None,
    user_agent: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """
    Grava uma linha em audit_events. Falha silenciosa.

    Nunca passe segredos em `details` (senha, TOTP, codigos).
    """
    if ip is None:
        ip = _client_ip(request)
    if user_agent is None:
        user_agent = _user_agent(request)

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into audit_events (user_id, event, ip, user_agent, details)
                    values (%s, %s, %s, %s, %s)
                    """,
                    (user_id, event, ip, user_agent, Jsonb(details or {})),
                )
            conn.commit()
    except Exception as exc:
        print(f"[audit] failed to record {event} for user {user_id}: {exc}", file=sys.stderr)


def is_known_login_ip(user_id: int, ip: str | None) -> bool:
    """
    True se o user ja teve login totalmente bem-sucedido vindo desse IP.

    Consulta auth_login_events (fonte de verdade ja existente).
    Filtra somente success=true com failure_reason IS NULL — i.e. ignora
    rows tipo `mfa_pending` que sao apenas "senha correta, aguardando MFA"
    e nao representam um login completo.

    Sem IP -> True (nao alarmar quando nao se sabe).
    Erro -> True (preferir nao alarmar a registrar falso positivo).
    """
    if not ip:
        return True
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select 1 from auth_login_events
                    where user_id = %s
                      and success = true
                      and failure_reason is null
                      and ip_address = %s
                    limit 1
                    """,
                    (user_id, ip),
                )
                return cur.fetchone() is not None
    except Exception as exc:
        print(f"[audit] is_known_login_ip query failed: {exc}", file=sys.stderr)
        return True


def maybe_record_login_from_new_ip(
    user_id: int,
    *,
    request: Any = None,
    details: dict[str, Any] | None = None,
) -> None:
    """
    Detecta login a partir de IP nunca visto e grava o evento se for o caso.

    IMPORTANTE: chame ANTES de registrar o login atual em auth_login_events,
    senao o IP da request corrente passa a ser "conhecido" e o evento nunca
    dispara.
    """
    ip = _client_ip(request)
    if is_known_login_ip(user_id, ip):
        return
    record_audit_event(
        user_id,
        AuditEvent.LOGIN_FROM_NEW_IP,
        request=request,
        details=details,
    )
