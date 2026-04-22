# core/types.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional, Any

Platform = Literal["discord", "whatsapp"]

@dataclass
class Attachment:
    filename: str
    content_type: str = "application/octet-stream"
    data: bytes = b""
    url: Optional[str] = None  # útil no WhatsApp/Discord quando você quiser lazy-download

@dataclass
class IncomingMessage:
    platform: Platform
    user_id: int
    text: str
    message_id: Optional[str] = None
    attachments: list[Attachment] = field(default_factory=list)
    external_id: str | None = None

    # opcional: guardar payload bruto p/ debug
    raw: Any = None

@dataclass
class OutgoingMessage:
    text: str