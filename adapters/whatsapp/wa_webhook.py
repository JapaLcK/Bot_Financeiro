# adapters/whatsapp/wa_webhook.py
from __future__ import annotations

import os
import sys
import time
import re
import traceback
import threading
import hashlib
from dataclasses import dataclass
from typing import Any

from flask import Flask, request
from dotenv import load_dotenv

load_dotenv()

# Garante que o root do projeto está no sys.path
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from adapters.whatsapp.wa_parse import extract_messages, InboundMessage, InboundAttachmentRef
from adapters.whatsapp.wa_client import send_text, download_media
from core.types import IncomingMessage
from core.handle_incoming import handle_incoming
from db import get_or_create_canonical_user

app = Flask(__name__)

VERIFY_TOKEN = (os.getenv("WA_VERIFY_TOKEN") or "meu_verify_token_123").strip()

# ---------------------------
# Deduplicação (evita responder a mesma msg várias vezes)
# ---------------------------
_SEEN: dict[str, float] = {}
_SEEN_LOCK = threading.Lock()
_SEEN_TTL = 180  # segundos


def _seen_recent(msg_id: str) -> bool:
    """
    Retorna True se já vimos msg_id recentemente.
    Também limpa entradas antigas.
    """
    now = time.time()
    with _SEEN_LOCK:
        # limpa antigos
        for k, ts in list(_SEEN.items()):
            if now - ts > _SEEN_TTL:
                _SEEN.pop(k, None)

        if msg_id in _SEEN:
            return True

        _SEEN[msg_id] = now
        return False


@dataclass
class Attachment:
    """
    Anexo baixado (bytes).
    """
    filename: str
    content_type: str
    data: bytes


def _safe_text(obj: Any) -> str:
    """
    Converte o retorno do core em string “limpa” para enviar no WhatsApp.
    Resolve o caso chato onde chega 'OutgoingMessage(text=...)' como string.
    """
    if obj is None:
        return ""

    # Se já for string, pode ser string normal OU string do repr do OutgoingMessage
    if isinstance(obj, str):
        s = obj

        # Caso venha: OutgoingMessage(text='...\n...')
        # A gente extrai o conteúdo do text='...'
        m = re.match(r"^OutgoingMessage\(text=(?P<q>['\"])(?P<body>.*)(?P=q)\)\s*$", s, flags=re.S)
        if m:
            body = m.group("body")
            # transforma \n literal em newline real
            body = body.replace("\\n", "\n")
            body = body.replace("\\t", "\t")
            body = body.replace("\\r", "\r")
            return body.strip()

        return s.strip()

    # Se for dict vindo de algum adapter
    if isinstance(obj, dict):
        s = str(obj.get("text") or obj.get("body") or "")
        return s.strip()

    # Se for um objeto tipo OutgoingMessage com atributo .text
    if hasattr(obj, "text"):
        s = str(getattr(obj, "text") or "")
        return s.strip()

    # Fallback
    return str(obj).strip()


def _send_reply(to_wa_id: str, body: str) -> None:
    """
    Envia texto para o WhatsApp.
    """
    body = (body or "").strip()
    if not body:
        return
    send_text(to=to_wa_id, body=body)


def _download_attachments_sync(att_refs: list[InboundAttachmentRef]) -> list[Attachment]:
    """
    Baixa anexos (sync). Usamos isso dentro de uma thread para não travar o webhook.
    """
    out: list[Attachment] = []
    for a in att_refs:
        try:
            data = download_media(a.media_id)
            filename = a.filename or f"file_{a.media_id}"
            content_type = a.content_type or "application/octet-stream"
            out.append(Attachment(filename=filename, content_type=content_type, data=data))
        except Exception as e:
            print("ATTACH_DOWNLOAD_ONE_ERROR:", repr(e))
            continue
    return out


def _looks_like_ofx(filename: str, content_type: str) -> bool:
    fn = (filename or "").lower().strip()
    ct = (content_type or "").lower().strip()
    if fn.endswith(".ofx"):
        return True
    # whatsapp costuma vir como octet-stream mesmo
    if ct in ("application/octet-stream", "text/plain", "application/xml"):
        if fn.endswith(".ofx"):
            return True
    return False


def _process_one_message(m: InboundMessage) -> None:
    """
    Processa UMA mensagem do WhatsApp em background (thread),
    para o webhook responder rápido e não travar o servidor.
    """
    try:
        uid = get_or_create_canonical_user("whatsapp", m.wa_id)

        # Pega um id estável da mensagem (wamid)
        msg_id = ""
        try:
            msg_id = str(m.raw.get("id") or m.timestamp or "")
        except Exception:
            msg_id = str(m.timestamp or "")

        # Se vier vazio, cria um fallback (não ideal, mas evita flood)
        if not msg_id:
            msg_id = hashlib.sha256(repr(m.raw).encode("utf-8")).hexdigest()

        # Dedupe de mensagem
        if _seen_recent(msg_id):
            print("WA DEDUPE: ignorando msg repetida:", msg_id)
            return

        att_refs = m.attachments or []

        # Se tem anexo, responde imediatamente (pra ficar < 5s)
        if att_refs:
            _send_reply(m.wa_id, "📥 Recebi seu arquivo. Processando agora…")

        attachments: list[Any] = []
        if att_refs:
            attachments = _download_attachments_sync(att_refs)

            # Se falhar download, passa as refs mesmo (pra o core decidir o que fazer)
            if not attachments:
                attachments = att_refs

        incoming = IncomingMessage(
            platform="whatsapp",
            user_id=uid,
            external_id=m.wa_id,
            text=m.text or "",
            message_id=msg_id,
            attachments=attachments,
        )

        outs = handle_incoming(incoming) or []
        for out in outs:
            body = _safe_text(out)
            if body:
                _send_reply(m.wa_id, body)

    except Exception as e:
        print("PROCESS_ONE_ERROR:", repr(e))
        traceback.print_exc()


@app.get("/webhook")
def verify():
    """
    Verificação do webhook (Meta pede isso).
    """
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return challenge, 200
    return "forbidden", 403


@app.post("/webhook")
def inbound():
    """
    Recebe eventos do WhatsApp.
    IMPORTANTÍSSIMO: não pode demorar.
    Por isso a gente joga o processamento em thread e retorna 200 rápido.
    """
    data = request.get_json(silent=True) or {}

    try:
        msgs: list[InboundMessage] = extract_messages(data)
        for m in msgs:
            t = threading.Thread(target=_process_one_message, args=(m,), daemon=True)
            t.start()

    except Exception as e:
        print("WEBHOOK_ERROR:", repr(e))
        traceback.print_exc()

    return "ok", 200


if __name__ == "__main__":
    port = int(os.getenv("PORT") or "5001")
    # debug=False e use_reloader=False evitam duplicar chamadas/respostas
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)