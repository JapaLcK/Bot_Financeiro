"""
core/services/ai_chat/tools/_base.py — modelo declarativo de tool da IA.

Toda tool exposta ao LLM é representada por um objeto `Tool` com:
  - schema: definição no formato OpenAI function calling (passada em tools=[]
            do chat.completions.create).
  - is_write: se True, a invocação não é executada direto; vira pending action
              e o user precisa confirmar com "sim"/"não".
  - execute: handler que recebe (user_id, args) e retorna:
               * dict (read tool) → serializado como JSON e devolvido pro LLM
               * str  (write tool) → mensagem final que vai pro user após confirm
  - summary: SÓ pra write tools — recebe args e devolve descrição em pt-BR
             ("criar regra X → Y") usada no template 3 de confirmação.

Cada arquivo em tools/ exporta uma lista `TOOLS: list[Tool]`. O `__init__.py`
do pacote tools/ agrega todas e expõe utilitários (schemas, lookup por nome).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class Tool:
    schema: dict[str, Any]
    is_write: bool
    execute: Callable[[int, dict[str, Any]], Any]
    summary: Optional[Callable[[dict[str, Any]], str]] = None

    @property
    def name(self) -> str:
        return self.schema["function"]["name"]
