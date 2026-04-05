# adapters/whatsapp/wa_parse.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class InboundAttachmentRef:
    media_id: str
    filename: str | None = None
    content_type: str | None = None


@dataclass
class InboundMessage:
    wa_id: str
    text: str
    timestamp: str | None
    attachments: list[InboundAttachmentRef]
    raw: dict[str, Any]


def _get_value(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return payload["entry"][0]["changes"][0]["value"]
    except Exception:
        return {}


def extract_messages(payload: dict[str, Any]) -> list[InboundMessage]:
    """
    Extract inbound user messages from WhatsApp Cloud API webhook payload.

    Supports:
      - text
      - document/image/audio/video (attachments via media id)
      - interactive button replies (best-effort)
    """
    value = _get_value(payload)
    msgs = value.get("messages") or []

    # wa_id canônico por número (do campo contacts do payload)
    contacts = value.get("contacts") or []
    canonical_wa_id: dict[str, str] = {}
    for c in contacts:
        phone = c.get("wa_id") or ""
        if phone:
            canonical_wa_id[phone] = phone
    print(f"[DEBUG] contacts wa_ids={list(canonical_wa_id.keys())}", flush=True)

    out: list[InboundMessage] = []

    for m in msgs:
        wa_id = m.get("from") or ""
        if not wa_id:
            continue
        # usa wa_id canônico do contacts se disponível
        wa_id = canonical_wa_id.get(wa_id, wa_id)
        print(f"[DEBUG] message from={m.get('from')} resolved_wa_id={wa_id}", flush=True)

        mtype = m.get("type") or "unknown"
        ts = m.get("timestamp")
        text = ""
        atts: list[InboundAttachmentRef] = []

        if mtype == "text":
            text = (m.get("text") or {}).get("body") or ""

        elif mtype in ("document", "image", "audio", "video", "sticker"):
            node = m.get(mtype) or {}
            media_id = node.get("id") or ""
            content_type = node.get("mime_type") or node.get("mimeType")
            filename = node.get("filename")

            caption = node.get("caption") or m.get("caption") or ""
            text = caption or ""

            if media_id:
                atts.append(
                    InboundAttachmentRef(
                        media_id=media_id,
                        filename=filename or f"{mtype}_{media_id}",
                        content_type=content_type or "",
                    )
                )

        elif mtype == "interactive":
            inter = m.get("interactive") or {}
            # button reply
            br = inter.get("button_reply") or {}
            # list reply
            lr = inter.get("list_reply") or {}

            text = (
                br.get("title")
                or br.get("id")
                or lr.get("title")
                or lr.get("id")
                or ""
            )

        else:
            # fallback: try anything
            text = str(m.get("text") or "")

        out.append(
            InboundMessage(
                wa_id=wa_id,
                text=(text or "").strip(),
                timestamp=ts,
                attachments=atts,
                raw=m,
            )
        )

    return out
