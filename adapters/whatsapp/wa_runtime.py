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

from adapters.whatsapp.wa_client import (
    download_media,
    send_interactive_buttons,
    send_interactive_list,
    send_text,
    send_typing_indicator,
)
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
from core.handlers import report as h_report
from core.observability import log_system_event_sync
from core.types import IncomingMessage
from db import (
    attempt_whatsapp_phone_link,
    clear_pending_action,
    get_conn,
    get_or_create_canonical_user,
    get_pending_action,
    set_pending_action,
    set_whatsapp_updates_opt_out,
    update_launch_category,
)
from utils_phone import mask_phone

logger = logging.getLogger(__name__)

WA_CONFIRM_YES_ID = "confirm_yes"
WA_CONFIRM_NO_ID = "confirm_no"
WA_UNDO_LAUNCH_ID = "undo_launch"
WA_DAILY_REPORT_DISABLE_ID = "daily_report_disable"
WA_RECAT_BUTTON_PREFIX = "recat:"          # botão pós-lançamento
WA_RECAT_PICK_PREFIX = "recatpick:"        # item da lista de categorias
WA_RECAT_OTHER_PREFIX = "recatother:"      # opção "outra (digitar)"
WA_UPDATES_DISABLE_IDS = {
    "parar atualizações",
    "parar atualizacoes",
    "whatsapp_updates_disable",
    "updates_disable",
}


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
        logger.info("WA sending reply to=%s chars=%s", to_wa_id, len(body))
        try:
            result = send_text(to=to_wa_id, body=body)
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
            logger.exception("WA send_text exception to=%s error=%s", to_wa_id, e)
            raise


def _pending_supports_confirmation_buttons(pending: dict[str, Any] | None) -> bool:
    if not pending:
        return False

    action_type = pending.get("action_type")
    payload = pending.get("payload") or {}
    step = payload.get("step")

    if action_type in {"delete_launch", "delete_launch_bulk", "delete_pocket", "delete_investment", "credit_delete_card", "confirm_media_launch"}:
        return True

    if action_type == "credit_card_set_primary":
        return step != "choose"

    if action_type == "credit_card_setup":
        return step in {"reminder_opt_in", "set_primary", "confirm_delete_existing_card"}

    return False


def _send_reply_with_optional_buttons(to_wa_id: str, body: str, user_id: int | None = None) -> None:
    body = (body or "").strip()
    if not body:
        return

    pending = get_pending_action(int(user_id)) if user_id is not None else None

    # Botão de desfazer (one-shot após áudio processado)
    if pending and pending.get("action_type") == "undo_audio":
        # Limpa imediatamente — só aparece uma vez
        if user_id is not None:
            try:
                clear_pending_action(int(user_id))
            except Exception as exc:
                logger.warning("WA clear undo_audio pending failed: %s", exc)
        logger.info("WA sending undo button to=%s", to_wa_id)
        try:
            send_interactive_buttons(
                to=to_wa_id,
                body=body,
                buttons=[{"id": WA_UNDO_LAUNCH_ID, "title": "↩️ Desfazer"}],
                footer="Toque para desfazer o último lançamento",
            )
            return
        except Exception as exc:
            logger.warning("WA send_interactive_buttons (undo) failed, fallback: %s", exc)

    # Lista de categorias direto na confirmação de lançamento (one-shot)
    elif pending and pending.get("action_type") == "recategorize_launch_offer":
        launch_id = (pending.get("payload") or {}).get("launch_id")
        if user_id is not None:
            try:
                clear_pending_action(int(user_id))
            except Exception as exc:
                logger.warning("WA clear recategorize_offer pending failed: %s", exc)
        if launch_id:
            logger.info("WA sending recategorize list to=%s launch_id=%s", to_wa_id, launch_id)
            try:
                _send_recategorize_list(to_wa_id, body, int(launch_id))
                return
            except Exception as exc:
                logger.warning("WA send recategorize list failed, fallback texto: %s", exc)

    elif _pending_supports_confirmation_buttons(pending):
        logger.info("WA sending interactive confirmation buttons to=%s", to_wa_id)
        try:
            send_interactive_buttons(
                to=to_wa_id,
                body=body,
                buttons=[
                    {"id": WA_CONFIRM_YES_ID, "title": "Sim"},
                    {"id": WA_CONFIRM_NO_ID, "title": "Não"},
                ],
                footer="Toque para responder",
            )
            return
        except Exception as exc:
            logger.warning("WA send_interactive_buttons failed, fallback para texto: %s", exc)

    _send_reply(to_wa_id, body)


