from __future__ import annotations

import logging
import os
import threading

from flask import Flask, request

from adapters.whatsapp.wa_runtime import process_message, verify_webhook_signature
from adapters.whatsapp.wa_parse import extract_messages
from config.env import load_app_env

load_app_env()

logger = logging.getLogger(__name__)
app = Flask(__name__)

VERIFY_TOKEN = (os.getenv("WA_VERIFY_TOKEN") or "").strip()
APP_SECRET = (os.getenv("WA_APP_SECRET") or "").strip()


@app.get("/webhook")
def verify():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if not VERIFY_TOKEN:
        logger.error("WA_VERIFY_TOKEN is not configured")
        return "forbidden", 403
    if mode == "subscribe" and token == VERIFY_TOKEN and challenge:
        return challenge, 200
    return "forbidden", 403


@app.post("/webhook")
def inbound():
    raw = request.get_data(cache=False)
    signature = request.headers.get("X-Hub-Signature-256", "")
    if APP_SECRET and not verify_webhook_signature(raw, signature, APP_SECRET):
        return "forbidden", 403

    data = request.get_json(silent=True) or {}
    try:
        msgs = extract_messages(data)
        for message in msgs:
            t = threading.Thread(target=process_message, args=(message,), daemon=True)
            t.start()
    except Exception as exc:
        logger.exception("WA webhook error: %s", exc)

    return "ok", 200


if __name__ == "__main__":
    port = int(os.getenv("PORT") or "5001")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
