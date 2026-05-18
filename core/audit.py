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
import threading
from typing import Any

from psycopg.types.json import Jsonb

from core.crypto import PiiAccessContext, decrypt_pii_optional
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


# Texto humano apresentado na tela "Atividade da conta" (settings → Segurança).
# Mantenha em sync com AuditEvent.
EVENT_LABELS_PT_BR: dict[str, str] = {
    AuditEvent.PASSWORD_RESET_COMPLETED: "Senha redefinida",
    AuditEvent.EMAIL_CHANGED: "E-mail da conta alterado",
    AuditEvent.MFA_ENABLED: "Autenticação em dois fatores ativada",
    AuditEvent.MFA_DISABLED: "Autenticação em dois fatores desativada",
    AuditEvent.MFA_BACKUP_CODES_REGENERATED: "Códigos de backup do MFA renovados",
    AuditEvent.OPEN_FINANCE_CONNECTED: "Conta bancária conectada via Open Finance",
    AuditEvent.OPEN_FINANCE_DISCONNECTED: "Conta bancária desconectada do Open Finance",
    AuditEvent.LOGIN_FROM_NEW_IP: "Login a partir de um novo dispositivo",
}


def event_label_pt(event: str) -> str:
    """Devolve a label PT-BR de um evento, ou o proprio nome se desconhecido."""
    return EVENT_LABELS_PT_BR.get(event, event)


def list_audit_events(
    user_id: int,
    limit: int = 10,
    before_id: int | None = None,
) -> list[dict]:
    """
    Retorna os ultimos `limit` eventos do usuario (mais recentes primeiro),
    opcionalmente paginando com cursor `before_id`. Cada row vem com
    `event_label` ja traduzido pra PT-BR.
    """
    limit = max(1, min(int(limit), 50))
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                if before_id:
                    cur.execute(
                        """
                        select id, event, ip, user_agent, created_at, details
                        from audit_events
                        where user_id = %s and id < %s
                        order by id desc
                        limit %s
                        """,
                        (user_id, int(before_id), limit),
                    )
                else:
                    cur.execute(
                        """
                        select id, event, ip, user_agent, created_at, details
                        from audit_events
                        where user_id = %s
                        order by id desc
                        limit %s
                        """,
                        (user_id, limit),
                    )
                rows = list(cur.fetchall())
    except Exception as exc:
        print(f"[audit] list_audit_events failed for user {user_id}: {exc}", file=sys.stderr)
        return []

    for row in rows:
        row["event_label"] = event_label_pt(row["event"])
    return rows


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
    notify: bool = True,
) -> None:
    """
    Detecta login a partir de IP nunca visto e grava o evento se for o caso.

    Quando dispara, opcionalmente envia o e-mail "novo login detectado" em
    background (daemon thread) — login nao espera por geolocalizacao/SMTP.
    O PRIMEIRO evento login_from_new_ip de um usuario e suprimido (cobre o
    fluxo cadastro→login imediato; nao avisa o proprio dono que ele acabou
    de criar a conta).

    IMPORTANTE: chame ANTES de registrar o login atual em auth_login_events,
    senao o IP da request corrente passa a ser "conhecido" e o evento nunca
    dispara.
    """
    ip = _client_ip(request)
    ua = _user_agent(request)
    if is_known_login_ip(user_id, ip):
        return
    record_audit_event(
        user_id,
        AuditEvent.LOGIN_FROM_NEW_IP,
        ip=ip,
        user_agent=ua,
        details=details,
    )
    if notify:
        threading.Thread(
            target=_dispatch_new_login_email,
            args=(user_id, ip, ua),
            daemon=True,
        ).start()


def _count_login_from_new_ip(user_id: int) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select count(*) as n from audit_events where user_id = %s and event = %s",
                (user_id, AuditEvent.LOGIN_FROM_NEW_IP),
            )
            row = cur.fetchone()
    if not row:
        return 0
    n = row.get("n") if isinstance(row, dict) else row[0]
    return int(n or 0)


def _user_email(user_id: int) -> str | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select email, email_enc from auth_accounts where user_id = %s", (user_id,))
            row = cur.fetchone()
    if not row:
        return None
    enc = row.get("email_enc") if isinstance(row, dict) else None
    if enc:
        email = decrypt_pii_optional(
            enc,
            ctx=PiiAccessContext(
                purpose="audit_new_login_notice",
                actor="system:audit",
                subject_user_id=user_id,
                field="email",
            ),
        )
    else:
        email = row.get("email") if isinstance(row, dict) else row[0]
    email = (email or "").strip()
    return email or None


def _dispatch_new_login_email(user_id: int, ip: str | None, user_agent: str | None) -> None:
    """
    Envia o aviso de novo login. Suprime o PRIMEIRO evento do usuario
    (cadastro recem-feito). Falha sempre silenciosa.
    """
    try:
        if _count_login_from_new_ip(user_id) <= 1:
            return  # primeiro evento — nao notifica (cadastro→primeiro login)
        email = _user_email(user_id)
        if not email:
            return
        from core.services.ipgeo import lookup_city  # lazy: evita import cycle
        from core.services.email_service import send_new_login_alert

        city = lookup_city(ip)
        send_new_login_alert(to=email, ip=ip, city=city, user_agent=user_agent)
    except Exception as exc:
        print(f"[audit] new-login email dispatch failed for user {user_id}: {exc}", file=sys.stderr)
