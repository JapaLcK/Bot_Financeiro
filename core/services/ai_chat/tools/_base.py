"""
core/services/ai_chat/tools/_base.py — modelo declarativo de tool da IA.

Toda tool exposta ao LLM é representada por um objeto `Tool` com:
  - schema: definição no formato OpenAI function calling (passada em tools=[]
            do chat.completions.create).
  - is_write: True se a tool muda estado (escreve no DB). Default False.
  - requires_confirmation: SÓ relevante quando is_write=True.
      * True (default)  → vira pending action; user confirma com "sim"/"não".
      * False           → executa direto; a mensagem retornada vai pro user
                          como resposta final (sem 2º round-trip com LLM).
      Use False só pra ações reversíveis e de baixo risco (ex: add_launch,
      cujo recovery é desfazer ou trocar categoria depois).
  - execute: handler (user_id, args) → :
               * dict (read tool) → JSON, devolvido pro LLM
               * str  (write tool) → mensagem final pro user
  - summary: SÓ pra writes que precisam de confirmação — recebe args e devolve
             descrição em pt-BR usada no template 3.

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
    requires_confirmation: bool = True

    @property
    def name(self) -> str:
        return self.schema["function"]["name"]