def _send_recategorize_list(to_wa_id: str, body: str, launch_id: int) -> None:
    """Envia a confirmação como lista interativa com as categorias disponíveis.

    WhatsApp limita a 10 rows totais por lista, então mostramos as mais
    comuns + "Outra (digitar)" como fallback para texto livre.
    """
    pick = lambda c: {"id": f"{WA_RECAT_PICK_PREFIX}{launch_id}:{c}", "title": c}
    sections = [
        {
            "title": "Mais comuns",
            "rows": [
                pick("alimentação"),
                pick("transporte"),
                pick("lazer"),
                pick("moradia"),
                pick("saúde"),
                pick("educação"),
                pick("compras online"),
                pick("assinaturas"),
            ],
        },
        {
            "title": "Outras",
            "rows": [
                pick("outros"),
                {"id": f"{WA_RECAT_OTHER_PREFIX}{launch_id}", "title": "✏️ Outra (digitar)"},
            ],
        },
    ]
    send_interactive_list(
        to=to_wa_id,
        body=body,
        button_label="📂 Trocar categoria",
        sections=sections,
        footer="Toque para escolher uma categoria",
    )


def _apply_recategorize(user_id: int, launch_id: int, raw_categoria: str) -> str:
    """Aplica uma nova categoria a um launch e devolve a mensagem de resposta."""
    from utils_text import canonicalize_category_label  # local import (evita ciclo)

    cat = (raw_categoria or "").strip()
    if not cat:
        return "Categoria inválida. Tente novamente."
    canon = canonicalize_category_label(cat) or cat.lower()
    try:
        ok = update_launch_category(user_id, launch_id, canon)
    except Exception as exc:
        logger.exception("WA recategorize update failed launch=%s: %s", launch_id, exc)
        return "Não consegui atualizar agora. Tente de novo em instantes."
    if not ok:
        return "Lançamento não encontrado (talvez já tenha sido apagado)."
    return f"✅ Categoria do lançamento #{launch_id} atualizada para *{canon}*."


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
            log_system_event_sync(
                "warning",
                "whatsapp_attachment_download_failed",
                f"Falha ao baixar anexo do WhatsApp: {exc}",
                source="wa_runtime",
                details={"media_id": att.media_id},
            )
    return out


