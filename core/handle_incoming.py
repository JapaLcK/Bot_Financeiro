# core/handle_incoming.py
"""
Ponto de entrada para todas as mensagens recebidas.

Fluxo:
  1. Anexo OFX?          → trata diretamente (não depende de intent)
  2. Anexo ÁUDIO?        → transcreve via Whisper e processa como texto
  3. Anexo IMAGEM?       → analisa via GPT-4o Vision e retorna dados para confirmação
  4. Ensure user no DB   → garante que o usuário existe
  5. Classifica intent   → core/intent_classifier.py (3 tiers: exact → regex → IA)
  6. Roteia              → core/intent_router.py
  7. Formata resposta    → core/response_formatter.py (Discord vs WhatsApp)
  8. Retorna OutgoingMessage
"""
from __future__ import annotations

import logging
import re
import traceback

import db
from core.types import IncomingMessage, OutgoingMessage
from core.intent_classifier import classify
from core.intent_router import route
from core.response_formatter import format_for_platform
from core.services.ofx_service import handle_ofx_import, handle_credit_ofx_import
from core.services.open_finance import handle_open_finance_whatsapp_command
from core.services.media_service import (
    is_audio_attachment,
    is_image_attachment,
    transcribe_audio,
    analyze_image,
)
from core.observability import log_system_event_sync
from core.services.plan_limits import PlanLimitExceeded
from utils_text import fmt_brl
from ai_router import _internal_user_id

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HELP_FALLBACK_MARKERS: tuple[str, ...] = (
    "Posso te ajudar com",       # "🧾 Posso te ajudar com fatura/pagamento assim:" e variantes
    "Não entendi exatamente",    # respostas de classifier confuso vindas dos handlers
)


# Pendências determinísticas que o route() retoma com PRIORIDADE MÁXIMA — o bot
# fez uma pergunta e está esperando a resposta. Espelha as checagens do topo do
# route(): clarification de lançamento ("Em que você gastou?") e os fluxos
# guiados de cartão. Enquanto uma delas está aberta, a resposta do user TEM que
# passar pelo route() determinístico — nunca pelo fallback de IA. Sem isso, uma
# resposta curta ("cinema", "dia 5") classifica como baixa confiança e a IA
# sequestra o turno, perdendo o contexto (ex: o valor 77,90 já informado) e
# falhando com "valor precisa ser maior que zero".
#
# NÃO inclui ofertas one-shot (recategorize_launch_offer, undo_audio): essas não
# são perguntas e não devem bloquear a IA nas mensagens seguintes.
_RESUMABLE_PENDING_TYPES: frozenset[str] = frozenset({
    "clarification",
    "credit_card_setup",
    "credit_card_set_primary",
    "credit_delete_card",
    "installment_pending",
    "pay_bill_choice",
})


def _looks_like_help_fallback(response: str | None) -> bool:
    """
    Detecta se `response` é o help genérico do bot tradicional (lista de
    comandos "tente assim"). Pra usuários Pro a gente prefere passar pra IA,
    que pode executar de fato com as tools ou dar uma resposta contextual
    melhor que a lista de comandos.

    Detecção por marker textual é frágil mas pragmática — os handlers usam
    esses dois prefixos de forma consistente. Se um dia migrarmos pra um
    sentinel explícito, basta trocar aqui.
    """
    if not response:
        return False
    return any(marker in response for marker in _HELP_FALLBACK_MARKERS)


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


def _bold(text: str, platform: str) -> str:
    """Formata texto em negrito de acordo com a plataforma."""
    if platform == "whatsapp":
        return f"*{text}*"
    return f"**{text}**"


# ---------------------------------------------------------------------------
# Handlers de mídia
# ---------------------------------------------------------------------------

