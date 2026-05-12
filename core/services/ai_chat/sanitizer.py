"""
core/services/ai_chat/sanitizer.py — saneamento defensivo da saída e do roteamento da IA.

Existe porque o LLM (gpt-4o-mini) é teimoso com 2 regras do system prompt:

1. **Regra 0 (anti-markdown headers)**: não escreva `#`, `##`, `###`. O
   WhatsApp não renderiza, o user vê literal. Mesmo com a regra dura e
   exemplo concreto, o modelo às vezes solta `### Resumo`. Aqui a gente
   converte `### Foo` em `*Foo*` (bold WhatsApp) como rede de proteção.

2. **Regra de tendência**: pergunta com "tendência"/"evolução"/"mês a mês"
   + janela temporal DEVE ir pra `get_spending_trend`, nunca pra
   `report_out_of_scope`. O LLM ainda erra. Aqui a gente detecta a
   heurística no texto do user e o runner usa pra fazer override quando
   o LLM escolhe a tool errada.

Mantido em módulo próprio pra que os helpers sejam testáveis sem mockar OpenAI.
"""
from __future__ import annotations

import re


_MD_HEADER_RE = re.compile(r"^[ \t]*#{1,6}[ \t]+(.+?)[ \t]*$", re.MULTILINE)


def strip_markdown_headers(text: str | None) -> str | None:
    """Converte linhas tipo `### Foo` em `*Foo*` (bold WhatsApp).

    Preserva None/strings vazias. Só pega `#` no início da linha (com
    espaço opcional antes); não toca `#` no meio da frase nem em
    hashtags inline.
    """
    if not text:
        return text
    return _MD_HEADER_RE.sub(r"*\1*", text)


# Heurística pra Bug 2: pergunta de tendência caindo em fallback.
_TREND_WORDS = (
    "tendência", "tendencia",
    "evolução", "evolucao",
    "mês a mês", "mes a mes",
    "mes a mes,", "mês a mês,",
)

_YEAR_WORDS = (
    "deste ano", "do ano", "este ano", "no ano",
    "anual", "ano financeiro", "ano todo", "ano inteiro",
)

_QUARTER_WORDS = ("trimestre", "trimestral")

_NMONTHS_RE = re.compile(r"\b(\d{1,2})\s*(?:mes(?:es)?|mês)\b", re.IGNORECASE)


def detect_trend_window(user_text: str | None) -> int | None:
    """Detecta se a mensagem do user é uma pergunta de tendência temporal.

    Retorna o número de meses sugerido (1–24) ou None se não casar.

    Mapeamento:
      - "deste ano"/"do ano"/"anual"  → 12
      - "trimestre"/"trimestral"      → 3
      - "últimos N meses" / "N meses" → N (clamped 1–24)
      - "tendência" sozinho           → 6 (default da tool)

    Falsos positivos custam: o runner vai trocar `report_out_of_scope`
    por `get_spending_trend`. Pra reduzir risco, exige que UMA palavra
    de tendência apareça — não dispara em frases tipo "no ritmo atual".
    """
    if not user_text:
        return None
    t = user_text.lower()

    has_trend = any(k in t for k in _TREND_WORDS)
    if not has_trend:
        return None

    if any(k in t for k in _YEAR_WORDS):
        return 12
    if any(k in t for k in _QUARTER_WORDS):
        return 3

    m = _NMONTHS_RE.search(t)
    if m:
        try:
            n = int(m.group(1))
        except ValueError:
            n = 6
        return max(1, min(n, 24))

    return 6


__all__ = ["strip_markdown_headers", "detect_trend_window"]
