"""
core/services/ai_chat_commands.py — handler do comando "pergunta" no bot.

Roteamento:
  - Se há pending action no DB (write esperando confirmação) → toda mensagem
    do user é roteada pra IA, que decide se executa/cancela/descarta.
  - Senão, se o texto começa com prefixo de IA ("pergunta", "piggy", "ia") →
    roteia pra IA com o resto da frase.
  - Senão → retorna None (segue o fluxo normal do bot).

Gate Pro: usuários Free recebem mensagem explícita "isso é PigBank+".
"""

from __future__ import annotations

import logging
import os
import unicodedata

import db
from core.services.plan_service import is_pro

logger = logging.getLogger(__name__)

AI_CHAT_MONTHLY_LIMIT = int(os.getenv("AI_CHAT_MONTHLY_LIMIT", "1000"))


# Prefixos aceitos pra "chamar" o Piggy IA. Case-insensitive, sem acentos.
# Mantém uma lista enxuta — adicionar prefix demais polui detecção.
_PREFIXES = (
    "pergunta",
    "piggy",
    "ia",
)


def _normalize_prefix(text: str) -> str:
    t = (text or "").strip().lower()
    t = "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn")
    return t


def _strip_ai_prefix(text: str) -> str | None:
    """
    Se o texto começa com um prefixo de IA ('pergunta X', 'piggy X', 'ia X'),
    retorna o resto da frase. Senão retorna None.

    Aceita variações: 'piggy, X' / 'piggy: X' / 'piggy X'.
    """
    raw = (text or "").strip()
    if not raw:
        return None

    norm = _normalize_prefix(raw)
    for prefix in _PREFIXES:
        if norm == prefix:
            # Apenas "piggy" ou "pergunta" puros → trata como saudação ambígua
            return ""
        for sep in (" ", ", ", ": ", "."):
            if norm.startswith(prefix + sep):
                # Encontra o sep no texto original (preservando case)
                lower_raw = raw.lower()
                idx = lower_raw.find(sep, len(prefix))
                if idx == -1:
                    continue
                return raw[idx + len(sep):].strip()
    return None


def handle_ai_chat_command(user_id: int, text: str, platform: str) -> str | None:
    """
    Detecta se a msg do user é uma interação com o chat IA e devolve a resposta.
    Retorna None se a msg NÃO é pra IA (segue fluxo normal do bot).

    Roteamento:
      1. Tem pending action → toda msg vai pra IA (mesmo sem prefixo)
      2. Msg começa com prefixo IA → vai pra IA
      3. Senão → None
    """
    text = (text or "").strip()
    if not text:
        return None

    # 1. Há pending action? Toda msg passa pela IA.
    try:
        pending = db.ai_get_pending_action(user_id)
    except Exception as exc:
        logger.warning("ai_get_pending_action falhou: %s", exc)
        pending = None

    has_pending = bool(pending)
    user_message: str | None = None

    if has_pending:
        user_message = text
    else:
        stripped = _strip_ai_prefix(text)
        if stripped is None:
            return None
        # Prefixo presente, mas sem conteúdo (só "piggy" puro)
        if not stripped:
            return (
                "🐷 Manda sua pergunta aí depois do meu nome.\n"
                'Ex.: "piggy, quanto gastei em alimentação esse mês?"'
            )
        user_message = stripped

    # 2. Gate Pro
    try:
        user_is_pro = is_pro(user_id)
    except Exception as exc:
        logger.warning("is_pro falhou: %s", exc)
        user_is_pro = False

    if not user_is_pro:
        # Se tinha pending e o user virou Free no meio (edge case), limpa
        if has_pending:
            try:
                db.ai_clear_pending_action(user_id)
            except Exception:
                pass
        return (
            "🐷 Conversar com a IA é um recurso do PigBank+.\n"
            "Dá uma olhada nos planos: https://pigbankai.com/precos"
        )

    # 3. Roteia pra IA
    from core.services.ai_chat import chat as ai_chat_run
    try:
        return ai_chat_run(user_id, user_message, monthly_limit=AI_CHAT_MONTHLY_LIMIT)
    except Exception as exc:
        logger.error("ai_chat_run falhou pra user %s: %s", user_id, exc)
        return (
            "🐷 Deu ruim aqui — tenta de novo. "
            "Se persistir, fala com a gente: suporte@pigbankai.com"
        )
