# core/response_formatter.py
"""
Formata o texto de resposta de acordo com a plataforma.
Handlers sempre retornam texto no estilo Discord (**bold**).
Este módulo converte para o formato certo de cada canal.
"""
from __future__ import annotations
import re


def format_for_platform(text: str, platform: str) -> str:
    """
    Converte **bold** (padrão Discord) para o formato correto de cada canal.
    """
    if platform == "whatsapp":
        # WhatsApp usa *bold* (um asterisco)
        text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
        # Remove blocos de código inline (WhatsApp não renderiza `code`)
        text = re.sub(r"`(.+?)`", r"\1", text)

    elif platform == "discord":
        # Discord já usa **bold** — nada a fazer
        pass

    elif platform == "telegram":
        # Telegram MarkdownV2 usa *bold*
        text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)

    return text


# WORD JOINER (U+2060): invisível, sem quebra, e da categoria Cf — a mesma que
# `_normalize_category_name` (db/categories.py:276) já converte em espaço. Medido:
# copiar a resposta do bot e mandá-la de volta como nome grava `a* b`, não o
# invisível — o banco nunca guarda o caractere.
_WORD_JOINER = "\u2060"

# `*`, `_`, `~` e crase são os quatro delimitadores de marcação do WhatsApp.
_MARCACAO_WA = re.compile(r"[*_~`]")


def escape_wa_markup(text: object) -> str:
    """Neutraliza marcação do WhatsApp em texto que veio do usuário.

    O WhatsApp NÃO tem caractere de escape: `\\*` não existe e entidade HTML
    aparece literal na tela. A saída é quebrar o PAREAMENTO — um WORD JOINER
    logo depois de cada delimitador — sem alterar o texto visível.

    Só para o argumento interpolado, nunca para a mensagem montada: os
    handlers misturam marcação própria e texto do usuário na mesma f-string
    (ex.: core/handlers/pockets.py:124), e depois de interpolar não há como
    distinguir as duas.
    """
    return _MARCACAO_WA.sub(lambda m: m.group() + _WORD_JOINER, str(text))
