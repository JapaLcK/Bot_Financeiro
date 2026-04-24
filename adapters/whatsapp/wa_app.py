from __future__ import annotations

import asyncio
import json
import logging
import os
import socket

from fastapi import Request
from fastapi.responses import JSONResponse, PlainTextResponse

from adapters.whatsapp.wa_client import send_interactive_buttons, send_template, send_text
from adapters.whatsapp.wa_runtime import process_payload, verify_webhook_signature
from config.env import load_app_env
from core.observability import log_system_event_sync
from core.reports.reports_daily import build_daily_report_text, build_due_bill_reminders
from db import (
    claim_daily_report_send,
    get_daily_report_prefs,
    list_identities_by_user,
    list_users_with_daily_report_enabled,
    mark_card_reminder_sent,
)
from utils_phone import normalize_phone_e164
from utils_date import now_tz

load_app_env()

logger = logging.getLogger(__name__)

VERIFY_TOKEN = (os.getenv("WA_VERIFY_TOKEN") or "").strip()
APP_SECRET = (os.getenv("WA_APP_SECRET") or "").strip()
_queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=500)
WA_DAILY_REPORT_DISABLE_ID = "daily_report_disable"


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
            normalized = normalize_phone_e164(raw)
        except Exception:
            normalized = raw

        if normalized in seen:
            continue

        seen.add(normalized)
        targets.append(raw)

    return targets


def _proactive_template_config() -> dict[str, str] | None:
    template_name = (os.getenv("WA_PROACTIVE_TEMPLATE_NAME") or "").strip()
    if not template_name:
        return None

    return {
        "name": template_name,
        "language_code": (os.getenv("WA_PROACTIVE_TEMPLATE_LANGUAGE") or "pt_BR").strip(),
    }


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
    if APP_SECRET and not verify_webhook_signature(raw, signature, APP_SECRET):
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
    count = await asyncio.to_thread(process_payload, payload)
    return {"ok": True, "processed_messages": count}


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
        if not claim_daily_report_send(uid, today):
            continue

        message = _strip_daily_report_disable_hint(build_daily_report_text(uid))
        reminders = build_due_bill_reminders(uid, today)
        ids = list_identities_by_user(uid)
        wa_targets = _dedupe_whatsapp_targets(ids)

        for to in wa_targets:
            try:
                proactive_template = _proactive_template_config()
                if proactive_template:
                    send_template(
                        to,
                        proactive_template["name"],
                        language_code=proactive_template["language_code"],
                    )
                for reminder in reminders:
                    send_text(to, reminder["message"])
                send_text(to, message)
                send_interactive_buttons(
                    to=to,
                    body="Se quiser parar o resumo diário, toque no botão abaixo.",
                    buttons=[{"id": WA_DAILY_REPORT_DISABLE_ID, "title": "Parar resumo"}],
                    footer="Você pode ligar novamente quando quiser.",
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
