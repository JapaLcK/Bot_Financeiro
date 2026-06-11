"""Rotas de configurações da conta — segurança, sessões e notificações.

Etapa 3 do refactor Fase 1 (docs/refactor_plan.md): movidas de
finance_bot_websocket_custom.py sem mudança de comportamento.
"""

import asyncio
import json

import psycopg
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core.audit import AuditEvent, list_audit_events, record_audit_event
from core.sessions import (
    device_label,
    list_user_sessions,
    revoke_other_sessions,
    revoke_session,
)
from db import (
    get_auth_user,
    get_daily_report_prefs,
    list_identities_by_user,
    set_daily_report_enabled,
    set_daily_report_hour,
    set_engagement_opt_out,
    set_insight_email_opt_out,
    set_tip_email_opt_out,
    set_whatsapp_updates_opt_out,
    sync_engagement_opt_out,
)
from frontend.routes import shared
from utils_phone import normalize_phone_e164

router = APIRouter()


class SecurityContactPayload(BaseModel):
    email: str | None = None
    phone: str | None = None
    display_name: str | None = None


class NotificationSettingsPayload(BaseModel):
    engagement_email_enabled: bool | None = None
    tip_email_enabled: bool | None = None
    insight_email_enabled: bool | None = None
    whatsapp_updates_enabled: bool | None = None
    daily_report_enabled: bool | None = None
    daily_report_hour: int | None = None
    daily_report_minute: int | None = None


async def _get_notification_settings(user_id: int) -> dict:
    auth_user, daily_prefs = await asyncio.gather(
        asyncio.to_thread(get_auth_user, user_id),
        asyncio.to_thread(get_daily_report_prefs, user_id),
    )
    auth_user = auth_user or {}
    daily_prefs = daily_prefs or {}
    email = auth_user.get("email")
    phone = auth_user.get("phone_e164")
    email_available = bool(email)
    whatsapp_updates_available = bool(phone)
    engagement_opt_out = bool(auth_user.get("engagement_opt_out", False))
    tip_email_enabled = email_available and not engagement_opt_out and not bool(auth_user.get("tip_email_opt_out", False))
    insight_email_enabled = email_available and not engagement_opt_out and not bool(auth_user.get("insight_email_opt_out", False))
    whatsapp_updates_enabled = whatsapp_updates_available and not bool(auth_user.get("whatsapp_updates_opt_out", False))
    return {
        "ok": True,
        "email": email,
        "whatsapp_destination": phone,
        "email_notifications_available": email_available,
        "whatsapp_updates_available": whatsapp_updates_available,
        "engagement_email_enabled": tip_email_enabled or insight_email_enabled,
        "tip_email_enabled": tip_email_enabled,
        "insight_email_enabled": insight_email_enabled,
        "whatsapp_updates_enabled": whatsapp_updates_enabled,
        "daily_report_enabled": bool(daily_prefs.get("enabled", True)),
        "daily_report_hour": int(daily_prefs.get("hour", 9)),
        "daily_report_minute": int(daily_prefs.get("minute", 0)),
    }


async def _get_security_settings(user_id: int) -> dict:
    auth_user, identities = await asyncio.gather(
        asyncio.to_thread(get_auth_user, user_id),
        asyncio.to_thread(list_identities_by_user, user_id),
    )
    auth_user = auth_user or {}
    identities = identities or []
    whatsapp_identity = next((i for i in identities if i.get("provider") == "whatsapp"), None)
    phone = auth_user.get("phone_e164") or (whatsapp_identity or {}).get("external_id")
    return json.loads(shared.jdump({
        "ok": True,
        "user_id": user_id,
        "email": auth_user.get("email"),
        "display_name": auth_user.get("display_name"),
        "phone": phone,
        "phone_status": auth_user.get("phone_status"),
        "phone_confirmed_at": auth_user.get("phone_confirmed_at"),
        "whatsapp_verified_at": auth_user.get("whatsapp_verified_at"),
        "plan": auth_user.get("plan"),
        "plan_expires_at": auth_user.get("plan_expires_at"),
        "created_at": auth_user.get("created_at"),
        "identities": identities,
    }))


def _current_session_jti(request: Request) -> str | None:
    """Le o jti da sessao corrente a partir do cookie auth_token. None se ausente/legado."""
    token = shared.get_auth_token_from_request(request, None)
    if not token:
        return None
    payload = shared.decode_jwt(token)
    if not payload or payload.get("type") != "auth":
        return None
    return payload.get("jti")


@router.get("/settings/{user_id}/security")
async def security_settings_route(request: Request, user_id: int):
    shared.authorize_dashboard_access(request, user_id)
    return await _get_security_settings(user_id)


