from __future__ import annotations

import hashlib
import hmac
import logging
import re
import threading
import time
import traceback
from dataclasses import dataclass
from typing import Any

from adapters.whatsapp.wa_client import download_media, send_text
from adapters.whatsapp.wa_parse import InboundAttachmentRef, InboundMessage, extract_messages
from core.handle_incoming import handle_incoming
from core.types import IncomingMessage
from db import get_or_create_canonical_user

logger = logging.getLogger(__name__)

_SEEN: dict[str, float] = {}
_SEEN_LOCK = threading.Lock()
_SEEN_TTL = 180


@dataclass
class Attachment:
    filename: str
    content_type: str
    data: bytes


def verify_webhook_signature(raw_body: bytes, signature_header: str, app_secret: str) -> bool:
    if not app_secret:
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected_hash = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    expected = f"sha256={expected_hash}"
    return hmac.compare_digest(signature_header, expected)


def _seen_recent(msg_id: str) -> bool:
    now = time.time()
    with _SEEN_LOCK:
        for key, seen_at in list(_SEEN.items()):
            if now - seen_at > _SEEN_TTL:
                _SEEN.pop(key, None)
        if msg_id in _SEEN:
            return True
        _SEEN[msg_id] = now
        return False


def safe_text(obj: Any) -> str:
    if obj is None:
        return ""

    if isinstance(obj, str):
        m = re.match(r"^OutgoingMessage\(text=(?P<q>['\"])(?P<body>.*)(?P=q)\)\s*$", obj, flags=re.S)
        if m:
            body = m.group("body")
            body = body.replace("\\n", "\n").replace("\\t", "\t").replace("\\r", "\r")
            return body.strip()
        return obj.strip()

    if isinstance(obj, dict):
        return str(obj.get("text") or obj.get("body") or "").strip()

    if hasattr(obj, "text"):
        return str(getattr(obj, "text") or "").strip()

    return str(obj).strip()


def _send_reply(to_wa_id: str, body: str) -> None:
    body = (body or "").strip()
    if body:
        print(f"[DEBUG] _send_reply to={to_wa_id} chars={len(body)}", flush=True)
        logger.info("WA sending reply to=%s chars=%s", to_wa_id, len(body))
        try:
            result = send_text(to=to_wa_id, body=body)
            print(f"[DEBUG] send_text result={result}", flush=True)
        except Exception as e:
            print(f"[DEBUG] send_text EXCEPTION: {e}", flush=True)
            raise


def _download_attachments_sync(att_refs: list[InboundAttachmentRef]) -> list[Attachment]:
    out: list[Attachment] = []
    for att in att_refs:
        try:
            data = download_media(att.media_id)
            out.append(
                Attachment(
                    filename=att.filename or f"file_{att.media_id}",
                    content_type=att.content_type or "application/octet-stream",
                    data=data,
                )
            )
        except Exception as exc:
            logger.warning("WA attachment download failed media_id=%s error=%s", att.media_id, exc)
    return out


def process_message(message: InboundMessage) -> None:
    try:
        print(f"[DEBUG] process_message from={message.wa_id} text={repr((message.text or '')[:80])}", flush=True)
        logger.info(
            "WA process_message from=%s text=%r attachments=%s",
            message.wa_id,
            (message.text or "")[:120],
            len(message.attachments or []),
        )
        uid = get_or_create_canonical_user("whatsapp", message.wa_id)
        print(f"[DEBUG] uid={uid}", flush=True)

        try:
            msg_id = str(message.raw.get("id") or message.timestamp or "")
        except Exception:
            msg_id = str(message.timestamp or "")

        if not msg_id:
            msg_id = hashlib.sha256(repr(message.raw).encode("utf-8")).hexdigest()

        if _seen_recent(msg_id):
            logger.info("WA duplicate ignored message_id=%s", msg_id)
            return

        att_refs = message.attachments or []
        if att_refs:
            _send_reply(message.wa_id, "Recebi seu arquivo. Processando agora...")

        attachments: list[Any] = []
        if att_refs:
            attachments = _download_attachments_sync(att_refs)
            if not attachments:
                attachments = att_refs

        incoming = IncomingMessage(
            platform="whatsapp",
            user_id=uid,
            external_id=message.wa_id,
            text=message.text or "",
            message_id=msg_id,
            attachments=attachments,
        )

        outs = handle_incoming(incoming) or []
        if not outs:
            logger.info("WA no outgoing messages for from=%s", message.wa_id)
            _send_reply(message.wa_id, "Nao entendi. Digite ajuda para ver os comandos.")
            return

        logger.info("WA generated outgoing messages count=%s for from=%s", len(outs), message.wa_id)
        for out in outs:
            body = safe_text(out)
            if body:
                _send_reply(message.wa_id, body)
    except Exception as exc:
        logger.error("WA message processing failed wa_id=%s error=%s", message.wa_id, exc)
        traceback.print_exc()


def process_payload(payload: dict[str, Any]) -> int:
    msgs = extract_messages(payload)
    logger.info("WA extracted messages=%s", len(msgs))
    for message in msgs:
        process_message(message)
    return len(msgs)
