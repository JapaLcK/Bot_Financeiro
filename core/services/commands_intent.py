"""
core/services/commands_intent.py — detecção de pergunta meta sobre
capacidades do bot.

Captura variações de "o que você faz?", "do que é capaz?", "quais
suas funções?", etc. — perguntas onde o user quer descobrir o catálogo
de tools em vez de executar alguma ação. Quando bate, o adapter dispara
o menu interativo `send_commands_menu` em vez de deixar a IA improvisar
texto.

Mantido em módulo próprio (puro Python, sem DB) pra ser testável e
reaproveitado entre WhatsApp e Discord.
"""
from __future__ import annotations

import re
import unicodedata


# Triggers exatos: o user já sabe que tem essa funcionalidade e digitou
# o comando direto. Match O(1) com set lookup.
EXACT_TRIGGERS: frozenset[str] = frozenset({
    "comandos", "/comandos",
    "exemplos", "/exemplos",
    "explorar", "/explorar",
    "o que pedir", "que pedir",
    "o que voce faz", "o que vc faz",
    "o que pode fazer", "o que voce pode fazer", "o que vc pode fazer",
    "lista de comandos", "listar comandos",
})


# Padrões pra capturar variações de pergunta meta. Cada padrão é uma
# regex compilada que match em texto normalizado (lowercase, sem acentos,
# sem pontuação final, espaços colapsados).
_META_PATTERNS: tuple[re.Pattern[str], ...] = (
    # "o que (que) (eu) (posso/da pra) (te) pedir/fazer/usar"
    re.compile(r"\bo que (?:que )?(?:eu )?(?:posso|da pra|de pra)? ?(?:te |vc |voce )?(?:pedir|pode pedir|posso pedir|fazer|posso fazer|usar)\b"),
    # "do que (voce/vc/tu)? (eh/e/esta) capaz" — sujeito opcional
    re.compile(r"\bdo que (?:(?:voce|vc|tu) )?(?:eh|e|esh|esta) capaz\b"),
    # "o que (voce/vc/tu) (sabe/consegue/faz/tem/pode) (fazer)?"
    re.compile(r"\bo que (?:voce|vc|tu) (?:sabe|consegue|faz|tem|pode)(?: fazer)?\b"),
    # "quais (sao) (as/suas) funcoes/funcionalidades/capacidades/opcoes/features/tools/comandos"
    re.compile(r"\bquais (?:sao )?(?:as |suas )?(?:funcoes|funcionalidades|capacidades|opcoes|features|tools|comandos)\b"),
    # "suas/as funcoes/funcionalidades/capacidades/features/tools"
    re.compile(r"\b(?:suas|as) (?:funcoes|funcionalidades|capacidades|features|tools)\b"),
    # "lista (de) (comandos/funcoes/features)"
    re.compile(r"\blista (?:de )?(?:comandos|funcoes|funcionalidades|features)\b"),
    # "me ajuda com o que/em que" — pedido genérico de descoberta
    re.compile(r"\bme ajuda com (?:o )?(?:que|quais)\b"),
)


def _normalize(text: str) -> str:
    """Lowercase, remove acentos, tira pontuação final, colapsa espaços."""
    if not text:
        return ""
    t = text.strip().lower()
    t = "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn")
    t = re.sub(r"[?!.,;:]+$", "", t)
    t = re.sub(r"\s+", " ", t)
    return t


def is_commands_intent(text: str | None) -> bool:
    """Retorna True se a mensagem é pergunta meta sobre capacidades.

    Compara contra EXACT_TRIGGERS primeiro (rápido), depois testa cada
    padrão da lista. Conservador — só dispara em frases que claramente
    perguntam "o que esse bot faz", evita falsos positivos em frases
    que mencionam "função" no meio de outra coisa.
    """
    norm = _normalize(text or "")
    if not norm:
        return False
    if norm in EXACT_TRIGGERS:
        return True
    return any(p.search(norm) for p in _META_PATTERNS)


__all__ = ["is_commands_intent", "EXACT_TRIGGERS"]
