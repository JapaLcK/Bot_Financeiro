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
from contextvars import ContextVar
from datetime import timedelta

import db
from core.services.ai_chat.runner import ERROR_MSG
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
    """Se a IA deixou uma pergunta feita há pouco, o id da ÚLTIMA mensagem do
    histórico (a âncora do `encerra_pergunta_da_ia`); senão None.

    A IA oferece coisas sem guardar pendência ("Quer que eu mostre suas maiores
    despesas?"), e o "sim" classifica como `confirm.yes` com confiança alta —
    não cai no fallback de IA, e o `route()` sem pendência responde "não
    entendi". Aqui o `handle_incoming` descobre que o "sim" é da IA.

    Turno da IA que falhou não fecha a pergunta: o runner grava `user` +
    `assistant(ERROR_MSG)`, ou só o `user` se a exceção escapou dele. Sem pular
    esses turnos, a nova tentativa da resposta ("300 reais transporte") cairia
    no `route()` e viraria despesa. A janela conta da pergunta, não da falha.
    """
    # ponytail: olha só as 6 últimas (3 turnos falhos seguidos); mais que isso
    # a pergunta fica fechada e o texto segue o route().
    rows = db.ai_get_last_messages(user_id, 6)
    for m in rows:
        if m["role"] == "user" or (m["role"] == "assistant" and m["content"] == ERROR_MSG):
            continue
        if (m["role"] == "assistant"
                and m["age"] < _JANELA_DA_PERGUNTA
                and re.search(r"\?\W*$", m["content"] or "")):
            return rows[0]["id"]
        return None
    return None


# `system` porque o widget do app (/ai/messages) não mostra essa role, e a IA
# ainda fica sabendo que a conversa foi interrompida.
_PERGUNTA_ENCERRADA = (
    "O usuário mandou outro comando, atendido fora desta conversa. "
    "A pergunta anterior não está mais em aberto."
)


# O que o turno do `handle_incoming` faz com a pergunta aberta da IA:
#   None    — leu a mensagem e a atendeu fora da IA: o `finally` encerra.
#   MANTEM  — foi para a IA (resposta ou ERROR_MSG), não leu a mensagem (áudio
#             antes de virar texto, texto vazio) ou falhou: a pergunta segue.
#             O histórico não prova a ida à IA: o runner devolve ERROR_MSG sem
#             gravar nada, e a nova tentativa ("300 reais transporte") cairia
#             no route() como despesa.
#   ENCERRA — largou a `ai_pending` com um comando claro: sem captura (o
#             comando vai ao route()), e o `finally` encerra.
MANTEM, ENCERRA = "mantem", "encerra"
pergunta_no_turno: ContextVar[str | None] = ContextVar("pergunta_no_turno", default=None)


def encerra_pergunta_da_ia(user_id: int, ultima_id: int) -> None:
    """Fim de um turno do `handle_incoming` que NÃO foi para a IA (ex.:
    "saldo", "plano", um OFX): a pergunta deixa de estar em aberto. O
    `ai_messages` só vê os turnos da IA, e sem isto um "sim" depois voltaria
    para a oferta antiga. Só grava se `ultima_id` (a âncora lida por
    `pergunta_aberta_da_ia`) ainda for a última linha: a IA do app pode ter
    respondido no meio, e aí a pergunta aberta é a dela."""
    try:
        db.ai_append_message_if_last(user_id, ultima_id, "system", _PERGUNTA_ENCERRADA)
    except Exception as exc:
        logger.warning("encerra_pergunta_da_ia falhou pra user %s: %s", user_id, exc)


def aviso_de_cota(user_id: int) -> str | None:
    """Para quem já está sem a IA (`ai_chat_allowed` False): o aviso de cota
    esgotada, ou None se o motivo não é a cota (v1 sem Pro). No v2 todo tier
    com IA só a perde pela cota mensal."""
    from core.services.plan_service import (
        plans_v2_enabled, get_user_limits, get_plan_tier, tier_at_least,
    )
    if not (plans_v2_enabled() and get_user_limits(user_id)["ai_conversational_enabled"]):
        return None
    tier = get_plan_tier(user_id)
    acabou = "🐷 Suas mensagens com a Piggy deste mês acabaram!\n"
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
                pergunta_no_turno.set(ENCERRA)
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
            aviso = aviso_de_cota(user_id)
            if aviso:
                return aviso
        except Exception:
            pass
        return (
            "🐷 Conversar com a IA é um recurso do PigBank+.\n"
            "Dá uma olhada nos planos: https://pigbankai.com/precos"
        )

    # 4. Tem acesso: roteia pra IA (cota do tier).
    pergunta_no_turno.set(MANTEM)
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