def _split_audio_transactions(text: str) -> list[str]:
    """
    Detecta múltiplos lançamentos em um único áudio e os separa.
    Ex: "recebi 600 da mãe e gastei 100 no mercado" → ["recebi 600 da mãe", "gastei 100 no mercado"]
    Retorna lista com um único item se não detectar múltiplos lançamentos.
    """
    # Verbos que iniciam um lançamento financeiro
    FINANCIAL_VERBS = (
        r"gastei|paguei|comprei|debitei|mandei|enviei|pix|gasto|"
        r"recebi|ganhei|receita"
    )
    # Separadores que podem introduzir um segundo lançamento
    # "e gastei", "mas também recebi", "além disso gastei", etc.
    split_pattern = re.compile(
        r"\s+(?:e\s+também|também|mas\s+também|além\s+disso|e)\s+"
        rf"(?={FINANCIAL_VERBS})",
        re.IGNORECASE,
    )
    parts = split_pattern.split(text)
    cleaned = [p.strip() for p in parts if p.strip()]
    return cleaned if len(cleaned) > 1 else [text]


def _process_audio_transaction(uid: int, transcription: str, msg: IncomingMessage, platform: str) -> str:
    """
    Processa um único lançamento de áudio diretamente (sem confirmação).
    Retorna a resposta formatada.
    """
    intent_result = classify(transcription, user_id=uid)
    msg_from_audio = IncomingMessage(
        platform=msg.platform,
        user_id=uid,
        text=transcription,
        message_id=msg.message_id,
        attachments=[],
        external_id=msg.external_id,
        raw=msg.raw,
    )
    raw_response = route(intent_result, msg_from_audio)
    return format_for_platform(raw_response, platform)


def _handle_audio(msg: IncomingMessage, platform: str) -> list[OutgoingMessage] | None:
    """
    Detecta anexo de áudio, transcreve via Whisper e processa diretamente.

    Fluxo:
      1. Transcreve com Whisper
      2. Mostra o que foi entendido
      3. Detecta múltiplos lançamentos no mesmo áudio e processa cada um
      4. Processa sem confirmação — se errar, o usuário pode dizer "desfazer"

    Confirmação foi removida do áudio porque múltiplos áudios enviados ao mesmo
    tempo sobrescrevem a pendência no banco, causando perda de lançamentos.
    Imagens ainda usam confirmação (extração visual é menos confiável).
    """
    if not msg.attachments:
        return None

    audio_atts = [
        a for a in msg.attachments
        if is_audio_attachment(
            getattr(a, "filename", ""),
            getattr(a, "content_type", ""),
        )
    ]
    if not audio_atts:
        return None

    a = audio_atts[0]
    data = getattr(a, "data", None)
    filename = getattr(a, "filename", "audio.ogg")

    # Gate Pro: transcrição de áudio é Pro
    uid = _normalize_user_id(msg)
    try:
        from core.services.plan_service import is_pro
        if not is_pro(uid):
            return [OutgoingMessage(
                text=(
                    "🐷 Transcrever áudio é um recurso do PigBank+.\n"
                    "Dá uma olhada nos planos: https://pigbankai.com/precos"
                )
            )]
    except Exception:
        pass

    if not data:
        return [OutgoingMessage(
            text="🎙️ Recebi um áudio, mas não consegui baixar o arquivo. Tente reenviar."
        )]

    transcription = transcribe_audio(data, filename)

    if not transcription:
        return [OutgoingMessage(
            text=(
                "🎙️ Recebi seu áudio, mas não consegui entender o que foi dito.\n"
                "Tente reenviar em um ambiente mais silencioso ou escreva o comando."
            )
        )]

    db.ensure_user(uid)
    db.update_last_activity(uid)

    prefix = "_" if platform == "discord" else ""
    preview = f'🎙️ {prefix}Entendi: "{transcription}"{prefix}\n\n'

    # Detecta múltiplos lançamentos no mesmo áudio
    parts = _split_audio_transactions(transcription)

    responses = []
    for part in parts:
        result_text = _process_audio_transaction(uid, part, msg, platform)
        responses.append(result_text)

    body = "\n\n".join(responses)

    # Dica de desfazer
    if platform == "discord":
        undo_hint = "\n\n↩️ Para desfazer, diga: _desfazer_"
    else:
        # No WhatsApp, o botão "↩️ Desfazer" aparece na mensagem — salva pending para o runtime exibi-lo
        undo_hint = ""
        db.set_pending_action(uid, "undo_audio", {})

    return [OutgoingMessage(text=preview + body + undo_hint)]


