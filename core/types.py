from dataclasses import dataclass
from typing import Optional

@dataclass
class Attachment:
    filename: str
    content_type: str
    data: bytes

@dataclass
class IncomingMessage:
    platform: str           # "discord" | "whatsapp"
    user_id: str            # sempre string
    text: str
    attachments: list[Attachment]