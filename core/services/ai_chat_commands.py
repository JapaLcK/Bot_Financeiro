"""
core/services/ai_chat_commands.py — handler do comando "pergunta" no bot.

Roteamento:
  - Se há pending action no DB (write esperando confirmação) → toda mensagem
    do user é roteada pra IA, que decide se executa/cancela/descarta.
  - Senão, se o user é **Pro**:
      • com prefix "piggy"/"pergunta"/"ia" → vai pra IA (prefix removido).
      • sem prefix → retorna None: os comandos determinísticos (saldo,
        listar, "apagar CC17", etc.) rodam pelo classify/route. A IA ainda
        cobre frases que o classifier não reconhece, via o fallback de baixa
        confiança em handle_incoming.
  - Senão (Free), só roteia pra IA se o texto começa com prefixo. Aí cai
    no gate Pro com mensagem "isso é PigBank+".
  - Senão → retorna None (segue o fluxo normal do bot tradicional).

Gate Pro: usuários Free recebem mensagem explícita "isso é PigBank+".
"""

from __future__ import annotations

import logging
import os
import re
import unicodedata
from datetime import timedelta

import db
from core.services.plan_service import ai_chat_allowed, ai_monthly_limit_for

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


# Por quanto tempo um "sim"/"não" solto ainda responde à última pergunta da IA.
# Mesmo horizonte da pendência da IA (db.ai_chat.PENDING_TTL_MINUTES).
_JANELA_DA_PERGUNTA = timedelta(minutes=10)


def pergunta_aberta_da_ia(user_id: int) -> int | None:
    """Id da última mensagem do histórico, se ela é uma pergunta da IA feita há
    pouco; senão None.

    A IA oferece coisas sem guardar pendência ("Quer que eu mostre suas maiores
    despesas?"), e o "sim" classifica como `confirm.yes` com confiança alta —
    não cai no fallback de IA, e o `route()` sem pendência responde "não
    entendi". Aqui o `handle_incoming` descobre que o "sim" é da IA.
    """
    last = db.ai_get_last_message(user_id)
    if (last
            and last["role"] == "assistant"
            and last["age"] < _JANELA_DA_PERGUNTA
            and re.search(r"\?\W*$", last["content"] or "")):
        return last["id"]
    return None


# `system` porque o widget do app (/ai/messages) não mostra essa role, e a IA
# ainda fica sabendo que a conversa foi interrompida.
_PERGUNTA_ENCERRADA = (
    "O usuário mandou outro comando, atendido fora desta conversa. "
    "A pergunta anterior não está mais em aberto."
)


def encerra_pergunta_da_ia(user_id: int, pergunta_id: int) -> None:
    """Fim de um turno do `handle_incoming`: se a pergunta que estava aberta
    continua sendo a última mensagem, a IA não atendeu este turno (ex.: "saldo",
    "plano", um OFX) — então ela deixa de estar em aberto. O `ai_messages` só vê
    os turnos da IA, e sem isto um "sim" depois voltaria para a oferta antiga."""
    try:
        db.ai_append_message_if_last(user_id, pergunta_id, "system", _PERGUNTA_ENCERRADA)
    except Exception as exc:
        logger.warning("encerra_pergunta_da_ia falhou pra user %s: %s", user_id, exc)


