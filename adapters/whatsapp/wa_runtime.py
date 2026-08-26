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
from math import isfinite
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
from adapters.whatsapp.wa_commands_menu import (
    get_commands_menu_id,
    send_commands_menu,
    send_commands_section,
)
from core.handle_incoming import handle_incoming
from core.handlers import report as h_report
from core.observability import log_system_event_sync
from core.types import IncomingMessage
from db import (
    attempt_whatsapp_phone_link,
    claim_pending_action,
    consume_pending_action,
    get_conn,
    get_or_create_canonical_user,
    get_pending_action,
    restore_pending_on_error,
    set_pending_action,
    set_whatsapp_updates_opt_out,
    update_launch_category,
)
from utils_phone import mask_phone

logger = logging.getLogger(__name__)

WA_CONFIRM_YES_ID = "confirm_yes"
WA_CONFIRM_NO_ID = "confirm_no"
WA_UNDO_LAUNCH_ID = "undo_launch"          # legado: undo do último (botão pós-áudio)
WA_UNDO_LAUNCH_PREFIX = "undo_launch:"     # undo de lançamento específico (pós-confirmação)
WA_DAILY_REPORT_DISABLE_ID = "daily_report_disable"
WA_WEEKLY_REPORT_DISABLE_ID = "weekly_report_disable"
WA_MONTHLY_REPORT_DISABLE_ID = "monthly_report_disable"
WA_RECAT_BUTTON_PREFIX = "recat:"          # botão pós-lançamento
WA_RECAT_PICK_PREFIX = "recatpick:"        # item da lista de categorias
WA_RECAT_OTHER_PREFIX = "recatother:"      # opção "outra (digitar)"
WA_DELETE_CC_PREFIX = "del_cc:"            # botão "Apagar" pós-compra no crédito
WA_BILL_PAID_PREFIX = "bill_paid:"         # botão "Já paguei" do lembrete de conta a pagar
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
        # Fail closed: sem segredo configurado, rejeita em vez de aceitar
        # qualquer POST não-assinado. Em prod APP_SECRET é obrigatório no boot.
        return False
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
        # Limpa imediatamente — só aparece uma vez. CONDICIONAL: se outra tarefa
        # já armou uma PERGUNTA nesta linha, apagar por cima a deixaria órfã.
        # Perder é inofensivo — o botão não depende da linha para funcionar
        # (`WA_UNDO_LAUNCH_ID` injeta "desfazer" no classificador).
        if user_id is not None:
            try:
                consume_pending_action(int(user_id), pending)
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

    # Confirmação de lançamento: dois botões — Trocar categoria abre a lista,
    # Desfazer dispara propose_delete pra esse lançamento específico (one-shot)
    elif pending and pending.get("action_type") == "recategorize_launch_offer":
        launch_id = (pending.get("payload") or {}).get("launch_id")
        if user_id is not None:
            try:
                consume_pending_action(int(user_id), pending)
            except Exception as exc:
                logger.warning("WA clear recategorize_offer pending failed: %s", exc)
        if launch_id:
            lid = int(launch_id)
            logger.info("WA sending launch action buttons to=%s launch_id=%s", to_wa_id, lid)
            try:
                send_interactive_buttons(
                    to=to_wa_id,
                    body=body,
                    buttons=[
                        {"id": f"{WA_RECAT_BUTTON_PREFIX}{lid}", "title": "📂 Trocar categoria"},
                        {"id": f"{WA_UNDO_LAUNCH_PREFIX}{lid}", "title": "↩️ Desfazer"},
                    ],
                    footer="Errou? Toque pra trocar a categoria ou desfazer",
                )
                return
            except Exception as exc:
                logger.warning("WA send launch action buttons failed, fallback list: %s", exc)
            # Fallback: lista direta de categorias (sem o botão Desfazer)
            try:
                _send_recategorize_list(to_wa_id, body, lid)
                return
            except Exception as exc:
                logger.warning("WA send recategorize list failed, fallback texto: %s", exc)

    # Botão "Apagar" pós-compra no crédito (one-shot, igual ao undo de áudio)
    elif pending and pending.get("action_type") == "delete_credit_purchase":
        tx_id = (pending.get("payload") or {}).get("tx_id")
        if user_id is not None:
            try:
                consume_pending_action(int(user_id), pending)
            except Exception as exc:
                logger.warning("WA clear delete_credit_purchase pending failed: %s", exc)
        if tx_id:
            cid = int(tx_id)
            logger.info("WA sending credit delete button to=%s tx_id=%s", to_wa_id, cid)
            try:
                send_interactive_buttons(
                    to=to_wa_id,
                    body=body,
                    buttons=[{"id": f"{WA_DELETE_CC_PREFIX}{cid}", "title": "🗑️ Apagar"}],
                    footer="Errou? Toque pra apagar essa compra",
                )
                return
            except Exception as exc:
                logger.warning("WA send credit delete button failed, fallback texto: %s", exc)

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
                # Limite de 10 rows por lista no WhatsApp (8 aqui + 2 em "Outras").
                # "mercado" entrou no lugar de "compras online", que é bem menos
                # frequente — quem precisa dela usa "✏️ Outra (digitar)".
                pick("alimentação"),
                pick("mercado"),
                pick("transporte"),
                pick("lazer"),
                pick("moradia"),
                pick("saúde"),
                pick("educação"),
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
    """Aplica uma nova categoria a um launch e devolve a mensagem de resposta.

    `launch_id` é o id INTERNO (vem do payload do botão). O display usa user_seq.
    """
    from utils_text import canonicalize_category_label  # local import (evita ciclo)
    from db import display_id_for

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
    display = display_id_for(user_id, launch_id)
    return f"✅ Categoria do lançamento #{display} atualizada para *{canon}*."


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