def _is_greeting(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", (text or "").strip().lower())
    return normalized in {"oi", "ola", "olá", "hello", "hi", "hey", "bom dia", "boa tarde", "boa noite"}


def _build_autolink_warning_message(status: str, auto_link_result: dict[str, Any]) -> str | None:
    if status == "no_match":
        return (
            "⚠️ Não encontrei nenhuma conta cadastrada com este número de WhatsApp.\n"
            "Crie sua conta no site usando este mesmo número ou use o fluxo de código/link para vincular."
        )
    if status == "multiple_accounts":
        return "⚠️ Encontrei mais de uma conta com este número. Não consegui vincular automaticamente."
    if status == "wa_linked_other_account":
        return "⚠️ Este WhatsApp já está vinculado a outra conta. Revise seu cadastro ou use outro número."
    if status == "account_has_other_whatsapp":
        return (
            "⚠️ Sua conta já tem outro WhatsApp vinculado. "
            f"Este número ({mask_phone(auto_link_result['wa_phone'])}) não foi conectado automaticamente."
        )
    return None


def _autolink_warning_already_sent(wa_id: str, status: str) -> bool:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT 1
                    FROM system_event_logs
                    WHERE event_type = 'whatsapp_autolink_greeting_warning_sent'
                      AND details->>'wa_id' = %s
                      AND details->>'status' = %s
                    LIMIT 1
                    """,
                    (wa_id, status),
                )
                return cur.fetchone() is not None
    except Exception as exc:
        logger.warning("WA autolink warning lookup failed wa_id=%s status=%s error=%s", wa_id, status, exc)
        return False


def _maybe_send_autolink_greeting_warning(
    reply_to: str,
    message_text: str,
    status: str,
    auto_link_result: dict[str, Any],
    user_id: int | None = None,
) -> bool:
    if not _is_greeting(message_text):
        return False

    body = _build_autolink_warning_message(status, auto_link_result)
    if not body:
        return False

    if _autolink_warning_already_sent(reply_to, status):
        return False

    _send_reply(reply_to, body)
    log_system_event_sync(
        "info",
        "whatsapp_autolink_greeting_warning_sent",
        "Aviso de vinculação automática enviado no primeiro greeting do WhatsApp.",
        source="wa_runtime",
        user_id=user_id,
        details={"wa_id": reply_to, "status": status},
    )
    return True


def process_message(message: InboundMessage) -> None:
    try:
        reply_to = message.wa_id
        logger.info(
            "WA process_message from=%s reply_to=%s text=%r attachments=%s",
            message.wa_id,
            reply_to,
            (message.text or "")[:120],
            len(message.attachments or []),
        )
        uid = get_or_create_canonical_user("whatsapp", message.wa_id)
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
                uid = resolved_uid
            if auto_link_result["status"] == "linked":
                logger.info(
                    "WA phone auto-link success wa_id=%s final_user_id=%s",
                    message.wa_id,
                    auto_link_result["user_id"],
                )
                log_system_event_sync(
                    "info",
                    "whatsapp_auto_link_success",
                    "Conta vinculada automaticamente ao WhatsApp.",
                    source="wa_runtime",
                    user_id=uid,
                    details={"wa_id": message.wa_id},
                )
                if _is_greeting(message.text or ""):
                    # Só interrompe para onboarding quando a mensagem era uma saudação.
                    # Se o usuário mandou "saldo", "gastei..." etc., segue e executa o comando.
                    try:
                        send_welcome(reply_to, user_id=uid)
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
        elif auto_link_result["status"] in {
            "no_match",
            "multiple_accounts",
            "wa_linked_other_account",
            "account_has_other_whatsapp",
        }:
            if _maybe_send_autolink_greeting_warning(
                reply_to,
                message.text or "",
                auto_link_result["status"],
                auto_link_result,
                user_id=uid,
            ):
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
                    log_system_event_sync(
                        "warning",
                        "whatsapp_tutorial_button_error",
                        f"Erro ao processar botao do tutorial no WhatsApp: {e}",
                        source="wa_runtime",
                        user_id=uid,
                    )
                return

            # Itens do menu de ajuda
            help_id = get_help_menu_id(raw_msg)
            if help_id:
                logger.info("WA help menu id=%s wa_id=%s", help_id, reply_to)
                try:
                    send_help_section(reply_to, help_id)
                except Exception as e:
                    logger.exception("WA help menu error id=%s: %s", help_id, e)
                    log_system_event_sync(
                        "warning",
                        "whatsapp_help_menu_error",
                        f"Erro ao processar menu de ajuda no WhatsApp: {e}",
                        source="wa_runtime",
                        user_id=uid,
                    )
                return

            # Botão "Categoria errada?" pós-lançamento (legado — agora a lista
            # vem direto na confirmação, mas mantemos o handler para mensagens
            # antigas ainda na tela do usuário).
            if interactive_id.startswith(WA_RECAT_BUTTON_PREFIX):
                try:
                    launch_id = int(interactive_id.split(":", 1)[1])
                except (ValueError, IndexError):
                    launch_id = 0
                logger.info("WA recategorize button clicked wa_id=%s launch=%s", reply_to, launch_id)
                if launch_id:
                    try:
                        _send_recategorize_list(
                            reply_to,
                            f"Escolha a nova categoria para o lançamento #{launch_id}.",
                            launch_id,
                        )
                    except Exception as exc:
                        logger.warning("WA send recat list failed: %s", exc)
                        _send_reply(reply_to, "Não consegui abrir a lista de categorias agora.")
                return

            # Item da lista de categorias → atualiza direto
            if interactive_id.startswith(WA_RECAT_PICK_PREFIX):
                tail = interactive_id[len(WA_RECAT_PICK_PREFIX):]
                lid_str, _, cat = tail.partition(":")
                try:
                    launch_id = int(lid_str)
                except ValueError:
                    launch_id = 0
                logger.info("WA recategorize pick wa_id=%s launch=%s cat=%s", reply_to, launch_id, cat)
                if launch_id and cat:
                    _send_reply(reply_to, _apply_recategorize(uid, launch_id, cat))
                return

            # "Outra (digitar)" → grava pending para a próxima mensagem virar a categoria
            if interactive_id.startswith(WA_RECAT_OTHER_PREFIX):
                try:
                    launch_id = int(interactive_id[len(WA_RECAT_OTHER_PREFIX):])
                except ValueError:
                    launch_id = 0
                logger.info("WA recategorize other clicked wa_id=%s launch=%s", reply_to, launch_id)
                if launch_id:
                    try:
                        set_pending_action(uid, "recategorize_launch_text", {"launch_id": launch_id}, minutes=5)
                    except Exception as exc:
                        logger.warning("WA set recat_text pending failed: %s", exc)
                    _send_reply(reply_to, "Digite a nova categoria para esse lançamento:")
                return

            # Botão de desfazer áudio
            if interactive_id == WA_UNDO_LAUNCH_ID:
                logger.info("WA undo_launch button clicked wa_id=%s", reply_to)
                # Injeta "desfazer" para o classificador tratar normalmente
                message.text = "desfazer"
            elif interactive_id == WA_DAILY_REPORT_DISABLE_ID:
                logger.info("WA daily_report_disable button clicked wa_id=%s uid=%s", reply_to, uid)
                try:
                    _send_reply(reply_to, h_report.disable(uid))
                except Exception as e:
                    logger.exception("WA daily_report_disable button error wa_id=%s: %s", reply_to, e)
                    log_system_event_sync(
                        "warning",
                        "whatsapp_daily_report_disable_button_error",
                        f"Erro ao processar botão de desligar report diário: {e}",
                        source="wa_runtime",
                        user_id=uid,
                    )
                return
            elif interactive_id.strip().lower() in WA_UPDATES_DISABLE_IDS:
                logger.info("WA updates disable button clicked wa_id=%s uid=%s", reply_to, uid)
                try:
                    set_whatsapp_updates_opt_out(uid, True)
                    _send_reply(
                        reply_to,
                        "Pronto, parei as atualizações do Piggy por aqui. Você pode religar quando quiser em Configurações > Notificações.",
                    )
                except Exception as e:
                    logger.exception("WA updates disable button error wa_id=%s: %s", reply_to, e)
                    log_system_event_sync(
                        "warning",
                        "whatsapp_updates_disable_button_error",
                        f"Erro ao processar botão de parar atualizações: {e}",
                        source="wa_runtime",
                        user_id=uid,
                    )
                return

        # ---------------------------------------------------------------
        # Interceptação: usuário escolheu "Outra (digitar)" e agora digitou
        # a categoria que quer aplicar ao lançamento.
        # ---------------------------------------------------------------
        if (message.text or "").strip():
            try:
                pending_recat = get_pending_action(uid)
            except Exception:
                pending_recat = None
            if pending_recat and pending_recat.get("action_type") == "recategorize_launch_text":
                launch_id = (pending_recat.get("payload") or {}).get("launch_id")
                try:
                    clear_pending_action(uid)
                except Exception as exc:
                    logger.warning("WA clear recat_text pending failed: %s", exc)
                if launch_id:
                    _send_reply(reply_to, _apply_recategorize(uid, int(launch_id), message.text))
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

        try:
            send_typing_indicator(msg_id)
        except Exception as exc:
            logger.warning("WA typing indicator failed message_id=%s error=%s", msg_id, exc)

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
                _send_reply_with_optional_buttons(reply_to, body, user_id=uid)
    except Exception as exc:
        logger.error("WA message processing failed wa_id=%s error=%s", message.wa_id, exc)
        log_system_event_sync(
            "error",
            "whatsapp_message_processing_failed",
            f"Falha no processamento da mensagem do WhatsApp: {exc}",
            source="wa_runtime",
            details={"wa_id": message.wa_id},
        )
        traceback.print_exc()


def process_payload(payload: dict[str, Any]) -> int:
    msgs = extract_messages(payload)
    logger.info("WA extracted messages=%s", len(msgs))
    for message in msgs:
        process_message(message)
    return len(msgs)
