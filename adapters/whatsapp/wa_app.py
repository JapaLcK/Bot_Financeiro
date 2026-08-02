from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, PlainTextResponse

from adapters.whatsapp.wa_client import send_template
from adapters.whatsapp.wa_parse import InboundMessage, extract_messages
from adapters.whatsapp.wa_runtime import process_payload, verify_webhook_signature
from config.env import load_app_env
from core.observability import log_system_event_sync
from core.reports.reports_daily import (
    build_daily_report_summary,
    build_daily_report_text,
    build_due_bill_reminders,
    build_weekly_report_summary,
    build_monthly_report_summary,
)
from db import (
    claim_daily_report_send,
    claim_weekly_report_send,
    claim_monthly_report_send,
    get_daily_report_prefs,
    list_identities_by_user,
    list_users_with_daily_report_enabled,
    list_users_with_weekly_report_enabled,
    list_users_with_monthly_report_enabled,
    mark_card_reminder_sent,
)
from utils_phone import phone_lookup_candidates
from utils_date import now_tz

load_app_env()

logger = logging.getLogger(__name__)

VERIFY_TOKEN = (os.getenv("WA_VERIFY_TOKEN") or "").strip()
APP_SECRET = (os.getenv("WA_APP_SECRET") or "").strip()
if (os.getenv("APP_ENV") or "").strip().lower() == "prod" and not APP_SECRET:
    raise RuntimeError(
        "WA_APP_SECRET is required when APP_ENV=prod: webhook signature verification must not be bypassed."
    )
_queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=500)
WA_DAILY_REPORT_DISABLE_ID = "daily_report_disable"
WA_WEEKLY_REPORT_DISABLE_ID = "weekly_report_disable"
WA_MONTHLY_REPORT_DISABLE_ID = "monthly_report_disable"