@router.patch("/settings/{user_id}/security/contact")
async def update_security_contact_route(
    request: Request,
    user_id: int,
    payload: SecurityContactPayload,
):
    shared.authorize_dashboard_access(request, user_id)
    auth_user = await asyncio.to_thread(get_auth_user, user_id)
    if not auth_user:
        raise HTTPException(status_code=400, detail="Esta conta ainda não tem login por e-mail configurado.")

    email = payload.email.strip().lower() if payload.email else None
    phone = (payload.phone or "").strip() or None

    display_name_raw = payload.display_name
    display_name_provided = display_name_raw is not None
    display_name: str | None = None
    if display_name_provided:
        display_name = display_name_raw.strip()
        if display_name == "":
            display_name = None  # remove o nome
        else:
            if len(display_name) > 50:
                raise HTTPException(status_code=400, detail="O nome deve ter no máximo 50 caracteres.")
            if len(display_name) < 2:
                raise HTTPException(status_code=400, detail="O nome deve ter pelo menos 2 caracteres.")

    if not email and not phone and not display_name_provided:
        raise HTTPException(status_code=400, detail="Informe e-mail, telefone ou nome.")
    if email and ("@" not in email or "." not in email.rsplit("@", 1)[-1]):
        raise HTTPException(status_code=400, detail="E-mail inválido.")

    normalized_phone = None
    if phone:
        try:
            normalized_phone = normalize_phone_e164(phone)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    old_email = (auth_user.get("email") or "").strip().lower() or None
    email_actually_changed = bool(email) and email != old_email

    try:
        async with await shared.db_connect() as conn:
            async with conn.cursor() as cur:
                if email:
                    await cur.execute(
                        "UPDATE auth_accounts SET email = %s WHERE user_id = %s",
                        (email, user_id),
                    )
                if normalized_phone:
                    await cur.execute(
                        """
                        UPDATE auth_accounts
                        SET phone_e164 = %s,
                            phone_status = 'pending',
                            phone_confirmed_at = NULL,
                            whatsapp_verified_at = NULL
                        WHERE user_id = %s
                        """,
                        (normalized_phone, user_id),
                    )
                if display_name_provided:
                    await cur.execute(
                        "UPDATE auth_accounts SET display_name = %s WHERE user_id = %s",
                        (display_name, user_id),
                    )
            await conn.commit()
    except psycopg.errors.UniqueViolation as exc:
        raise HTTPException(status_code=409, detail="Este e-mail ou telefone já está em uso.") from exc

    if email_actually_changed:
        await asyncio.to_thread(
            record_audit_event,
            user_id,
            AuditEvent.EMAIL_CHANGED,
            request=request,
            details={"new_email": email},
        )

    return await _get_security_settings(user_id)


@router.post("/settings/{user_id}/password-reset")
@shared.limiter.limit("3/minute")
async def security_password_reset_route(request: Request, user_id: int):
    shared.authorize_dashboard_access(request, user_id)
    auth_user = await asyncio.to_thread(get_auth_user, user_id)
    email = (auth_user or {}).get("email")
    if not email:
        raise HTTPException(status_code=400, detail="Adicione um e-mail antes de resetar a senha.")

    from db import create_password_reset_token
    from core.services.email_service import send_password_reset_email

    token = await asyncio.to_thread(create_password_reset_token, email)
    if not token:
        raise HTTPException(status_code=404, detail="Conta de e-mail não encontrada.")
    reset_url = f"{shared.DASHBOARD_URL}/reset-password#token={token}"
    sent = await asyncio.to_thread(send_password_reset_email, email.strip().lower(), reset_url)
    if not sent:
        raise HTTPException(status_code=500, detail="Não foi possível enviar o e-mail de reset.")
    return {"ok": True, "message": "Enviamos um link de redefinição de senha para o seu e-mail."}


@router.get("/settings/{user_id}/activity")
async def security_activity_route(
    request: Request,
    user_id: int,
    limit: int = 10,
    before_id: int | None = None,
):
    """Lista os ultimos eventos de auditoria do usuario (Atividade da conta)."""
    shared.authorize_dashboard_access(request, user_id)
    rows = await asyncio.to_thread(list_audit_events, user_id, limit, before_id)
    next_before = rows[-1]["id"] if rows and len(rows) >= max(1, min(int(limit), 50)) else None
    return json.loads(shared.jdump({"ok": True, "events": rows, "next_before": next_before}))


