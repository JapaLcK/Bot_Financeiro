# core/handle_incoming.py
"""
Ponto de entrada para todas as mensagens recebidas.

Fluxo:
  1. Anexo OFX?          → trata diretamente (não depende de intent)
  2. Ensure user no DB   → garante que o usuário existe
  3. Classifica intent   → core/intent_classifier.py (3 tiers: exact → regex → IA)
  4. Roteia              → core/intent_router.py
  5. Formata resposta    → core/response_formatter.py (Discord vs WhatsApp)
  6. Retorna OutgoingMessage
"""
from __future__ import annotations

import db
from core.types import IncomingMessage, OutgoingMessage
from core.intent_classifier import classify
from core.intent_router import route
from core.response_formatter import format_for_platform
from core.services.ofx_service import handle_ofx_import
from utils_text import fmt_brl
from ai_router import _internal_user_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_ofx_attachment(a) -> bool:
    fn = (getattr(a, "filename", "") or "").lower()
    ct = (getattr(a, "content_type", "") or "").lower()
    return fn.endswith(".ofx") or "ofx" in ct


def _normalize_user_id(msg: IncomingMessage) -> int:
    """
    Garante que o user_id seja sempre um int seguro,
    independente de vir como string longa (WhatsApp) ou int (Discord).
    """
    raw = msg.user_id
    try:
        uid = int(raw)
        # IDs do WhatsApp são enormes (>2 bilhões) → comprime
        if uid > 2_000_000_000:
            return _internal_user_id(raw)
        return uid
    except (ValueError, TypeError):
        return _internal_user_id(raw)


# ---------------------------------------------------------------------------
# Entrada principal
# ---------------------------------------------------------------------------

def handle_incoming(msg: IncomingMessage) -> list[OutgoingMessage]:
    platform = msg.platform

    # ------------------------------------------------------------------
    # 1. Anexo OFX — tratamento especial (não passa pelo classificador)
    # ------------------------------------------------------------------
    if msg.attachments:
        ofx_atts = [a for a in msg.attachments if _is_ofx_attachment(a)]
        if ofx_atts:
            a = ofx_atts[0]
            if not getattr(a, "data", None):
                return [OutgoingMessage(
                    text="📎 Recebi o OFX, mas não consegui baixar o arquivo. Reenvie o .ofx por favor."
                )]

            uid = _normalize_user_id(msg)
            db.ensure_user(uid)

            report = handle_ofx_import(str(uid), a.data, getattr(a, "filename", "arquivo.ofx"))

            # handle_ofx_import pode retornar str ou dict
            if isinstance(report, str):
                return [OutgoingMessage(text=report)]

            periodo   = f"{report.get('dt_start')} → {report.get('dt_end')}"
            total     = report.get("total_in_file")
            ins       = report.get("inserted")
            dup       = report.get("duplicates")
            saldo_raw = report.get("new_balance") or report.get("balance")
            saldo_txt = fmt_brl(float(saldo_raw)) if saldo_raw is not None else "(indisponível)"

            bold = lambda s: f"*{s}*" if platform == "whatsapp" else f"**{s}**"
            return [OutgoingMessage(text=(
                f"✅ {bold('OFX importado')}\n"
                f"📅 Período: {periodo}\n"
                f"🧾 Transações: {total}\n"
                f"➕ Inseridas: {ins} | ♻️ Duplicadas: {dup}\n"
                f"🏦 Saldo atual: {saldo_txt}"
            ))]

    # ------------------------------------------------------------------
    # 2. Garante usuário no banco
    # ------------------------------------------------------------------
    uid = _normalize_user_id(msg)
    db.ensure_user(uid)

    # Substitui o user_id normalizado para o restante do fluxo
    msg_normalized = IncomingMessage(
        platform=msg.platform,
        user_id=uid,
        text=msg.text,
        message_id=msg.message_id,
        attachments=msg.attachments,
        external_id=msg.external_id,
        raw=msg.raw,
    )

    # ------------------------------------------------------------------
    # 3. Classifica intenção
    # ------------------------------------------------------------------
    text = (msg.text or "").strip()
    if not text:
        return []

    intent_result = classify(text)

    # ------------------------------------------------------------------
    # 4. Roteia → executa → obtém resposta bruta
    # ------------------------------------------------------------------
    raw_response = route(intent_result, msg_normalized)

    # ------------------------------------------------------------------
    # 5. Formata para o canal
    # ------------------------------------------------------------------
    formatted = format_for_platform(raw_response, platform)

    return [OutgoingMessage(text=formatted)]
