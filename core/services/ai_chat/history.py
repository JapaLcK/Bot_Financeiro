"""
core/services/ai_chat/history.py — saneamento do histórico antes de mandar pra OpenAI.

A sliding window pode cortar o histórico no meio de uma sequência
assistant.tool_calls → tool response, gerando mensagens órfãs que a API rejeita.
Este módulo remove esses casos defensivamente.
"""
from __future__ import annotations

from typing import Any


def trim_history_for_openai(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Garante que o histórico carregado do DB respeite as regras da API:

    - Cada `tool` message deve ter um `tool_call_id` que aparece num
      `assistant.tool_calls` anterior. Se a sliding window cortou antes do
      assistant, a `tool` órfã no começo é removida.
    - Se a última mensagem é um `assistant` com `tool_calls` mas sem respostas
      de `tool` em seguida, removemos esse trailing também (senão a API recusa).
    """
    if not history:
        return history

    seen_tool_call_ids: set[str] = set()
    cleaned: list[dict[str, Any]] = []
    for msg in history:
        if msg.get("role") == "tool":
            tcid = msg.get("tool_call_id")
            if tcid in seen_tool_call_ids:
                cleaned.append(msg)
            # senão dropa (órfã)
        else:
            cleaned.append(msg)
            if msg.get("role") == "assistant":
                for tc in (msg.get("tool_calls") or []):
                    if isinstance(tc, dict) and tc.get("id"):
                        seen_tool_call_ids.add(tc["id"])

    while cleaned:
        last = cleaned[-1]
        if last.get("role") == "assistant" and last.get("tool_calls"):
            cleaned.pop()
            continue
        break

    return cleaned
