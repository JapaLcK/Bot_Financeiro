from __future__ import annotations

import asyncio
import json
import logging
import os

from fastapi import Request
from fastapi.responses import JSONResponse, PlainTextResponse

from adapters.whatsapp.wa_client import send_text
from adapters.whatsapp.wa_runtime import process_payload, verify_webhook_signature
from core.reports.reports_daily import build_daily_report_text
from db import (
    get_daily_report_prefs,
    list_identities_by_user,
    list_users_with_daily_report_enabled,
    mark_daily_report_sent,
    was_daily_report_sent_today,
)
from utils_date import now_tz

logger = logging.getLogger(__name__)

VERIFY_TOKEN = (os.getenv("WA_VERIFY_TOKEN") or "").strip()
APP_SECRET = (os.getenv("WA_APP_SECRET") or "").strip()
_queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=500)


async def wa_verify(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if not VERIFY_TOKEN:
        logger.error("WA_VERIFY_TOKEN is not configured")
        return PlainTextResponse("forbidden", status_code=403)

    if mode == "subscribe" and token == VERIFY_TOKEN and challenge:
        return PlainTextResponse(challenge)
    return PlainTextResponse("forbidden", status_code=403)


async def wa_webhook(request: Request):
    raw = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")
    if APP_SECRET and not verify_webhook_signature(raw, signature, APP_SECRET):
        return PlainTextResponse("forbidden", status_code=403)

    payload = json.loads(raw.decode("utf-8"))
    try:
        _queue.put_nowait(payload)
    except asyncio.QueueFull:
        logger.warning("WA queue full, dropping payload")
        return JSONResponse({"ok": True, "dropped": True})

    return JSONResponse({"ok": True})


async def wa_simulate(payload: dict):
    count = await asyncio.to_thread(process_payload, payload)
    return {"ok": True, "processed_messages": count}


async def _worker_loop():
    while True:
        payload = await _queue.get()
        try:
            count = await asyncio.to_thread(process_payload, payload)
            logger.info("WA payload processed messages=%s", count)
        except Exception as exc:
            logger.exception("WA worker error: %s", exc)
        finally:
            _queue.task_done()


async def _daily_report_loop():
    while True:
        try:
            now = now_tz()
            today = now.date()

            for uid in list_users_with_daily_report_enabled():
                prefs = get_daily_report_prefs(uid)
                if not prefs["enabled"]:
                    continue

                hour = prefs["hour"]
                minute = prefs["minute"]
                if (now.hour, now.minute) < (hour, minute):
                    continue
                if was_daily_report_sent_today(uid, today):
                    continue

                message = build_daily_report_text(uid)
                ids = list_identities_by_user(uid)
                wa_targets = [x["external_id"] for x in ids if x["provider"] == "whatsapp"]

                for to in wa_targets:
                    try:
                        await asyncio.to_thread(send_text, to, message)
                    except Exception as exc:
                        logger.warning("WA daily report send error to=%s error=%s", to, exc)

                mark_daily_report_sent(uid, today)
        except Exception as exc:
            logger.exception("WA daily report loop error: %s", exc)

        await asyncio.sleep(30)
