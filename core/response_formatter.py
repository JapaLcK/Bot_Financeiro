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