def handle_ai_chat_command(user_id: int, text: str, platform: str) -> str | None:
    """
    Detecta se a msg do user é uma interação com o chat IA e devolve a resposta.
    Retorna None se a msg NÃO é pra IA (segue fluxo normal do bot).

    Roteamento:
      1. Tem pending action → toda msg vai pra IA (mesmo sem prefixo).
      2. User é Pro + prefixo → vai pra IA. Pro sem prefixo → None (comandos
         determinísticos têm precedência; a IA cobre o resto via fallback).
      3. User é Free + msg começa com prefixo → cai no gate "isso é PigBank+".
      4. Senão (Free sem prefixo) → None (segue fluxo tradicional do bot).
    """
    text = (text or "").strip()
    if not text:
        return None

    # 1. Há pending action? Toda msg passa pela IA, qualquer plano.
    try:
        pending = db.ai_get_pending_action(user_id)
    except Exception as exc:
        logger.warning("ai_get_pending_action falhou: %s", exc)
        pending = None

    has_pending = bool(pending)

    # 2. Plano determina o roteamento default. v1: só Pro fala com a IA.
    # v2: todo tier tem IA — a cota mensal do tier é quem barra (ai_chat_allowed
    # devolve False com a cota estourada e o user cai na mensagem de upgrade).
    try:
        user_is_pro = ai_chat_allowed(user_id)
    except Exception as exc:
        logger.warning("ai_chat_allowed falhou: %s", exc)
        user_is_pro = False

    user_message: str | None = None

    if has_pending:
        # Guard anti-sequestro: o ai_pending só existe pra confirmar um write
        # (delete, etc.) esperando "sim"/"não". Se o user, em vez de confirmar,
        # manda OUTRO comando determinístico claro (ex: "Apagar id 5", "saldo"),
        # ele abandonou a confirmação — limpa o pending órfão e devolve None pra
        # o comando rodar pelo fluxo determinístico, em vez de ser engolido pela
        # IA (que geraria uma 2ª confirmação, inconsistente e empilhada).
        # "sim"/"não" (confirm.*) e frases ambíguas (out_of_scope/baixa
        # confiança) seguem pra IA normalmente — podem retomar/cancelar o pending.
        try:
            from core.intent_classifier import classify as _classify
            _res = _classify(text, user_id=user_id)
            _is_confirm = _res.intent in ("confirm.yes", "confirm.no")
            if (not _is_confirm
                    and _res.intent != "out_of_scope"
                    and _res.confidence >= 0.55):
                # Abandono: se perdeu o CAS, não havia nada nosso pra abandonar.
                db.ai_consume_pending_action(user_id, pending)
                return None
        except Exception as exc:
            logger.warning("guard anti-sequestro do ai_pending falhou: %s", exc)

        # Pending action sempre passa, com texto cru. Se Pro também tem
        # prefix, removemos pra não confundir a IA.
        if user_is_pro:
            stripped = _strip_ai_prefix(text)
            user_message = stripped if stripped else text
        else:
            user_message = text
    elif user_is_pro:
        # Pro sem pending. O prefix "piggy"/"ia"/"pergunta" é o sinal explícito
        # de que o user quer falar com a IA — só nesse caso interceptamos aqui.
        # Sem prefix, devolvemos None pra deixar os comandos determinísticos
        # (saldo, listar lançamentos, "apagar CC17", etc.) rodarem pelo
        # classify/route. A IA ainda cobre o que o classifier não reconhecer,
        # via o fallback de baixa confiança em handle_incoming.
        stripped = _strip_ai_prefix(text)
        if stripped is not None:
            # Prefix presente: usa o resto (mesmo se vazio — "piggy" sozinho
            # vira saudação ambígua tratada pela IA).
            user_message = stripped or text
        else:
            return None
    else:
        # Free sem pending: prefix obrigatório pra cair no gate Pro.
        stripped = _strip_ai_prefix(text)
        if stripped is None:
            return None
        # Cai no gate Pro abaixo (mesmo se for só "piggy" puro).

    # 3. Sem acesso à IA → mensagem de upgrade (ou cota estourada, no v2).
    if not user_is_pro:
        if has_pending:
            # Edge case: tinha pending e o user perdeu o acesso no meio. Limpa
            # pra não deixar o estado preso.
            try:
                # Abandono: retorno ignorado (`pending` garantido não-None por has_pending).
                db.ai_consume_pending_action(user_id, pending)
            except Exception:
                pass
        try:
            from core.services.plan_service import (
                plans_v2_enabled, get_user_limits, get_plan_tier, tier_at_least,
            )
            if plans_v2_enabled() and get_user_limits(user_id)["ai_conversational_enabled"]:
                tier = get_plan_tier(user_id)
                acabou = "🐷 Suas mensagens com o Piggy deste mês acabaram!\n"
                # A cota vira no dia 1º (db/ai_quota._current_month_start).
                # Plus e Pro têm o mesmo teto: subir de um pro outro não dá mais mensagens.
                if tier_at_least(tier, "plus"):
                    return acabou + "Elas renovam no dia 1º."
                if tier == "essencial":
                    return (
                        acabou + "Elas renovam no dia 1º. No Plus você tem mais mensagens: "
                        "https://pigbankai.com/precos"
                    )
                return acabou + "Nos planos pagos a conversa continua: https://pigbankai.com/precos"
        except Exception:
            pass
        return (
            "🐷 Conversar com a IA é um recurso do PigBank+.\n"
            "Dá uma olhada nos planos: https://pigbankai.com/precos"
        )

    # 4. Tem acesso: roteia pra IA (cota do tier).
    from core.services.ai_chat import chat as ai_chat_run
    try:
        return ai_chat_run(
            user_id,
            user_message,
            monthly_limit=ai_monthly_limit_for(user_id),
            platform=platform,
        )
    except Exception as exc:
        logger.error("ai_chat_run falhou pra user %s: %s", user_id, exc)
        return (
            "🐷 Deu ruim aqui — tenta de novo. "
            "Se persistir, fala com a gente: suporte@pigbankai.com"
        )
