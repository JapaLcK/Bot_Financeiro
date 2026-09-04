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


# `*`, `_`, `~` e crase são os quatro delimitadores de marcação do WhatsApp.
# O `_` entra com fronteira, os outros três não — MEDIDO pelo dono no cliente
# real do WhatsApp em 2026-09-04: `meta_casa_nova` sai LITERAL (underscore no
# meio de palavra não pareia), enquanto `~x~` sai RISCADO (o til pareia mesmo
# colado à palavra). Sem a fronteira, todo nome com `_` no miolo perdia o
# negrito do bot sem que houvesse nada a consertar.
_MARCACAO_WA = re.compile(r"[*~`]|(?<!\w)_|_(?!\w)")


def wrap_wa_markup(text: object, delim: str = "*") -> str:
    """Embrulha texto do usuário em marcação do WhatsApp — só quando dá.

    O WhatsApp NÃO tem caractere de escape: `\\*` não existe e entidade HTML
    aparece literal na tela. Neutralizar o delimitador do usuário também não
    resolve — medido no cliente real do WhatsApp, `*a*<WJ>b*` (WJ = U+2060)
    renderiza `ab*`: o WORD JOINER cega a ABERTURA, e o FECHAMENTO olha o
    caractere ANTERIOR, não o seguinte. A saída é não embrulhar: se o texto
    do usuário já tem marcação, o negrito do bot sai de cena e o nome aparece
    inteiro.

    Custo aceito: perde-se o negrito nesses nomes. Nesse mesmo caso a
    formatação já estava quebrada antes, então não se perde nada que funcione.

    `delim` porque um dos sítios embrulha em crase (core/handlers/pending.py).
    `str(text)` de propósito: `paid.get("name")` pode ser `None`, e imprimir
    `None` é melhor que estourar `TypeError` no caminho do dinheiro.
    """
    text = str(text)
    return text if _MARCACAO_WA.search(text) else f"{delim}{text}{delim}"
