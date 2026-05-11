"""
core/services/ai_chat — Chat conversacional com IA (Piggy).

Pacote organizado por responsabilidade:

  runner.py        — orquestração: chat(), loop de function calling, dispatch
  system_prompt.py — string do system prompt (10 templates + regras)
  confirmations.py — detecção "sim"/"não" pra pending actions
  history.py       — saneamento do histórico antes da OpenAI
  tools/           — registro de tools (uma por domínio)
                     ├── _base.py       dataclass Tool
                     ├── __init__.py    agrega TOOLS + expõe SCHEMAS/WRITE_TOOL_NAMES
                     └── categories.py  tools de categorização

Public API: `chat(user_id, user_text, *, monthly_limit=1000) -> str`
"""
from .runner import chat

__all__ = ["chat"]
