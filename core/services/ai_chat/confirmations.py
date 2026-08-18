"""
core/services/ai_chat/confirmations.py — detecção de confirma/cancela.

Quando há uma `ai_pending_actions` no DB, a próxima mensagem do user é testada
contra estas listas antes de bater na OpenAI. Match por CONJUNTO (igualdade,
não substring) sobre o texto normalizado.

A normalização remove acentos e pontuação/emoji das bordas ("Sim!" → "sim",
"não 👍" → "nao"), o que amplia bastante a cobertura sem abrir mão da segurança:
seguimos exigindo igualdade exata com um item do conjunto, nunca `in`/substring —
match parcial geraria falsos positivos no chat normal (o user pode dizer "não vou
querer isso aqui" sem estar cancelando nada).
"""
from __future__ import annotations

import unicodedata


# Conjuntos SEM acento — o texto é normalizado (sem acento) antes da comparação.
_CONFIRM_EXACT: frozenset[str] = frozenset({
    # núcleo
    "sim", "s", "yes", "y", "ok", "okay", "confirma", "confirmar", "confirmo",
    "confirmado",
    # imperativos de "manda ver"
    "manda", "manda bala", "manda ver", "vai", "bora",
    # "pode"
    "pode", "pode sim", "pode mandar", "pode ir",
    # aprovação informal
    "blz", "beleza", "ta", "ta bom", "ta certo", "feito", "claro",
    "perfeito", "fechado", "combinado",
    # concordância
    "isso", "isso mesmo", "isso ai", "isso ai mesmo", "e isso", "eh isso",
    "aham", "uhum", "certo", "exato", "exatamente", "com certeza", "positivo",
})


_CANCEL_EXACT: frozenset[str] = frozenset({
    # núcleo
    "nao", "n", "no", "cancela", "cancelar", "cancelo", "cancelado",
    "negativo",
    # "deixa"
    "deixa", "deixa pra la", "deixa quieto", "deixa isso",
    # recusa
    "nao quero", "nao quero nao", "esquece", "esquece isso", "para",
    "para nao", "agora nao", "melhor nao", "nem", "nem vem",
})


def _normalize(text: str) -> str:
    """lowercase + tira acento + colapsa qualquer não-alfanumérico (emoji,
    pontuação, etc.) em espaço. Preserva palavras internas: "pode mandar!" →
    "pode mandar"."""
    t = (text or "").strip().lower()
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = "".join(c if (c.isalnum() or c.isspace()) else " " for c in t)
    return " ".join(t.split())


def is_confirm(text: str) -> bool:
    return _normalize(text) in _CONFIRM_EXACT


def is_cancel(text: str) -> bool:
    return _normalize(text) in _CANCEL_EXACT
