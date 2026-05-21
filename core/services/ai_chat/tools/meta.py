"""
core/services/ai_chat/tools/meta.py — tools meta (telemetria, fallback).

`report_out_of_scope` é a tool que a IA chama quando reconhece que a pergunta
é DE FINANÇAS mas ela não tem ferramenta adequada pra responder (ex: "gasto
por dia da semana", "projeção em 6 meses"). Auto-execute: grava em
`ai_fallback_log` e retorna a mensagem padrão direto pro user.

Diferente do template 6 (off-topic, "capital da França") — esse é pra
in-scope sem cobertura. A telemetria alimenta a decisão de quais tools
criar no futuro.
"""
from __future__ import annotations

import logging
from typing import Any

import db

from core.handlers.dashboard import open_dashboard as _open_dashboard_link

from .._context import CURRENT_USER_MESSAGE
from ._base import Tool


logger = logging.getLogger(__name__)


_FALLBACK_BASE = (
    "🐷 Isso tá além do que consigo te dar aqui."
)

_FALLBACK_ALTERNATIVES = (
    "Posso te ajudar com:\n"
    "• Registrar gasto/receita ('gastei 50 mercado')\n"
    "• Ver saldo e maiores gastos do mês\n"
    "• Top categorias / últimos lançamentos\n"
    "• Apagar/editar lançamentos\n"
    "• Faturas e limite de cartão\n\n"
    "Pra análises mais profundas, dá uma olhada no dashboard: https://pigbankai.com/app"
)


def _report_out_of_scope_execute(user_id: int, args: dict[str, Any]) -> str:
    reason = (args.get("reason") or "").strip() or None
    question = CURRENT_USER_MESSAGE.get() or "(pergunta não capturada)"

    try:
        db.log_ai_fallback(user_id, question, reason)
    except Exception as e:
        # Telemetria silenciosa — não pode quebrar a resposta pro user.
        logger.warning("falha ao logar fallback: %s", e)

    return f"{_FALLBACK_BASE}\n\n{_FALLBACK_ALTERNATIVES}"


def _open_dashboard_execute(user_id: int, args: dict[str, Any]) -> str:
    return _open_dashboard_link(user_id)


TOOLS: list[Tool] = [
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "open_dashboard",
                "description": (
                    "Devolve o LINK autenticado do dashboard web do user. "
                    "USE sempre que o user pedir o dashboard / painel / link "
                    "do dashboard (ex: 'dashboard', 'manda o painel', 'abre "
                    "o dashboard', 'link do dashboard', 'painel', 'web'). "
                    "NÃO liste os dados em texto — quem decide o que ver é "
                    "o user no navegador. A tool já retorna a resposta "
                    "pronta com o link, expiração e instruções."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        },
        is_write=True,
        requires_confirmation=False,
        execute=_open_dashboard_execute,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "report_out_of_scope",
                "description": (
                    "USE SÓ quando a pergunta do user é claramente de "
                    "finanças pessoais MAS você não tem ferramenta adequada "
                    "pra responder (ex: 'gasto por dia da semana', "
                    "'projeção de 6 meses', 'análise de tendência ano a "
                    "ano', 'qual padrão de consumo no fim de semana'). "
                    "Auto-executa: registra a pergunta pra análise e "
                    "responde ao user com mensagem padrão sugerindo o "
                    "dashboard. NÃO use pra off-topic (capital da França, "
                    "lição de casa) — pra esses, use o template 6 normal. "
                    "Antes de chamar, tenha CERTEZA que tentou outras "
                    "tools e nenhuma serve."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reason": {
                            "type": "string",
                            "description": (
                                "Categoria/motivo curto pra agrupar nos "
                                "logs. Ex: 'projeção temporal', 'análise "
                                "comparativa complexa', 'breakdown por dia "
                                "da semana', 'tendência multi-ano'."
                            ),
                        },
                    },
                    "required": ["reason"],
                },
            },
        },
        is_write=True,
        requires_confirmation=False,
        execute=_report_out_of_scope_execute,
    ),
]
