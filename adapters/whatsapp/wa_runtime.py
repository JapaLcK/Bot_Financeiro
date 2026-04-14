from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import threading
import time
import traceback
from dataclasses import dataclass
from typing import Any

from adapters.whatsapp.wa_client import download_media, send_text
from adapters.whatsapp.wa_parse import InboundAttachmentRef, InboundMessage, extract_messages, get_interactive_id
from adapters.whatsapp.wa_tutorial import (
    TUTORIAL_BUTTON_IDS,
    get_tutorial_button_id,
    handle_tutorial_button,
    send_welcome,
)
from adapters.whatsapp.wa_help_menu import (
    HELP_MENU_IDS,
    get_help_menu_id,
    send_help_menu,
    send_help_section,
)
from core.handle_incoming import handle_incoming
from core.types import IncomingMessage
from db import attempt_whatsapp_phone_link, get_or_create_canonical_user
from utils_phone import mask_phone

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
            try:
                message_ids = [m.get("id") for m in (result or {}).get("messages", []) if m.get("id")]
                contacts = [c.get("wa_id") for c in (result or {}).get("contacts", []) if c.get("wa_id")]
                logger.info(
                    "WA send_text accepted: to=%s canonical_contacts=%s message_ids=%s",
                    to_wa_id,
                    contacts,
                    message_ids,
                )
            except Exception:
                logger.info("WA send_text accepted but unable to summarize response")
        except Exception as e:
            print(f"[DEBUG] send_text EXCEPTION: {e}", flush=True)
            logger.exception("WA send_text exception to=%s error=%s", to_wa_id, e)
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


