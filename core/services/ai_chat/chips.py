"""
core/services/ai_chat/chips.py — chips de continuação do chat do app.

Uma segunda chamada, curta, DEPOIS do turno: 2 a 4 próximas perguntas que o
usuário poderia tocar. Só o `executar` do streaming chama (nunca WhatsApp nem
`/ai/chat`). Cliente próprio, sem retentativa e nunca via `streaming.completar`
(com emissor ela faria stream e mandaria o JSON dos chips como `delta`).
Qualquer falha vira `[]`: chip é enfeite, não pode derrubar o `fim`.
"""
from __future__ import annotations

import json
import logging
import os
import unicodedata

from .confirmations import is_cancel, is_confirm

logger = logging.getLogger(__name__)

MODELO_CHIPS = "gpt-4o-mini"
TIMEOUT_S = 4.0
MAX_CHIPS = 4
MAX_UTF16 = 40  # o `z.string().max(40)` do app conta unidades UTF-16

PROMPT_CHIPS = (
    "Você sugere o que o usuário pode perguntar a seguir num app de finanças pessoais. "
    "Devolva de 2 a 4 perguntas curtas (até 40 caracteres), em português, na voz do "
    "usuário (primeira pessoa), sobre o assunto da resposta dada. Só perguntas de "
    "consulta: nunca sugira confirmar, cancelar, registrar, apagar ou pagar nada, "
    "nem 'sim' ou 'não'."
)

_FORMATO = {"type": "json_schema", "json_schema": {
    "name": "chips", "strict": True,
    "schema": {"type": "object", "additionalProperties": False, "required": ["chips"],
               "properties": {"chips": {"type": "array", "items": {"type": "string"}}}}}}


def _ruim(t: str) -> bool:
    """Controle/quebra (Cc, Zl, Zp), surrogate solto (Cs) ou Cf que não seja o ZWJ
    dos emojis compostos (barra bidi override e afins)."""
    return any((c := unicodedata.category(ch)) in ("Cc", "Zl", "Zp", "Cs")
               or (c == "Cf" and ch != "\u200d") for ch in t)


def validar(itens) -> list[str]:
    if not isinstance(itens, list):
        return []
    vistos, out = set(), []
    for t in itens:
        if not isinstance(t, str):
            continue
        t = t.strip()
        if (not t or _ruim(t) or not any(unicodedata.category(c)[0] in "LNPS" for c in t)
                or len(t.encode("utf-16-le")) // 2 > MAX_UTF16
                or t.casefold() in vistos or is_confirm(t) or is_cancel(t)):
            continue
        vistos.add(t.casefold())
        out.append(t)
    out = out[:MAX_CHIPS]
    return out if len(out) >= 2 else []


def sugerir(pergunta: str, resposta: str | None, cards: list[str]) -> list[str]:
    try:
        from openai import OpenAI
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY", "").strip(),
                        timeout=TIMEOUT_S, max_retries=0)
        r = client.chat.completions.create(
            model=MODELO_CHIPS,
            messages=[{"role": "system", "content": PROMPT_CHIPS},
                      {"role": "user", "content":
                       f"Pergunta: {pergunta}\nResposta: {resposta}\n"
                       f"Cards mostrados: {', '.join(cards) or 'nenhum'}"}],
            response_format=_FORMATO, max_tokens=120, temperature=0.5)
        return validar(json.loads(r.choices[0].message.content)["chips"])
    except Exception as e:
        logger.info("chips indisponíveis: %s", e)
        return []
