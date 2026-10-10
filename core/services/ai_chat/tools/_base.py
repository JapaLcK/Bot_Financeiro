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
  - summary: SÓ pra writes que precisam (ou podem precisar) de confirmação —
             recebe args e devolve descrição em pt-BR usada no template 3.
  - confirmar_se: SÓ pra writes com requires_confirmation=False. Recebe
             (user_id, args); True = desta vez vira pending action como as de
             confirmação, com resposta fixa do runner. None = sempre executa.

Cada arquivo em tools/ exporta uma lista `TOOLS: list[Tool]`. O `__init__.py`
do pacote tools/ agrega todas e expõe utilitários (schemas, lookup por nome).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional, Union


# Pendência viva de outro pedido: a nova NÃO foi armada (ver
# `db.ai_chat.set_pending_action`). Texto único do runner e do `set_budget`.
OUTRO_PEDIDO = ("🐷 Tem outro pedido seu esperando confirmação. Responde ele "
                "primeiro e depois me manda este de novo.")


@dataclass(frozen=True)
class Tool:
    schema: dict[str, Any]
    is_write: bool
    execute: Callable[[int, dict[str, Any]], Any]
    summary: Optional[Callable[[dict[str, Any]], str]] = None
    requires_confirmation: bool = True
    validate: Optional[Callable[[int, dict[str, Any]], Optional[str]]] = None
    """Pre-check opcional pra writes com confirmação. Recebe (user_id, args)
    e retorna msg de erro (string) se algo já está obviamente inválido —
    runner pula a pending_action e mostra a msg direto pro user. Retornar
    None = OK, segue pro fluxo normal de confirmação.

    Caso de uso principal: evitar pedir confirmação pra apagar/editar um
    ID que nem existe (LLM pode ter inventado). Sem validate, o user
    confirma achando que era real e só depois vê o 'não achei'."""

    confirmar_se: Optional[Callable[[int, dict[str, Any]], bool]] = None
    arma_pendencia_no_execute: Union[bool, Callable[[int, dict[str, Any]], bool]] = False
    """Write SEM `requires_confirmation` cujo execute pode, por conta própria,
    armar uma pergunta pendente (ai_pending ou `pending_actions`) e devolvê-la
    como resposta. O runner não a vê armar; na pré-varredura da rodada ela
    conta como escrita que arma pendência (não roda junto de outra escrita).
    Bool = sempre/nunca; callable (user_id, args) = só quando aquela chamada
    armaria."""
    ao_confirmar: Optional[Callable[[int, dict[str, Any]], dict[str, Any]]] = None
    """Só com `confirmar_se`: ajusta os args que vão para a pendência (e para
    o resumo) — o que o "sim" grava é o que a pergunta mostrou."""

    has_side_effects: bool = False
    """Consulta que sincroniza ou aplica juros. Não é comando de escrita,
    mas uma falha após sua tentativa não pode restituir a reserva da cota."""

    @property
    def name(self) -> str:
        return self.schema["function"]["name"]