def _is_greeting(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", (text or "").strip().lower())
    return normalized in {"oi", "ola", "olá", "hello", "hi", "hey", "bom dia", "boa tarde", "boa noite"}


def process_message(message: InboundMessage) -> None:
    try:
        reply_to = message.wa_id
        print(f"[DEBUG] process_message from={message.wa_id} reply_to={reply_to} text={repr((message.text or '')[:80])}", flush=True)
        logger.info(
            "WA process_message from=%s reply_to=%s text=%r attachments=%s",
            message.wa_id,
            reply_to,
            (message.text or "")[:120],
            len(message.attachments or []),
        )
        uid = get_or_create_canonical_user("whatsapp", message.wa_id)
        print(f"[DEBUG] uid={uid}", flush=True)
        logger.info("WA canonical user resolved uid=%s from=%s", uid, message.wa_id)

        auto_link_result = attempt_whatsapp_phone_link(message.wa_id, current_user_id=uid)
        if auto_link_result["status"] in {"linked", "already_linked"}:
            resolved_uid = int(auto_link_result.get("user_id") or uid)
            if resolved_uid != uid:
                logger.info(
                    "WA canonical user updated after auto-link old_uid=%s new_uid=%s from=%s",
                    uid,
                    resolved_uid,
                    message.wa_id,
                )
                print(f"[DEBUG] uid remapped old={uid} new={resolved_uid}", flush=True)
                uid = resolved_uid
            if auto_link_result["status"] == "linked":
                logger.info(
                    "WA phone auto-link success wa_id=%s final_user_id=%s",
                    message.wa_id,
                    auto_link_result["user_id"],
                )
                # Envia mensagem de boas-vindas interativa com botão de tutorial
                try:
                    send_welcome(reply_to)
                except Exception as e:
                    logger.warning("WA send_welcome failed, falling back to text: %s", e)
                    _send_reply(
                        reply_to,
                        (
                            "✅ WhatsApp conectado à sua conta!\n\n"
                            "Já pode usar:\n"
                            "• gastei 50 mercado\n"
                            "• recebi 1000 salario\n"
                            "• saldo\n"
                            "• ajuda"
                        ),
                    )
                return
        elif auto_link_result["status"] == "no_match" and _is_greeting(message.text or ""):
            _send_reply(
                reply_to,
                (
                    "⚠️ Não encontrei nenhuma conta cadastrada com este número de WhatsApp.\n"
                    "Crie sua conta no site usando este mesmo número ou use o fluxo de código/link para vincular."
                ),
            )
            return
        elif auto_link_result["status"] == "multiple_accounts" and _is_greeting(message.text or ""):
            _send_reply(
                reply_to,
                "⚠️ Encontrei mais de uma conta com este número. Não consegui vincular automaticamente.",
            )
            return
        elif auto_link_result["status"] == "wa_linked_other_account" and _is_greeting(message.text or ""):
            _send_reply(
                reply_to,
                "⚠️ Este WhatsApp já está vinculado a outra conta. Revise seu cadastro ou use outro número.",
            )
            return
        elif auto_link_result["status"] == "account_has_other_whatsapp" and _is_greeting(message.text or ""):
            _send_reply(
                reply_to,
                (
                    "⚠️ Sua conta já tem outro WhatsApp vinculado. "
                    f"Este número ({mask_phone(auto_link_result['wa_phone'])}) não foi conectado automaticamente."
                ),
            )
            return

        # ---------------------------------------------------------------
        # Interceptação de mensagens interativas (botões / listas)
        # Deve ocorrer ANTES da deduplicação para evitar ignorar cliques.
        # ---------------------------------------------------------------
        raw_msg = message.raw or {}
        interactive_id = get_interactive_id(raw_msg)

        if interactive_id:
            # Botões do tutorial
            tut_bid = get_tutorial_button_id(raw_msg)
            if tut_bid:
                logger.info("WA tutorial button id=%s wa_id=%s", tut_bid, reply_to)
                try:
                    handle_tutorial_button(reply_to, tut_bid)
                except Exception as e:
                    logger.exception("WA tutorial button error id=%s: %s", tut_bid, e)
                return

            # Itens do menu de ajuda
            help_id = get_help_menu_id(raw_msg)
            if help_id:
                logger.info("WA help menu id=%s wa_id=%s", help_id, reply_to)
                try:
                    send_help_section(reply_to, help_id)
                except Exception as e:
                    logger.exception("WA help menu error id=%s: %s", help_id, e)
                return

        # ---------------------------------------------------------------
        # Interceptação de comandos de texto simples para fluxo interativo
        # ---------------------------------------------------------------
        text_cmd = (message.text or "").strip().lower()

        if text_cmd in {"ajuda", "help", "menu", "/ajuda", "/help"}:
            logger.info("WA help menu via texto wa_id=%s", reply_to)
            try:
                send_help_menu(reply_to)
            except Exception as e:
                logger.warning("WA send_help_menu failed, usando texto: %s", e)
                # fallback para o fluxo normal de texto
                pass
            else:
                return

        elif text_cmd in {"tutorial", "/tutorial"}:
            logger.info("WA tutorial welcome via texto wa_id=%s", reply_to)
            try:
                send_welcome(reply_to)
            except Exception as e:
                logger.warning("WA send_welcome failed, usando texto: %s", e)
                pass
            else:
                return

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
            _send_reply(reply_to, "Recebi seu arquivo. Processando agora...")

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
            _send_reply(reply_to, "Nao entendi. Digite ajuda para ver os comandos.")
            return

        logger.info("WA generated outgoing messages count=%s for from=%s", len(outs), message.wa_id)
        for out in outs:
            body = safe_text(out)
            if body:
                _send_reply(reply_to, body)
    except Exception as exc:
        logger.error("WA message processing failed wa_id=%s error=%s", message.wa_id, exc)
        traceback.print_exc()


def process_payload(payload: dict[str, Any]) -> int:
    msgs = extract_messages(payload)
    print(f"[DEBUG] extracted_messages={len(msgs)}", flush=True)
    logger.info("WA extracted messages=%s", len(msgs))
    for message in msgs:
        process_message(message)
    return len(msgs)