def _handle_image(msg: IncomingMessage, platform: str) -> list[OutgoingMessage] | None:
    """
    Detecta anexo de imagem, analisa via GPT-4o Vision e retorna resumo para confirmação.
    Retorna lista de OutgoingMessage ou None se não houver imagem.
    """
    if not msg.attachments:
        return None

    image_atts = [
        a for a in msg.attachments
        if is_image_attachment(
            getattr(a, "filename", ""),
            getattr(a, "content_type", ""),
        )
    ]
    if not image_atts:
        return None

    a = image_atts[0]
    data = getattr(a, "data", None)
    filename = getattr(a, "filename", "image.jpg")

    # Gate Pro: leitura de cupom/comprovante por IA é Pro
    uid = _normalize_user_id(msg)
    try:
        from core.services.plan_service import is_pro
        if not is_pro(uid):
            return [OutgoingMessage(
                text=(
                    "🐷 Ler foto de cupom ou comprovante é um recurso do PigBank+.\n"
                    "Dá uma olhada nos planos: https://pigbankai.com/precos"
                )
            )]
    except Exception:
        pass

    if not data:
        return [OutgoingMessage(
            text="📷 Recebi uma imagem, mas não consegui baixá-la. Tente reenviar."
        )]

    result = analyze_image(data, filename)

    # Falha na API
    if result is None:
        return [OutgoingMessage(
            text=(
                "📷 Recebi sua imagem, mas não consegui analisá-la agora.\n"
                "Tente digitar o lançamento manualmente, ex: `gastei 50 no mercado`."
            )
        )]

    # Imagem sem dado financeiro
    if not result.get("tem_dado_financeiro"):
        return [OutgoingMessage(
            text=(
                "📷 Analisei a imagem, mas não encontrei informações financeiras nela.\n"
                "Se quiser registrar um gasto, escreva: `gastei [valor] [onde]`"
            )
        )]

    # Monta resumo dos dados extraídos
    tipo  = result.get("tipo") or "despesa"
    valor = result.get("valor")
    alvo  = result.get("alvo")
    data_str = result.get("data")
    cat   = result.get("categoria") or "outros"
    extra = result.get("descricao_extra") or ""

    tipo_emoji = "💸" if tipo == "despesa" else "💰"
    valor_txt  = fmt_brl(float(valor)) if valor is not None else "valor não identificado"
    alvo_txt   = alvo if alvo else "não identificado"
    data_txt   = data_str if data_str else "hoje"

    b = lambda s: _bold(s, platform)

    linhas = [
        f"📷 {b('Imagem analisada!')} Encontrei o seguinte:\n",
        f"{tipo_emoji} {b('Tipo:')} {tipo.capitalize()}",
        f"💵 {b('Valor:')} {valor_txt}",
        f"🏪 {b('Estabelecimento:')} {alvo_txt}",
        f"📅 {b('Data:')} {data_txt}",
        f"🏷️ {b('Categoria:')} {cat}",
    ]
    if extra:
        linhas.append(f"📝 {b('Detalhe:')} {extra}")

    # Instruções para confirmar ou corrigir (Discord usa texto, WhatsApp usa botões)
    if platform == "whatsapp":
        linhas += ["", "O lançamento está correto?"]
    else:
        linhas += [
            "",
            "➡️ Para confirmar e registrar, responda: `sim`",
            "✏️ Para corrigir, escreva o lançamento manualmente, ex: `gastei 50 no mercado`",
            "❌ Para cancelar, responda: `não`",
        ]

    # Salva os dados no banco temporário de pendências para o confirm.yes processar
    uid = _normalize_user_id(msg)
    db.ensure_user(uid)

    # Monta comando de texto equivalente para salvar como pendência
    if valor is not None:
        valor_fmt = f"{float(valor):.2f}".replace(".", ",")
        cmd_parts = [f"gastei {valor_fmt}" if tipo == "despesa" else f"recebi {valor_fmt}"]
        if alvo:
            cmd_parts.append(alvo)
        pending_text = " ".join(cmd_parts)
        db.set_pending_action(uid, "confirm_media_launch", {"text": pending_text})

    return [OutgoingMessage(text="\n".join(linhas))]