@router.get("/settings/{user_id}/sessions")
async def security_sessions_list_route(request: Request, user_id: int):
    """Lista as sessoes ativas (dispositivos conectados) do usuario."""
    shared.authorize_dashboard_access(request, user_id)
    current_jti = _current_session_jti(request)
    rows = await asyncio.to_thread(list_user_sessions, user_id)
    sessions = []
    for r in rows:
        sessions.append({
            "jti": r["jti"],
            "device_label": device_label(r.get("user_agent")),
            "ip": r.get("ip"),
            "user_agent": r.get("user_agent"),
            "created_at": r.get("created_at"),
            "last_seen_at": r.get("last_seen_at"),
            "is_current": r["jti"] == current_jti,
        })
    return json.loads(shared.jdump({"ok": True, "sessions": sessions, "current_jti": current_jti}))


@router.delete("/settings/{user_id}/sessions/{jti}")
async def security_session_revoke_route(request: Request, user_id: int, jti: str):
    """Revoga uma sessao especifica (que nao seja a corrente)."""
    shared.authorize_dashboard_access(request, user_id)
    current_jti = _current_session_jti(request)
    if current_jti and jti == current_jti:
        raise HTTPException(
            status_code=400,
            detail="Use o botão 'Sair' para encerrar a sessão atual.",
        )
    revoked = await asyncio.to_thread(revoke_session, user_id, jti)
    if not revoked:
        raise HTTPException(status_code=404, detail="Sessão não encontrada ou já encerrada.")
    return {"ok": True}


@router.delete("/settings/{user_id}/sessions")
async def security_sessions_revoke_others_route(request: Request, user_id: int):
    """Revoga todas as sessoes do usuario exceto a corrente."""
    shared.authorize_dashboard_access(request, user_id)
    current_jti = _current_session_jti(request)
    revoked_count = await asyncio.to_thread(revoke_other_sessions, user_id, current_jti)
    return {"ok": True, "revoked": revoked_count}


@router.get("/settings/{user_id}/notifications")
async def notification_settings_route(request: Request, user_id: int):
    shared.authorize_dashboard_access(request, user_id)
    return await _get_notification_settings(user_id)


@router.patch("/settings/{user_id}/notifications")
async def update_notification_settings_route(
    request: Request,
    user_id: int,
    payload: NotificationSettingsPayload,
):
    shared.authorize_dashboard_access(request, user_id)

    touches_email_prefs = (
        payload.engagement_email_enabled is not None
        or payload.tip_email_enabled is not None
        or payload.insight_email_enabled is not None
    )
    if touches_email_prefs:
        auth_user = await asyncio.to_thread(get_auth_user, user_id)
        if not auth_user or not auth_user.get("email"):
            raise HTTPException(status_code=400, detail="Vincule um e-mail para configurar notificações por e-mail.")

    if payload.engagement_email_enabled is not None:
        await asyncio.to_thread(set_engagement_opt_out, user_id, not payload.engagement_email_enabled)

    if payload.tip_email_enabled is not None:
        await asyncio.to_thread(set_tip_email_opt_out, user_id, not payload.tip_email_enabled)

    if payload.insight_email_enabled is not None:
        await asyncio.to_thread(set_insight_email_opt_out, user_id, not payload.insight_email_enabled)

    if payload.tip_email_enabled is not None or payload.insight_email_enabled is not None:
        await asyncio.to_thread(sync_engagement_opt_out, user_id)

    if payload.whatsapp_updates_enabled is not None:
        auth_user = await asyncio.to_thread(get_auth_user, user_id)
        if not auth_user or not auth_user.get("phone_e164"):
            raise HTTPException(status_code=400, detail="Vincule um WhatsApp para receber atualizações.")
        await asyncio.to_thread(set_whatsapp_updates_opt_out, user_id, not payload.whatsapp_updates_enabled)

    if payload.daily_report_hour is not None or payload.daily_report_minute is not None:
        current = await asyncio.to_thread(get_daily_report_prefs, user_id)
        hour = payload.daily_report_hour if payload.daily_report_hour is not None else int(current.get("hour", 9))
        minute = payload.daily_report_minute if payload.daily_report_minute is not None else int(current.get("minute", 0))
        if not 0 <= int(hour) <= 23:
            raise HTTPException(status_code=400, detail="Hora inválida.")
        if not 0 <= int(minute) <= 59:
            raise HTTPException(status_code=400, detail="Minuto inválido.")
        await asyncio.to_thread(set_daily_report_hour, user_id, int(hour), int(minute))

    if payload.daily_report_enabled is not None:
        await asyncio.to_thread(set_daily_report_enabled, user_id, payload.daily_report_enabled)

    return await _get_notification_settings(user_id)
