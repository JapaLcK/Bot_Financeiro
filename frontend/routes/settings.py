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
from core.crypto import encrypt_pii_optional, hash_pii_optional
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
    set_weekly_report_enabled,
    set_monthly_report_enabled,
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


class AccountResetPayload(BaseModel):
    password: str


class NotificationSettingsPayload(BaseModel):
    engagement_email_enabled: bool | None = None
    tip_email_enabled: bool | None = None
    insight_email_enabled: bool | None = None
    whatsapp_updates_enabled: bool | None = None
    daily_report_enabled: bool | None = None
    daily_report_hour: int | None = None
    daily_report_minute: int | None = None
    weekly_report_enabled: bool | None = None
    monthly_report_enabled: bool | None = None


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
        "weekly_report_enabled": bool(daily_prefs.get("weekly_enabled", True)),
        "monthly_report_enabled": bool(daily_prefs.get("monthly_enabled", True)),
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


@router.post("/settings/reset")
@shared.limiter.limit("5/minute")
async def account_reset_route(request: Request, payload: AccountResetPayload):
    """Recomeçar do zero: apaga dados financeiros e de uso, preserva a conta.

    O user_id vem EXCLUSIVAMENTE da sessão (sem {user_id} no path, padrão de
    frontend/routes/onboarding.py) e NÃO usa authorize_dashboard_access: o
    subscription gate devolveria 402 e travaria a conta free de resetar.
    """
    user_id = shared.resolve_dashboard_user_id(request)
    shared.raise_if_account_scheduled_for_deletion(user_id)

    # Imports na função (padrão do arquivo p/ dependências fora do caminho quente).
    from core.observability import log_system_event_sync
    from db.privacy import PasswordNotSetError, ResetLockUnavailableError, reset_user_data
    from frontend.routes.open_finance import delete_pluggy_items_best_effort

    # O que a limpeza remota ENUMEROU — comparado adiante com o que o DELETE
    # local varreu (RETURNING), para o 2º passe pegar item salvo na janela.
    enumerados: list[str] = []

    def _limpeza_remota() -> None:
        # Hook rodado por reset_user_data DEPOIS da senha e dos locks e ANTES
        # dos deletes locais (invariante: lock ocupado → Pluggy não tocada;
        # ver comentário em db/privacy.py). Best-effort: falha remota loga e
        # segue — mesmo contrato do disconnect. O helper já trata falha por
        # item; este try cobre falha do helper inteiro (e aí `enumerados`
        # fica vazio, o que manda TUDO que foi varrido para o 2º passe —
        # a retentativa certa).
        try:
            enumerados.extend(delete_pluggy_items_best_effort(user_id))
        except Exception as exc:  # noqa: BLE001
            log_system_event_sync(
                "warning", "account_reset_pluggy_cleanup_failed",
                f"Reset do user {user_id}: limpeza remota na Pluggy falhou: {exc}",
                source="settings", user_id=user_id, details={"error": str(exc)[:200]},
            )

    try:
        result = await asyncio.to_thread(
            reset_user_data, user_id, payload.password, remote_cleanup=_limpeza_remota,
        )
    except PasswordNotSetError as exc:
        # ANTES do except PermissionError (é subclasse dele): invertido, o
        # genérico engole e o 409 nunca acontece.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except ResetLockUnavailableError as exc:
        # Mesmo desfecho do lock ocupado na reconexão (_grava_reconexao): 503
        # e o usuário tenta de novo — o reset é recuperável, escrita suja não.
        raise HTTPException(
            status_code=503,
            detail="Não foi possível recomeçar agora: uma sincronização bancária "
                   "está em andamento. Tente de novo em alguns segundos.",
        ) from exc
    except psycopg.OperationalError as exc:
        # A CATEGORIA inteira, não só o `DeadlockDetected`. O reset toma accounts
        # primeiro e pockets/investments no laço; um saque de caixinha faz o
        # contrário, e o Postgres mata quem detectar primeiro (db/privacy.py,
        # comentário do `update accounts`). Mas o deadlock é UMA das folhas:
        # `PoolTimeout`, `QueryCanceled` e `LockNotAvailable` também descendem de
        # `OperationalError`, e o conserto deste PR tornou a primeira mais
        # provável — durante a transação todo `ensure_user` daquele usuário
        # bloqueia SEGURANDO um dos 8 slots do pool sync (db/connection.py:77).
        #
        # Capturar a folha era copiar a forma do precedente e perder a lição
        # dele: `frontend/routes/open_finance.py:393` captura `OperationalError`
        # justamente porque o Codex apontou OITO vezes o mesmo fenômeno por
        # portas diferentes, e nomeia o `DeadlockDetected` como uma delas.
        #
        # Todas abortam a transação INTEIRA — nada local mudou — e todas são
        # temporárias, então o desfecho certo é o mesmo 503 recuperável, em vez
        # do 500 "erro interno" numa condição que a retentativa resolve. E o
        # `remote_cleanup` já rodou (db/privacy.py:593, antes do `get_conn`),
        # então cair em 500 aqui deixaria os items da Pluggy deletados com o
        # banco local intacto — a janela residual que a docstring do
        # `reset_user_data` descreve, com gatilho novo.
        raise HTTPException(
            status_code=503,
            detail="Não foi possível recomeçar agora: outra operação na sua conta "
                   "estava em andamento. Tente de novo em alguns segundos.",
        ) from exc

    # 2º passe remoto (Codex PR #217, 11º): item Pluggy salvo ENTRE a
    # enumeração da limpeza remota e o DELETE local foi varrido do banco sem
    # ser deletado na Pluggy — órfão que bloqueia a reconexão ("já possui
    # conexão com este acesso"). O RETURNING do reset diz o que foi varrido;
    # deleta o que a enumeração não viu (normalmente vazio). Falha aqui cai
    # no teto já documentado: órfão vai ao registry via webhook e a saúde
    # marca ERROR/item_missing.
    tardios = sorted(set(result.pop("pluggy_items_swept", []) or []) - set(enumerados))
    if tardios:
        try:
            await asyncio.to_thread(delete_pluggy_items_best_effort, user_id, tardios)
        except Exception as exc:  # noqa: BLE001 — best-effort, como o 1º passe
            await asyncio.to_thread(
                log_system_event_sync,
                "warning", "account_reset_pluggy_cleanup_failed",
                f"Reset do user {user_id}: 2º passe remoto falhou: {exc}",
                source="settings", user_id=user_id,
                details={"items": tardios, "error": str(exc)[:200]},
            )

    # Mesmo padrão de toda rota de mutação (cards/pockets/launches): sem isto,
    # o snapshot "mês corrente" (TTL 45s) seguia servindo pockets/cartões/OF
    # apagados a outra aba com o dashboard aberto.
    shared.invalidate_dashboard_current_cache(user_id)

    # Dashboards conectados — inclusive noutro DISPOSITIVO, onde o storage
    # event do finbot_reset_at não chega — refazem o mês na hora: reuso
    # deliberado do evento que o dashboard.js já trata (dispara get_month),
    # mesmo best-effort do sync (frontend/routes/open_finance.py). A /home
    # não tem WebSocket — teto aceito: lá a recarga cai no gate.
    try:
        from frontend.finance_bot_websocket_custom import manager

        await manager.broadcast_to_user(
            user_id, json.dumps({"type": "open_finance_synced", "item_id": "account_reset"}),
        )
    except Exception:  # noqa: BLE001 — atualização ao vivo é conveniência, nunca bloqueia o reset
        pass

    await asyncio.to_thread(
        record_audit_event, user_id, AuditEvent.ACCOUNT_RESET, request=request,
    )
    return json.loads(shared.jdump({"ok": True, **result}))


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
                # Os lookups da app usam as colunas *_hash (login por email_hash,
                # auto-link do WhatsApp por phone_hash) — atualizar só a coluna em
                # claro deixa o hash apontando pro valor antigo e o bot nunca
                # reconhece o número novo.
                if email:
                    await cur.execute(
                        """
                        UPDATE auth_accounts
                        SET email = %s,
                            email_hash = %s,
                            email_enc = %s
                        WHERE user_id = %s
                        """,
                        (
                            email,
                            hash_pii_optional(email, kind="email"),
                            encrypt_pii_optional(email),
                            user_id,
                        ),
                    )
                if normalized_phone:
                    await cur.execute(
                        """
                        UPDATE auth_accounts
                        SET phone_e164 = %s,
                            phone_hash = %s,
                            phone_enc = %s,
                            phone_status = 'pending',
                            phone_confirmed_at = NULL,
                            whatsapp_verified_at = NULL
                        WHERE user_id = %s
                        """,
                        (
                            normalized_phone,
                            hash_pii_optional(normalized_phone, kind="phone"),
                            encrypt_pii_optional(normalized_phone),
                            user_id,
                        ),
                    )
                if display_name_provided:
                    await cur.execute(
                        """
                        UPDATE auth_accounts
                        SET display_name = %s,
                            display_name_enc = %s
                        WHERE user_id = %s
                        """,
                        (display_name, encrypt_pii_optional(display_name), user_id),
                    )
            await conn.commit()
        from db_support import invalidate_auth_user_cache
        invalidate_auth_user_cache(user_id)
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

    from db import auth_account_has_password, create_password_reset_token
    from core.services.email_service import send_password_reset_email

    token = await asyncio.to_thread(create_password_reset_token, email)
    if not token:
        raise HTTPException(status_code=404, detail="Conta de e-mail não encontrada.")
    has_password = await asyncio.to_thread(auth_account_has_password, user_id)
    reset_url = f"{shared.DASHBOARD_URL}/reset-password#token={token}"
    sent = await asyncio.to_thread(send_password_reset_email, email.strip().lower(), reset_url, has_password)
    if not sent:
        raise HTTPException(status_code=500, detail="Não foi possível enviar o e-mail de reset.")
    message = (
        "Enviamos um link de redefinição de senha para o seu e-mail."
        if has_password
        else "Enviamos um link para você definir sua senha. Confira seu e-mail."
    )
    return {"ok": True, "message": message}


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

    if payload.weekly_report_enabled is not None:
        await asyncio.to_thread(set_weekly_report_enabled, user_id, payload.weekly_report_enabled)

    if payload.monthly_report_enabled is not None:
        await asyncio.to_thread(set_monthly_report_enabled, user_id, payload.monthly_report_enabled)

    return await _get_notification_settings(user_id)