# ---------------------------------------------------------------------------
# Entrada principal
# ---------------------------------------------------------------------------

def handle_incoming(msg: IncomingMessage) -> list[OutgoingMessage]:
    platform = msg.platform

    try:
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

                # Detecta se é extrato bancário ou fatura de cartão de crédito
                from ofx_import import detect_ofx_type
                ofx_type = detect_ofx_type(a.data)
                ofx_filename = getattr(a, "filename", "arquivo.ofx")

                if ofx_type == "credit_card":
                    # Roteamento para importação de fatura de cartão
                    result_text = handle_credit_ofx_import(str(uid), a.data, ofx_filename)
                    return [OutgoingMessage(text=result_text)]

                # Extrato bancário (padrão)
                report = handle_ofx_import(str(uid), a.data, ofx_filename)

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
        # 2. Anexo ÁUDIO — transcreve via Whisper e processa como texto
        # ------------------------------------------------------------------
        audio_result = _handle_audio(msg, platform)
        if audio_result is not None:
            return audio_result

        # ------------------------------------------------------------------
        # 3. Anexo IMAGEM — analisa via Vision e pede confirmação
        # ------------------------------------------------------------------
        image_result = _handle_image(msg, platform)
        if image_result is not None:
            return image_result

        # ------------------------------------------------------------------
        # 4. Garante usuário no banco + registra atividade
        # ------------------------------------------------------------------
        uid = _normalize_user_id(msg)
        db.ensure_user(uid)
        db.update_last_activity(uid)

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
        # 5. Classifica intenção
        # ------------------------------------------------------------------
        text = (msg.text or "").strip()
        if not text:
            return []

        if platform == "whatsapp":
            open_finance_reply = handle_open_finance_whatsapp_command(uid, text)
            if open_finance_reply is not None:
                return [OutgoingMessage(text=open_finance_reply)]

        # Comandos de assinatura (assinar / cancelar / plano) — funcionam em
        # whatsapp e discord. Roda antes do classifier pra nao depender de
        # intent training.
        from core.services.billing_commands import handle_billing_command
        billing_reply = handle_billing_command(uid, text, platform=platform)
        if billing_reply is not None:
            return [OutgoingMessage(text=billing_reply)]

        # Chat IA (Pro v1 Fase 2): roteia pra IA se houver pending action
        # ou se a msg comecar com 'pergunta', 'piggy', 'ia'. Senao retorna None
        # e o fluxo segue normal pelo intent classifier.
        from core.services.ai_chat_commands import handle_ai_chat_command
        ai_reply = handle_ai_chat_command(uid, text, platform=platform)
        if ai_reply is not None:
            return [OutgoingMessage(text=ai_reply)]

        intent_result = classify(text, user_id=uid)

        # ------------------------------------------------------------------
        # 5b. Roteamento híbrido: se o classifier não reconheceu (out_of_scope
        # ou baixa confiança), tenta a IA conversacional pra users Pro. Cobre
        # variações que escapam dos regex do classifier — typos ("qual eh meu
        # saldo"), gírias ("qto sobrou"), fraseamento solto. Free segue no
        # fluxo padrão (mensagem "não entendi" com sugestões).
        # ------------------------------------------------------------------
        # Pendência determinística aguardando resposta tem precedência ABSOLUTA
        # sobre o fallback de IA. Ex: o bot perguntou "Em que você gastou R$
        # 77,90?" e guardou um pending de clarification. A resposta "cinema"
        # classifica como baixa confiança e, sem este guard, seria sequestrada
        # pela IA — que não conhece o valor já informado e falha com "valor
        # precisa ser maior que zero". route() resolve a pendência primeiro.
        has_resumable_pending = False
        try:
            _pend = db.get_pending_action(uid)
            if _pend and _pend.get("action_type") in _RESUMABLE_PENDING_TYPES:
                has_resumable_pending = True
        except Exception:
            has_resumable_pending = False

        should_try_ai_fallback = (
            not has_resumable_pending
            and (
                intent_result.intent == "out_of_scope"
                or intent_result.confidence < 0.55
            )
        )
        if should_try_ai_fallback:
            try:
                from core.services.plan_service import is_pro
                if is_pro(uid):
                    from core.services.ai_chat import chat as ai_chat_run
                    from core.services.ai_chat_commands import AI_CHAT_MONTHLY_LIMIT
                    ai_reply = ai_chat_run(
                        uid, text, monthly_limit=AI_CHAT_MONTHLY_LIMIT, platform=platform,
                    )
                    return [OutgoingMessage(text=ai_reply)]
            except Exception as exc:
                logger.warning(
                    "ai fallback falhou pra user %s: %s — caindo no fluxo normal",
                    uid, exc,
                )
                # segue pro route() abaixo (resposta padrão "não entendi")

        # ------------------------------------------------------------------
        # 6. Roteia → executa → obtém resposta bruta
        # ------------------------------------------------------------------
        raw_response = route(intent_result, msg_normalized)

        # ------------------------------------------------------------------
        # 6b. Post-route fallback: o bot tradicional reconheceu intent mas
        # caiu num help genérico ("Posso te ajudar com X assim..."). Pra Pro,
        # tenta a IA — ela tem tools pra executar de fato ou dar resposta
        # contextual melhor. Pra Free mantém o help (não tem IA mesmo).
        # ------------------------------------------------------------------
        if _looks_like_help_fallback(raw_response):
            try:
                from core.services.plan_service import is_pro
                if is_pro(uid):
                    from core.services.ai_chat import chat as ai_chat_run
                    from core.services.ai_chat_commands import AI_CHAT_MONTHLY_LIMIT
                    ai_reply = ai_chat_run(
                        uid, text, monthly_limit=AI_CHAT_MONTHLY_LIMIT, platform=platform,
                    )
                    return [OutgoingMessage(text=ai_reply)]
            except Exception as exc:
                logger.warning(
                    "help→AI fallback falhou pra user %s: %s — devolve help original",
                    uid, exc,
                )

        # ------------------------------------------------------------------
        # 7. Formata para o canal
        # ------------------------------------------------------------------
        formatted = format_for_platform(raw_response, platform)

        return [OutgoingMessage(text=formatted)]

    except PlanLimitExceeded as exc:
        # Limite de plano Free atingido — qualquer handler que toca DB
        # protegido (db.create_pocket, db.create_card, etc.) levanta isso.
        # Mensagem já vem amigável, com CTA pra upgrade.
        return [OutgoingMessage(text=format_for_platform(exc.message, msg.platform))]

    except Exception as exc:
        tb = traceback.format_exc()
        logger.error(
            "handle_incoming FAILED platform=%s user_id=%s text=%r error=%s",
            msg.platform,
            getattr(msg, "user_id", "?"),
            (msg.text or "")[:120],
            exc,
        )
        # Registra no banco para aparecer no dashboard de monitoramento
        try:
            uid_for_log = None
            try:
                uid_for_log = int(_normalize_user_id(msg))
            except Exception:
                pass
            log_system_event_sync(
                "error",
                "message_processing_failed",
                f"Falha ao processar mensagem ({msg.platform}): {exc}",
                source=f"handle_incoming/{msg.platform}",
                user_id=uid_for_log,
                details={
                    "text": (msg.text or "")[:200],
                    "platform": msg.platform,
                    "traceback": tb[-1500:],
                },
            )
        except Exception as log_exc:
            logger.error("Falha ao registrar erro no banco: %s", log_exc)

        # Retorna mensagem amigável ao usuário em vez de silêncio
        return [OutgoingMessage(
            text="⚠️ Ocorreu um erro interno ao processar sua mensagem. Tente novamente em instantes."
        )]
