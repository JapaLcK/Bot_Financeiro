"""
core/services/ai_chat/_context.py — contextvar com a plataforma do chat atual.

Setado pelo `runner.chat()` no início de cada turno e lido pelas tools que
precisam adaptar comportamento por canal (ex: `add_launch` aciona o botão
de "trocar categoria" só quando o canal renderiza buttons — WhatsApp).

Default 'dashboard' porque é o canal mais conservador (sem buttons), evita
side-effects acidentais se alguém chamar `_add_launch_execute` direto.
"""
from __future__ import annotations

import contextvars

CURRENT_PLATFORM: contextvars.ContextVar[str] = contextvars.ContextVar(
    "ai_chat_platform", default="dashboard"
)

CURRENT_USER_MESSAGE: contextvars.ContextVar[str] = contextvars.ContextVar(
    "ai_chat_user_message", default=""
)
"""Mensagem do user no turno atual. Setada pelo runner e lida por tools que
precisam logar/registrar a pergunta original (ex: `report_out_of_scope`)."""
