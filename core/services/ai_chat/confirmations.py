"""
core/services/ai_chat/confirmations.py — detecção de confirma/cancela.

Quando há uma `ai_pending_actions` no DB, a próxima mensagem do user é testada
contra estas listas antes de bater na OpenAI. Match exato (lowercase, strip),
sem normalização de acentos — match parcial geraria falsos positivos no chat
normal (o user pode dizer "não vou querer isso aqui" sem cancelar).
"""
from __future__ import annotations


_CONFIRM_EXACT: frozenset[str] = frozenset({
    "sim", "s", "yes", "y", "ok", "okay", "confirma", "confirmar", "confirmo",
    "manda", "manda bala", "vai", "pode", "pode mandar", "blz", "beleza",
    "ta", "tá", "ta bom", "tá bom", "feito", "claro",
})


_CANCEL_EXACT: frozenset[str] = frozenset({
    "não", "nao", "n", "no", "cancela", "cancelar", "cancelo",
    "deixa", "deixa pra lá", "deixa pra la", "não quero", "nao quero",
    "esquece", "para", "pára",
})


def is_confirm(text: str) -> bool:
    return (text or "").strip().lower() in _CONFIRM_EXACT


def is_cancel(text: str) -> bool:
    return (text or "").strip().lower() in _CANCEL_EXACT
