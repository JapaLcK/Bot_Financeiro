# adapters/whatsapp/wa_app.py
import os
import json
import hmac
import hashlib
import asyncio
from typing import Any, Dict
import time as pytime
from utils_date import now_tz
from db import (
    list_users_with_daily_report_enabled,
    list_identities_by_user,
    was_daily_report_sent_today,
    mark_daily_report_sent,
)
from core.reports.reports_daily import build_daily_report_text

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, JSONResponse
from db import get_or_create_canonical_user  # no topo do arquivo
from core.types import IncomingMessage
from core.handle_incoming import handle_incoming
from adapters.whatsapp.wa_parse import extract_messages
from adapters.whatsapp.wa_client import send_text, wa  # WhatsAppClient singleton

app = FastAPI()

VERIFY_TOKEN = os.getenv("WA_VERIFY_TOKEN", "dev-token")
APP_SECRET = os.getenv("WA_APP_SECRET", "").strip()  # opcional, mas recomendado

_queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue(maxsize=500)

def _verify_signature(raw_body: bytes, signature_header: str) -> bool:
    """
    Valida X-Hub-Signature-256: "sha256=<hmac>"
    """
    if not APP_SECRET:
        return True  # se não configurou, não bloqueia (mas em prod configure!)
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    provided = signature_header
    expected_hash = hmac.new(APP_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    expected = f"sha256={expected_hash}"
    return hmac.compare_digest(provided, expected)

@app.on_event("startup")
async def _startup():
    # worker interno da fila
    asyncio.create_task(_worker_loop())

    # scheduler do report diário (WhatsApp)
    asyncio.create_task(_daily_report_loop())

@app.get("/wa/webhook")
async def wa_verify(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN and challenge:
        return PlainTextResponse(challenge)
    return PlainTextResponse("forbidden", status_code=403)

@app.post("/wa/webhook")
async def wa_webhook(request: Request):
    raw = await request.body()

    sig = request.headers.get("X-Hub-Signature-256", "")
    if not _verify_signature(raw, sig):
        return PlainTextResponse("forbidden", status_code=403)

    payload = json.loads(raw.decode("utf-8"))

    # responde rápido, processa depois
    try:
        _queue.put_nowait(payload)
    except asyncio.QueueFull:
        # se lotar, não trava o webhook
        return JSONResponse({"ok": True, "dropped": True})

    return JSONResponse({"ok": True})

async def _worker_loop():
    while True:
        payload = await _queue.get()
        try:
            await _process_payload(payload)
        except Exception as e:
            print("[WA] worker error:", repr(e))
        finally:
            _queue.task_done()

async def _daily_report_loop():
    """
    Loop interno que manda o report diário no WhatsApp sem precisar de cron/service extra.
    Roda pra sempre, checa o horário e envia 1x por dia por usuário.
    """
    while True:
        try:
            now = now_tz()
            hour = int(os.getenv("DAILY_REPORT_HOUR", "9"))
            minute = int(os.getenv("DAILY_REPORT_MINUTE", "0"))

            if now.hour == hour and now.minute == minute:
                today = now.date()

                user_ids = list_users_with_daily_report_enabled(hour, minute)

                for uid in user_ids:
                    # evita duplicar no mesmo dia
                    if was_daily_report_sent_today(uid, today):
                        continue

                    msg = build_daily_report_text(uid)

                    ids = list_identities_by_user(uid)
                    wa_targets = [x["external_id"] for x in ids if x["provider"] == "whatsapp"]

                    for to in wa_targets:
                        try:
                            # ✅ melhor usar o client async quando possível
                            await wa.send_text(to, msg)
                        except Exception as e:
                            print("[WA] daily report send error:", repr(e))

                    # marca como enviado (mesmo se não tinha target, pra não ficar tentando)
                    try:
                        mark_daily_report_sent(uid, today)
                    except Exception as e:
                        print("[WA] mark_daily_report_sent error:", repr(e))

        except Exception as e:
            print("[WA] daily report loop error:", repr(e))

        # acorda 2x por minuto pra não “perder” o minuto 09:00
        await asyncio.sleep(30)

async def _process_payload(payload: dict):
    msgs = extract_messages(payload)

    for m in msgs:
        from_phone = m["from"]
        message_id = m.get("message_id")

        # id interno (seu esquema atual)
        external_id = from_phone  # wa_id (string)
        uid = get_or_create_canonical_user("whatsapp", external_id)

        if m["type"] == "text":
            text = (m.get("text") or "").strip()
            incoming = IncomingMessage(
                platform="whatsapp",
                user_id=uid,            # ✅ user canônico
                external_id=external_id,  # ✅ necessário para link
                text=text,
                message_id=message_id,
            )
            outs = handle_incoming(incoming)
            if not outs:
                # fallback WhatsApp (porque aqui não existe "código legado" como no Discord)
                send_text(from_phone, "❓ Não entendi. Digite `ajuda` para ver os comandos.")
            else:
                for out in outs:
                    send_text(from_phone, out.text)

            if message_id:
                await wa.mark_read(message_id)

        elif m["type"] == "document":
            # pronto pra plugar: baixa o arquivo e chama seu importador OFX
            media_id = m.get("media_id")
            filename = m.get("filename") or "arquivo"

            # aqui você decide o comando: pode exigir que o usuário mande "importar ofx"
            # ou importar automaticamente se for .ofx
            if media_id:
                url = await wa.get_media_url(media_id)
                file_bytes = await wa.download_media_bytes(url)

                # Exemplo: reaproveitar seu core/ofx_service.py ou função existente
                # result_text = import_ofx_flow(user_id, file_bytes, filename)
                # await wa.send_text(from_phone, result_text)

                await wa.send_text(from_phone, f"Recebi o arquivo `{filename}`. (download OK)")

            if message_id:
                await wa.mark_read(message_id)


# Endpoint de DEV pra testar SEM META (você já tem algo assim)
@app.post("/wa/dev/simulate")
async def wa_simulate(payload: dict):
    from_phone = payload.get("from", "+15551234567")
    text = (payload.get("text") or "").strip()

    digits = "".join(ch for ch in from_phone if ch.isdigit())
    user_id = -int(digits) if digits else -555

    incoming = IncomingMessage(platform="whatsapp", user_id=user_id, text=text, message_id=None)
    outs = handle_incoming(incoming)
    if not outs:
        texts = ["❓ Não entendi. Digite `ajuda` para ver os comandos."]
    else:
        texts = [o.text for o in outs]
    for t in texts:
        await wa.send_text(from_phone, t)

    return {"ok": True, "responses": texts}