def _env_flag(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _serialize_simulated_message(message: InboundMessage) -> dict[str, Any]:
    return {
        "wa_id": message.wa_id,
        "text": message.text,
        "timestamp": message.timestamp,
        "attachments": [
            {
                "media_id": attachment.media_id,
                "filename": attachment.filename,
                "content_type": attachment.content_type,
            }
            for attachment in message.attachments
        ],
        "raw_type": (message.raw or {}).get("type"),
    }


def _runtime_instance_details() -> dict[str, str | int]:
    return {
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
    }


def _dedupe_whatsapp_targets(ids: list[dict]) -> list[str]:
    seen: set[str] = set()
    targets: list[str] = []

    for item in ids:
        if item.get("provider") != "whatsapp":
            continue

        raw = (item.get("external_id") or "").strip()
        if not raw:
            continue

        try:
            candidates = phone_lookup_candidates(raw)
            normalized = max(candidates, key=len)
        except Exception:
            candidates = [raw]
            normalized = raw

        if any(candidate in seen for candidate in candidates):
            continue

        seen.update(candidates)
        targets.append(normalized)

    return targets


def _proactive_template_config() -> dict[str, str] | None:
    template_name = (os.getenv("WA_PROACTIVE_TEMPLATE_NAME") or "").strip()
    if not template_name:
        return None

    return {
        "name": template_name,
        "language_code": (os.getenv("WA_PROACTIVE_TEMPLATE_LANGUAGE") or "pt_BR").strip(),
    }


def _proactive_template_includes_report() -> bool:
    return os.getenv("WA_PROACTIVE_TEMPLATE_INCLUDE_REPORT", "0").strip() == "1"


def _proactive_template_stop_button_enabled() -> bool:
    return os.getenv("WA_PROACTIVE_TEMPLATE_STOP_BUTTON", "0").strip() == "1"


def _proactive_template_named_body_params(user_id: int) -> dict[str, str] | None:
    if not _proactive_template_includes_report():
        return None

    summary = build_daily_report_summary(user_id)
    return {
        "saldo": summary["saldo"],
        "gastos": summary["gastos"],
        "receita": summary["receita"],
        "lancamentos": summary["lancamentos"],
    }


def _proactive_template_quick_reply_buttons() -> list[dict] | None:
    if not _proactive_template_stop_button_enabled():
        return None
    return [{"index": 0, "payload": WA_DAILY_REPORT_DISABLE_ID}]


# ── Resumos periódicos (semanal / mensal) via template proativo ───────────────
#
# Envios proativos no WhatsApp (fora da janela de 24h) exigem template aprovado
# pela Meta. Cada período usa seu próprio template, configurado por env var:
#   WA_WEEKLY_TEMPLATE_NAME   → enviado toda segunda-feira (semana anterior)
#   WA_MONTHLY_TEMPLATE_NAME  → enviado todo dia 1º (mês anterior)
# O idioma reaproveita WA_PROACTIVE_TEMPLATE_LANGUAGE (default pt_BR).
#
# O corpo do template deve declarar estes parâmetros nomeados:
#   {{periodo}} {{saldo}} {{gastos}} {{receita}} {{lancamentos}}

def _periodic_template_config(kind: str) -> dict[str, str] | None:
    env_name = "WA_WEEKLY_TEMPLATE_NAME" if kind == "weekly" else "WA_MONTHLY_TEMPLATE_NAME"
    template_name = (os.getenv(env_name) or "").strip()
    if not template_name:
        return None

    return {
        "name": template_name,
        "language_code": (os.getenv("WA_PROACTIVE_TEMPLATE_LANGUAGE") or "pt_BR").strip(),
    }


def _periodic_template_named_body_params(summary: dict[str, str]) -> dict[str, str]:
    return {
        "periodo": f"{summary['start']} a {summary['end']}",
        "saldo": summary["saldo"],
        "gastos": summary["gastos"],
        "receita": summary["receita"],
        "lancamentos": summary["lancamentos"],
    }


def _periodic_template_stop_button_enabled() -> bool:
    # ligue quando os templates semanal/mensal tiverem o botão de resposta rápida
    # "Desligar" (quick reply). Independente do botão do report diário.
    return (os.getenv("WA_PERIODIC_TEMPLATE_STOP_BUTTON", "0") or "").strip() == "1"


def _periodic_template_quick_reply_buttons(kind: str) -> list[dict] | None:
    if not _periodic_template_stop_button_enabled():
        return None
    payload = WA_WEEKLY_REPORT_DISABLE_ID if kind == "weekly" else WA_MONTHLY_REPORT_DISABLE_ID
    return [{"index": 0, "payload": payload}]


def _strip_daily_report_disable_hint(message: str) -> str:
    lines = (message or "").splitlines()
    filtered = [
        line for line in lines
        if line.strip() not in {
            "⚙️ Para desligar o report diário automatico:",
            "*desligar report diario*",
        }
    ]

    while filtered and not filtered[-1].strip():
        filtered.pop()

    return "\n".join(filtered).strip()


async def wa_verify(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if not VERIFY_TOKEN:
        logger.error("WA_VERIFY_TOKEN is not configured")
        log_system_event_sync(
            "error",
            "whatsapp_verify_token_missing",
            "WA_VERIFY_TOKEN nao configurado para verificacao do webhook.",
            source="wa_app",
        )
        return PlainTextResponse("forbidden", status_code=403)

    if mode == "subscribe" and token == VERIFY_TOKEN and challenge:
        log_system_event_sync(
            "info",
            "whatsapp_webhook_verified",
            "Webhook do WhatsApp validado com sucesso.",
            source="wa_app",
        )
        return PlainTextResponse(challenge)
    log_system_event_sync(
        "warning",
        "whatsapp_webhook_verify_failed",
        "Tentativa de verificacao do webhook do WhatsApp falhou.",
        source="wa_app",
        details={"mode": mode, "token_present": bool(token)},
    )
    return PlainTextResponse("forbidden", status_code=403)


async def wa_webhook(request: Request):
    raw = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not verify_webhook_signature(raw, signature, APP_SECRET):
        logger.warning("WA webhook forbidden: invalid signature")
        log_system_event_sync(
            "warning",
            "whatsapp_webhook_invalid_signature",
            "Webhook do WhatsApp rejeitado por assinatura invalida.",
            source="wa_app",
        )
        return PlainTextResponse("forbidden", status_code=403)

    payload = json.loads(raw.decode("utf-8"))
    try:
        value = payload.get("entry", [{}])[0].get("changes", [{}])[0].get("value", {})
        statuses = value.get("statuses") or []
        messages = value.get("messages") or []
        log_system_event_sync(
            "info",
            "whatsapp_webhook_received",
            "Webhook do WhatsApp recebido.",
            source="wa_app",
            details={
                "field": payload.get("entry", [{}])[0].get("changes", [{}])[0].get("field"),
                "messages": len(messages),
                "statuses": len(statuses),
            },
        )
        if not messages and not statuses:
            print(
                f"[DEBUG] webhook payload without messages/statuses: keys={list(value.keys())} value={value}",
                flush=True,
            )
        for status in statuses:
            if status.get("errors"):
                log_system_event_sync(
                    "warning",
                    "whatsapp_status_error",
                    "Status de mensagem do WhatsApp retornou erro.",
                    source="wa_app",
                    details={
                        "status": status.get("status"),
                        "recipient_id": status.get("recipient_id"),
                        "errors": status.get("errors"),
                    },
                )
    except Exception:
        logger.info("WA webhook received: unable to summarize payload")
    try:
        _queue.put_nowait(payload)
    except asyncio.QueueFull:
        logger.warning("WA queue full, dropping payload")
        log_system_event_sync(
            "error",
            "whatsapp_queue_drop",
            "Fila interna do WhatsApp lotou e o payload foi descartado.",
            source="wa_app",
            details={"queue_maxsize": _queue.maxsize},
        )
        return JSONResponse({"ok": True, "dropped": True})

    return JSONResponse({"ok": True})


async def wa_simulate(payload: dict):
    if _env_flag("WA_SIMULATION_ONLY"):
        messages = extract_messages(payload)
        return {
            "ok": True,
            "simulation_only": True,
            "processed_messages": 0,
            "would_process_messages": len(messages),
            "messages": [_serialize_simulated_message(message) for message in messages],
        }

    count = await asyncio.to_thread(process_payload, payload)
    return {"ok": True, "simulation_only": False, "processed_messages": count}


async def _worker_loop():
    while True:
        payload = await _queue.get()
        try:
            await asyncio.to_thread(process_payload, payload)
        except Exception as exc:
            logger.exception("WA worker error: %s", exc)
            log_system_event_sync(
                "error",
                "whatsapp_worker_error",
                f"Erro no worker do WhatsApp: {exc}",
                source="wa_app",
            )
        finally:
            _queue.task_done()


def _daily_report_tick() -> None:
    now = now_tz()
    today = now.date()
    instance = _runtime_instance_details()

    for uid in list_users_with_daily_report_enabled():
        prefs = get_daily_report_prefs(uid)
        if not prefs["enabled"]:
            continue

        hour = prefs["hour"]
        minute = prefs["minute"]
        if (now.hour, now.minute) < (hour, minute):
            continue
        ids = list_identities_by_user(uid)
        wa_targets = _dedupe_whatsapp_targets(ids)
        proactive_template = _proactive_template_config()

        if wa_targets and not proactive_template:
            logger.warning("WA daily report skipped uid=%s: WA_PROACTIVE_TEMPLATE_NAME nao configurado", uid)
            log_system_event_sync(
                "warning",
                "whatsapp_proactive_template_missing",
                "Relatorio diario via WhatsApp ignorado: WA_PROACTIVE_TEMPLATE_NAME nao configurado.",
                source="wa_app",
                user_id=uid,
                details={"targets": len(wa_targets)},
            )
            continue

        if not claim_daily_report_send(uid, today):
            continue

        message = _strip_daily_report_disable_hint(build_daily_report_text(uid))
        reminders = build_due_bill_reminders(uid, today)

        for to in wa_targets:
            try:
                template_body_params = _proactive_template_named_body_params(uid)
                send_template(
                    to,
                    proactive_template["name"],
                    language_code=proactive_template["language_code"],
                    named_body_params=template_body_params,
                    quick_reply_buttons=_proactive_template_quick_reply_buttons(),
                )
                logger.info(
                    "WA daily report proactive template sent uid=%s to=%s reminders=%s pid=%s hostname=%s",
                    uid,
                    to,
                    len(reminders),
                    instance["pid"],
                    instance["hostname"],
                )
                log_system_event_sync(
                    "info",
                    "whatsapp_daily_report_template_sent",
                    "Template proativo de relatorio diario enviado para o WhatsApp.",
                    source="wa_app",
                    user_id=uid,
                    details={
                        "to": to,
                        "template_name": proactive_template["name"],
                        "language_code": proactive_template["language_code"],
                        "included_report_param": bool(template_body_params),
                    },
                )
                logger.info(
                    "WA daily report sent uid=%s to=%s reminders=%s pid=%s hostname=%s",
                    uid,
                    to,
                    len(reminders),
                    instance["pid"],
                    instance["hostname"],
                )
            except Exception as exc:
                logger.warning("WA daily report send error to=%s error=%s", to, exc)
                log_system_event_sync(
                    "warning",
                    "whatsapp_daily_report_send_failed",
                    f"Falha ao enviar relatorio diario via WhatsApp: {exc}",
                    source="wa_app",
                    user_id=uid,
                    details={"to": to},
                )

        if reminders:
            logger.info(
                "WA card reminders not marked uid=%s: proactive template uses compact daily report only",
                uid,
            )
            continue

        for reminder in reminders:
            try:
                mark_card_reminder_sent(uid, reminder["card_id"], today)
            except Exception as exc:
                logger.warning("WA card reminder mark error uid=%s card_id=%s error=%s", uid, reminder["card_id"], exc)
                log_system_event_sync(
                    "warning",
                    "whatsapp_card_reminder_mark_failed",
                    f"Falha ao marcar lembrete de cartao enviado: {exc}",
                    source="wa_app",
                    user_id=uid,
                    details={"card_id": reminder["card_id"]},
                )


# ---------------------------------------------------------------------------
# Lembretes de CONTAS A PAGAR (boletos)
#
# Proativo → WhatsApp NÃO deixa mandar texto livre fora da janela de 24h, só
# TEMPLATE aprovado pela Meta. Por isso este loop fica DORMENTE até
# WA_BILL_REMINDER_TEMPLATE_NAME estar setado (o template que o Lucas vai criar
# e submeter à Meta). Sem a env, _bill_reminder_tick retorna na hora — nada é
# enviado. Ver db/bills.py (list_due_bill_reminders) e o spec do template no
# fim deste arquivo / na entrega ao Lucas.
# ---------------------------------------------------------------------------

def _bill_reminder_template_config() -> dict[str, str] | None:
    name = (os.getenv("WA_BILL_REMINDER_TEMPLATE_NAME") or "").strip()
    if not name:
        return None
    return {
        "name": name,
        "language_code": (os.getenv("WA_BILL_REMINDER_TEMPLATE_LANGUAGE") or "pt_BR").strip(),
    }


def _bill_reminder_named_params(bill: dict, today) -> dict[str, str]:
    """Params do corpo do template (named). O template da Meta deve declarar as
    variáveis {{conta}}, {{valor}} e {{vencimento}}."""
    from utils_text import fmt_brl

    due = bill.get("due_date")
    amount = bill.get("amount")
    try:
        valor = fmt_brl(float(amount)) if amount is not None else "—"
    except (TypeError, ValueError):
        valor = "—"
    # Conta de valor variável (água/luz): o valor cadastrado é só estimativa;
    # sem estimativa (0) mostra "a confirmar".
    if bill.get("variable_amount"):
        try:
            has_est = amount is not None and float(amount) > 0
        except (TypeError, ValueError):
            has_est = False
        valor = f"~{valor} (varia)" if has_est else "a confirmar"
    try:
        vencimento = due.strftime("%d/%m") if due else "—"
    except Exception:
        vencimento = str(due or "—")
    return {
        "conta": str(bill.get("name") or "sua conta"),
        "valor": valor,
        "vencimento": vencimento,
    }


def _bill_reminder_tick() -> None:
    cfg = _bill_reminder_template_config()
    if not cfg:
        return  # dormente: template Meta ainda não configurado

    now = now_tz()
    send_hour = int(os.getenv("WA_BILL_REMINDER_HOUR", "9") or 9)
    if now.hour < send_hour:
        return  # manda de manhã (>= hora configurada), 1x/dia por conta

    today = now.date()
    days_before = int(os.getenv("WA_BILL_REMINDER_DAYS_BEFORE", "3") or 3)

    from db.bills import (
        list_users_with_pending_bills,
        list_due_bill_reminders,
        mark_bill_reminder_sent,
    )

    for uid in list_users_with_pending_bills():
        try:
            due = list_due_bill_reminders(uid, today, days_before=days_before)
        except Exception as exc:
            logger.warning("WA bill reminder query error uid=%s error=%s", uid, exc)
            continue
        if not due:
            continue

        wa_targets = _dedupe_whatsapp_targets(list_identities_by_user(uid))
        if not wa_targets:
            continue

        for bill in due:
            params = _bill_reminder_named_params(bill, today)
            # botão "✅ Já paguei" carrega o id da conta → quita exatamente ela.
            from adapters.whatsapp.wa_runtime import WA_BILL_PAID_PREFIX
            buttons = [{"index": 0, "payload": f"{WA_BILL_PAID_PREFIX}{bill.get('id')}"}]
            sent_any = False
            for to in wa_targets:
                try:
                    send_template(
                        to,
                        cfg["name"],
                        language_code=cfg["language_code"],
                        named_body_params=params,
                        quick_reply_buttons=buttons,
                    )
                    sent_any = True
                    log_system_event_sync(
                        "info",
                        "whatsapp_bill_reminder_sent",
                        "Lembrete de conta a pagar enviado via template WhatsApp.",
                        source="wa_app",
                        user_id=uid,
                        details={"to": to, "bill_id": bill.get("id"), "template_name": cfg["name"]},
                    )
                except Exception as exc:
                    logger.warning("WA bill reminder send error uid=%s to=%s error=%s", uid, to, exc)
            if sent_any:
                try:
                    mark_bill_reminder_sent(int(bill["id"]), today)
                except Exception as exc:
                    logger.warning("WA bill reminder mark error uid=%s bill_id=%s error=%s", uid, bill.get("id"), exc)


async def _bill_reminder_loop():
    await asyncio.sleep(8)
    while True:
        try:
            await asyncio.to_thread(_bill_reminder_tick)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.exception("WA bill reminder loop error: %s", exc)
            log_system_event_sync(
                "error",
                "whatsapp_bill_reminder_loop_error",
                f"Erro no loop de lembretes de contas a pagar: {exc}",
                source="wa_app",
            )
        await asyncio.sleep(60 * 5)


async def _daily_report_loop():
    await asyncio.sleep(5)

    while True:
        try:
            await asyncio.to_thread(_daily_report_tick)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.exception("WA daily report loop error: %s", exc)
            log_system_event_sync(
                "error",
                "whatsapp_daily_report_loop_error",
                f"Erro no loop de relatorio diario do WhatsApp: {exc}",
                source="wa_app",
            )

        await asyncio.sleep(30)


def _send_periodic_template(uid, wa_targets, cfg, summary, kind, instance) -> None:
    named_params = _periodic_template_named_body_params(summary)
    quick_reply_buttons = _periodic_template_quick_reply_buttons(kind)
    label = "semanal" if kind == "weekly" else "mensal"

    for to in wa_targets:
        try:
            send_template(
                to,
                cfg["name"],
                language_code=cfg["language_code"],
                named_body_params=named_params,
                quick_reply_buttons=quick_reply_buttons,
            )
            logger.info(
                "WA %s report template sent uid=%s to=%s pid=%s hostname=%s",
                kind, uid, to, instance["pid"], instance["hostname"],
            )
            log_system_event_sync(
                "info",
                f"whatsapp_{kind}_report_template_sent",
                f"Template proativo de relatorio {label} enviado para o WhatsApp.",
                source="wa_app",
                user_id=uid,
                details={
                    "to": to,
                    "template_name": cfg["name"],
                    "language_code": cfg["language_code"],
                    "periodo": named_params["periodo"],
                },
            )
        except Exception as exc:
            logger.warning("WA %s report send error to=%s error=%s", kind, to, exc)
            log_system_event_sync(
                "warning",
                f"whatsapp_{kind}_report_send_failed",
                f"Falha ao enviar relatorio {label} via WhatsApp: {exc}",
                source="wa_app",
                user_id=uid,
                details={"to": to},
            )


def _periodic_report_tick() -> None:
    now = now_tz()
    today = now.date()
    is_monday = today.weekday() == 0   # segunda → resumo semanal (semana anterior)
    is_first  = today.day == 1         # dia 1  → resumo mensal (mês anterior)

    if not (is_monday or is_first):
        return

    weekly_cfg  = _periodic_template_config("weekly") if is_monday else None
    monthly_cfg = _periodic_template_config("monthly") if is_first else None
    if not weekly_cfg and not monthly_cfg:
        # nenhum template configurado para hoje → nada a fazer (e não consome o claim)
        return

    # toggles independentes: cada resumo tem seu próprio liga/desliga
    weekly_users  = set(list_users_with_weekly_report_enabled())  if weekly_cfg else set()
    monthly_users = set(list_users_with_monthly_report_enabled()) if monthly_cfg else set()

    instance = _runtime_instance_details()

    for uid in (weekly_users | monthly_users):
        prefs = get_daily_report_prefs(uid)

        # entrega no mesmo horário configurado para o report diário do usuário
        if (now.hour, now.minute) < (prefs["hour"], prefs["minute"]):
            continue

        ids = list_identities_by_user(uid)
        wa_targets = _dedupe_whatsapp_targets(ids)
        if not wa_targets:
            continue

        # claim atômico por período: o loop faz polling a cada 30s, o claim garante
        # que cada resumo saia uma única vez (mesmo com reinício / múltiplas instâncias)
        if uid in weekly_users and claim_weekly_report_send(uid, today):
            summary = build_weekly_report_summary(uid, closed=True)
            _send_periodic_template(uid, wa_targets, weekly_cfg, summary, "weekly", instance)

        if uid in monthly_users and claim_monthly_report_send(uid, today):
            summary = build_monthly_report_summary(uid, closed=True)
            _send_periodic_template(uid, wa_targets, monthly_cfg, summary, "monthly", instance)


async def _periodic_report_loop():
    await asyncio.sleep(10)

    while True:
        try:
            await asyncio.to_thread(_periodic_report_tick)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.exception("WA periodic report loop error: %s", exc)
            log_system_event_sync(
                "error",
                "whatsapp_periodic_report_loop_error",
                f"Erro no loop de relatorios periodicos do WhatsApp: {exc}",
                source="wa_app",
            )

        await asyncio.sleep(30)