# Tentativa de vincular por código ("link 123456" / "vincular 123456"). Espelha
# os padrões do intent_classifier (account.link / account.vincular). Um número
# SEM conta usa exatamente esse fluxo pra se vincular, então não pode ser barrado
# pelo aviso de "crie sua conta". Usa o MESMO _normalize do classificador (que
# troca pontuação por espaço) — senão "link: 123456" ou "link 123456." casariam
# como vínculo lá no handle_incoming mas seriam barrados aqui, engolindo o código.
_LINK_CODE_RE = re.compile(r"^(?:link|vincular)\s+\d{6}$")


def _is_link_code_attempt(text: str) -> bool:
    from core.intent_classifier import _normalize
    return bool(_LINK_CODE_RE.match(_normalize(text or "")))


def _signup_url() -> str:
    from core.dashboard_links import get_dashboard_base_url
    base = get_dashboard_base_url()
    if not base.startswith("https://"):
        base = "https://pigbankai.com"
    return f"{base}/cadastro"


def _send_no_account_notice(reply_to: str, auto_link_result: dict[str, Any], user_id: int | None = None) -> None:
    """Número de WhatsApp sem NENHUMA conta vinculada por telefone. Sem esse
    aviso, um comando ("gastei 50") cairia numa conta fantasma invisível (paywall
    off / plans-v2 on) ou na mensagem de assinatura que assume conta existente
    (paywall on) — os dois confundem quem ainda não tem cadastro. Então avisamos
    e paramos aqui. Mandamos em TODA mensagem (nunca some em silêncio); o dedup
    é só do log, pra não inflar o system_event_logs."""
    _send_reply(
        reply_to,
        (
            "🐷 Oi! Ainda não tenho uma conta ligada a este número de WhatsApp.\n\n"
            "Pra eu começar a cuidar do seu dinheiro:\n"
            f"1️⃣ Crie sua conta grátis: {_signup_url()}\n"
            "   (use *este mesmo número* de WhatsApp)\n"
            "2️⃣ Volte aqui e me manda um *oi* — eu vinculo na hora ✅\n\n"
            "Já tem conta? Gere um código de vínculo no site e me manda: *link 123456*"
        ),
    )
    if not _autolink_warning_already_sent(reply_to, "no_match_notice"):
        log_system_event_sync(
            "info",
            "whatsapp_autolink_greeting_warning_sent",
            "Convite de cadastro enviado a número de WhatsApp sem conta.",
            source="wa_runtime",
            user_id=user_id,
            details={"wa_id": reply_to, "status": "no_match_notice"},
        )


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
                # Onboarding: tutorial só pra cliente NOVO. status == "linked"
                # significa que ele acabou de vincular NESTA primeira mensagem
                # (quem já mandou mensagem antes cai em "already_linked" e não
                # vê o tutorial). E só quando essa primeira mensagem é uma
                # SAUDAÇÃO: se o cliente já abre com um comando ("saldo",
                # "gastei 50..."), ele já sabe usar — a gente executa o comando
                # e não interrompe com o tour.
                if _is_greeting(message.text or ""):
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
        elif auto_link_result["status"] == "no_match":
            # Número sem conta alguma. Convida pro cadastro/vínculo e para aqui —
            # EXCETO quando a própria mensagem é o código de vínculo, que precisa
            # seguir pro handle_incoming pra ser consumido.
            if not _is_link_code_attempt(message.text or ""):
                _send_no_account_notice(reply_to, auto_link_result, user_id=uid)
                return
        elif auto_link_result["status"] in {
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

            # Itens do menu "O que pedir" (catalogo de comandos)
            cmds_id = get_commands_menu_id(raw_msg)
            if cmds_id:
                logger.info("WA commands menu id=%s wa_id=%s", cmds_id, reply_to)
                try:
                    send_commands_section(reply_to, cmds_id)
                except Exception as e:
                    logger.exception("WA commands menu error id=%s: %s", cmds_id, e)
                    log_system_event_sync(
                        "warning",
                        "whatsapp_commands_menu_error",
                        f"Erro ao processar menu de comandos no WhatsApp: {e}",
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
                    from db import display_id_for as _disp
                    try:
                        _send_recategorize_list(
                            reply_to,
                            f"Escolha a nova categoria para o lançamento #{_disp(uid, launch_id)}.",
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

            # Botão de desfazer um lançamento específico (pós-confirmação)
            if interactive_id.startswith(WA_UNDO_LAUNCH_PREFIX):
                try:
                    launch_id = int(interactive_id[len(WA_UNDO_LAUNCH_PREFIX):])
                except ValueError:
                    launch_id = 0
                logger.info("WA undo_launch (specific) clicked wa_id=%s launch=%s", reply_to, launch_id)
                if launch_id:
                    from core.handlers import launches as h_launches
                    try:
                        body = h_launches.propose_delete(uid, launch_id)
                    except Exception as exc:
                        logger.exception("WA propose_delete failed launch=%s: %s", launch_id, exc)
                        _send_reply(reply_to, "Não consegui preparar o desfazer agora. Tente em instantes.")
                        return
                    _send_reply_with_optional_buttons(reply_to, body, user_id=uid)
                return

            # Botão "🗑️ Apagar" da confirmação de compra no crédito.
            # Reusa o comando textual "apagar CCnn" — mesmo delete, mesma msg.
            if interactive_id.startswith(WA_DELETE_CC_PREFIX):
                try:
                    tx_id = int(interactive_id[len(WA_DELETE_CC_PREFIX):])
                except ValueError:
                    tx_id = 0
                logger.info("WA credit delete button clicked wa_id=%s tx=%s", reply_to, tx_id)
                if tx_id:
                    from core.handlers import credit as h_credit
                    try:
                        body = h_credit.handle(uid, f"apagar CC{tx_id}")
                    except Exception as exc:
                        logger.exception("WA credit delete failed tx=%s: %s", tx_id, exc)
                        _send_reply(reply_to, "Não consegui apagar essa compra agora. Tente em instantes.")
                        return
                    _send_reply(reply_to, body or f"Não achei a compra CC{tx_id}.")
                return

            # Botão "✅ Já paguei" do lembrete de conta a pagar (boleto).
            # Payload traz o id da conta → quita exatamente aquela, sem depender
            # de o usuário digitar o nome.
            if interactive_id.startswith(WA_BILL_PAID_PREFIX):
                try:
                    bill_id = int(interactive_id[len(WA_BILL_PAID_PREFIX):])
                except ValueError:
                    bill_id = 0
                logger.info("WA bill_paid button clicked wa_id=%s uid=%s bill=%s", reply_to, uid, bill_id)
                if not (bill_id and uid):
                    _send_reply(reply_to, "Não consegui identificar a conta desse lembrete.")
                    return
                from db.bills import get_bill, mark_bill_paid
                from utils_text import fmt_brl
                try:
                    bill = get_bill(uid, bill_id)
                except Exception as exc:
                    logger.exception("WA bill_paid get_bill failed bill=%s: %s", bill_id, exc)
                    _send_reply(reply_to, "Não consegui registrar o pagamento agora. Tente em instantes.")
                    return
                if bill is None or bill.get("status") == "paid":
                    _send_reply(reply_to, "Essa conta já estava paga (ou não achei mais). 👍")
                    return
                # Valor variável (água/luz): o estimado não serve — pergunta quanto
                # veio e a próxima mensagem (número) fecha o pagamento.
                #
                # DIVERGE de propósito (por enquanto) do `bill_amount_expected`
                # de core/handlers/bills.py, que é a MESMA pergunta feita por
                # texto: aqui o tipo é `bill_pay_amount`, o prazo é 30 min e o
                # `clear` acontece antes do pagamento (sem compare-and-swap na
                # hora de pagar). Isto não passa pelo `handle_incoming` — o
                # consumidor está logo abaixo, na linha :896 deste arquivo,
                # antes da chamada do handle_incoming em :1021 —, então não
                # precisa do `suprime_ia` do registro e não sofre o sequestro
                # pela IA. Unificar os dois é issue separada: este fluxo tem
                # "cancelar", valor estimado no texto e re-pergunta própria, e
                # mexer nele sem teste de botão é troca de bug conhecido por
                # bug novo.
                #
                # A GRAVAÇÃO, porém, é o mesmo recurso disputado (a linha única
                # de `pending_actions`), então usa `claim_pending_action` como
                # os outros dois: tocar este botão não pode apagar uma pergunta
                # viva (um valor já digitado, uma fila de multi-lançamento, uma
                # confirmação de apagar). Para o `claim`, porém, as duas portas
                # contam como a MESMA pergunta (`_PERGUNTA_DE_VALOR_DE_CONTA`,
                # db/pending.py): tocar aqui substitui a pergunta de valor que
                # veio por texto, senão o número seguinte pagaria a outra conta.
                if bill.get("variable_amount"):
                    nome_conta = bill.get("name") or "conta"
                    try:
                        guardou = claim_pending_action(
                            uid, "bill_pay_amount",
                            {"bill_id": bill_id, "name": bill.get("name")}, minutes=30,
                        )
                    except Exception as exc:
                        logger.warning("WA claim bill_pay_amount pending failed: %s", exc)
                        guardou = False
                    est = bill.get("amount")
                    hint = f" (estimado {fmt_brl(est)})" if est else ""
                    if not guardou:
                        # Perdeu para outra pergunta: sem a pendência, o número
                        # solto não fecha nada aqui — e a forma completa
                        # ("paguei luz 132,50") também não, porque a pergunta
                        # que sobreviveu é resolvida antes. Texto único em
                        # core/handlers/bills.py.
                        from core.handlers.bills import pergunta_de_valor_sem_contexto
                        _send_reply(
                            reply_to,
                            pergunta_de_valor_sem_contexto(uid, nome_conta),
                        )
                        return
                    _send_reply(
                        reply_to,
                        f"Quanto veio a conta de *{nome_conta}* este mês?{hint}\n"
                        f"É só mandar o valor. Ex: *132,50*",
                    )
                    return
                # Valor fixo: quita direto no valor cadastrado.
                try:
                    paid = mark_bill_paid(uid, bill_id)
                except Exception as exc:
                    logger.exception("WA bill_paid failed bill=%s: %s", bill_id, exc)
                    _send_reply(reply_to, "Não consegui registrar o pagamento agora. Tente em instantes.")
                    return
                if paid is None:
                    _send_reply(reply_to, "Essa conta já estava paga (ou não achei mais). 👍")
                else:
                    val = paid.get("paid_amount") or paid.get("amount") or 0
                    _send_reply(
                        reply_to,
                        f"✅ Conta paga: *{paid.get('name')}* — {fmt_brl(val)} lançado e "
                        f"categorizado. Tá tudo em dia! 🐷",
                    )
                return

            # Botão de desfazer áudio (legado: undo do último lançamento)
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
            elif interactive_id == WA_WEEKLY_REPORT_DISABLE_ID:
                logger.info("WA weekly_report_disable button clicked wa_id=%s uid=%s", reply_to, uid)
                try:
                    _send_reply(reply_to, h_report.disable_weekly(uid))
                except Exception as e:
                    logger.exception("WA weekly_report_disable button error wa_id=%s: %s", reply_to, e)
                    log_system_event_sync(
                        "warning",
                        "whatsapp_weekly_report_disable_button_error",
                        f"Erro ao processar botão de desligar resumo semanal: {e}",
                        source="wa_runtime",
                        user_id=uid,
                    )
                return
            elif interactive_id == WA_MONTHLY_REPORT_DISABLE_ID:
                logger.info("WA monthly_report_disable button clicked wa_id=%s uid=%s", reply_to, uid)
                try:
                    _send_reply(reply_to, h_report.disable_monthly(uid))
                except Exception as e:
                    logger.exception("WA monthly_report_disable button error wa_id=%s: %s", reply_to, e)
                    log_system_event_sync(
                        "warning",
                        "whatsapp_monthly_report_disable_button_error",
                        f"Erro ao processar botão de desligar resumo mensal: {e}",
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
                # Porteiro: `_apply_recategorize` reescreve a categoria do
                # lançamento. Se a linha já não é esta, o texto responde a outra
                # pergunta — cair fora deixa o roteador tratá-lo normalmente.
                try:
                    if not consume_pending_action(uid, pending_recat):
                        pending_recat = None
                        launch_id = None
                except Exception as exc:
                    logger.warning("WA clear recat_text pending failed: %s", exc)
                if launch_id:
                    _send_reply(reply_to, _apply_recategorize(uid, int(launch_id), message.text))
                    return

            # Conta a pagar de valor variável: o usuário tocou "✅ Já paguei" e
            # agora está mandando o valor real que veio no boleto.
            if pending_recat and pending_recat.get("action_type") == "bill_pay_amount":
                payload_bp = pending_recat.get("payload") or {}
                bill_id = payload_bp.get("bill_id")
                name = payload_bp.get("name") or "conta"
                txt = (message.text or "").strip()
                if txt.lower() in {"cancelar", "cancela", "nao", "não", "deixa", "depois"}:
                    try:
                        # Abandono, condicional: se perdeu, não havia nada
                        # nosso para abandonar.
                        consume_pending_action(uid, pending_recat)
                    except Exception:
                        pass
                    _send_reply(reply_to, f"Ok, deixei a conta de *{name}* pendente. Quando pagar é só avisar. 🐷")
                    return
                from core.handlers.bills import (agrupamento_de_milhar_ok,
                                                 limpa_pontuacao_final)
                from utils_text import parse_money, fmt_brl
                try:
                    # Mesma limpeza do `resolve_bill_amount`: sem ela "132,50."
                    # vira 13250.0 no `parse_money` e paga R$ 13.250,00. Esta é
                    # a outra porta da MESMA pergunta, então tem o mesmo furo.
                    # Idem o milhar malformado: "1.23.456" pagaria R$ 123.456.
                    limpo = limpa_pontuacao_final(txt)
                    amount = parse_money(limpo) if agrupamento_de_milhar_ok(limpo) else None
                except Exception:
                    amount = None
                # Arredonda para centavos e recusa não finito ANTES de pagar:
                # "0,001" passava no `> 0` e o `mark_bill_paid` respondia com o
                # erro genérico, perdendo a pendência. Aqui a pergunta fica de
                # pé e o usuário responde de novo. Mesma regra do
                # `resolve_bill_amount` (core/handlers/bills.py).
                if amount is not None and isfinite(amount):
                    amount = round(amount, 2)
                if amount is None or not isfinite(amount) or amount <= 0:
                    # não entendeu o valor → re-pergunta, mantém o pending
                    _send_reply(reply_to, f"Não peguei o valor. Manda só o número da conta de *{name}*. Ex: *132,50* (ou *cancelar*)")
                    return
                # REIVINDICA antes de pagar, e condicionado ao que foi lido:
                # duas respostas concorrentes chegariam as duas ao
                # `mark_bill_paid` e debitariam o saldo duas vezes. Quem perde
                # sai sem fazer nada — o vencedor responde. Mesmo desenho do
                # `resolve_bill_amount` (core/handlers/bills.py), que é a outra
                # porta da MESMA pergunta.
                try:
                    reivindicou = consume_pending_action(uid, pending_recat)
                except Exception as exc:
                    logger.warning("WA clear bill_pay_amount pending failed: %s", exc)
                    reivindicou = False
                if not reivindicou:
                    return
                if bill_id:
                    from db.bills import mark_bill_paid
                    try:
                        # Devolve a pergunta se o pagamento estourar: sem isso o
                        # "Tente em instantes" é mentira — a pendência já foi e o
                        # próximo número não paga nada. Prazo 30 min, o mesmo com
                        # que ela foi armada (:773). Mesmo desenho da outra porta
                        # desta pergunta (core/handlers/bills.py::resolve_bill_amount).
                        with restore_pending_on_error(uid, pending_recat, 30):
                            paid = mark_bill_paid(uid, int(bill_id), amount)
                    except Exception as exc:
                        logger.exception("WA bill_pay_amount mark failed bill=%s: %s", bill_id, exc)
                        _send_reply(reply_to, "Não consegui registrar o pagamento agora. Tente em instantes.")
                        return
                    if paid is None:
                        _send_reply(reply_to, "Essa conta já estava paga (ou não achei mais). 👍")
                    else:
                        val = paid.get("paid_amount") or paid.get("amount") or amount
                        _send_reply(
                            reply_to,
                            f"✅ Conta paga: *{paid.get('name')}* — {fmt_brl(val)} lançado e "
                            f"categorizado. Tá tudo em dia! 🐷",
                        )
                    return

        # ---------------------------------------------------------------
        # Interceptação de comandos de texto simples para fluxo interativo
        # ---------------------------------------------------------------
        text_cmd = (message.text or "").strip().lower()

        # "ajuda" → tutor pra quem ta aprendendo (send_help_menu, com link
        # pro tutorial).
        if text_cmd in {"ajuda", "help", "menu", "/ajuda", "/help", "/menu"}:
            logger.info("WA help menu via texto wa_id=%s", reply_to)
            try:
                send_help_menu(reply_to)
            except Exception as e:
                logger.warning("WA send_help_menu failed, usando texto: %s", e)
            else:
                return

        # "comandos"/"do que voce eh capaz"/"quais suas funcoes" → catalogo
        # COMPLETO de tools. Detecção permissiva pra cobrir variações que
        # antes caíam na IA e viravam texto improvisado.
        from core.services.commands_intent import is_commands_intent
        if is_commands_intent(message.text):
            logger.info("WA commands menu via intent wa_id=%s", reply_to)
            try:
                send_commands_menu(reply_to)
            except Exception as e:
                logger.warning("WA send_commands_menu failed, usando texto: %s", e)
            else:
                return

        if text_cmd in {"tutorial", "/tutorial"}:
            logger.info("WA tutorial welcome via texto wa_id=%s", reply_to)
            try:
                send_welcome(reply_to)
            except Exception as e:
                logger.warning("WA send_welcome failed, usando texto: %s", e)
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
