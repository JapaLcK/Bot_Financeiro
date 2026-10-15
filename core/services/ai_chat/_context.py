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

CARDS_DO_TURNO: contextvars.ContextVar[list[str] | None] = contextvars.ContextVar(
    "ai_chat_cards_do_turno", default=None
)
"""Cards que o `show_card` já MOSTROU neste turno (emitiu bloco; chamada recusada não
entra). O teto vale por turno. Setada pelo runner; None fora dele = sem teto."""

FALHAS_DE_CARD_DO_TURNO: contextvars.ContextVar[list[str] | None] = contextvars.ContextVar(
    "ai_chat_falhas_de_card_do_turno", default=None
)
"""Ids dos cards cujo `show_card` NÃO saiu neste turno por plano, onboarding ou leitura (`montar`).
A tool que recusaria mandando a ESSE card devolve o dado (`_recusa_se_for_do_card`): ele já mostrou que não
resolve. Falha de outro card não libera. Setada pelo runner; None fora dele."""

METAS_DO_TURNO: contextvars.ContextVar[bool | None] = contextvars.ContextVar(
    "ai_chat_metas_do_turno", default=None
)
"""A chave das metas (`metas_conversa.metas_ligadas`), lida UMA vez por turno pelo runner: prompt,
`tools=`, tools e cards de todas as voltas concordam (a leitura decifra o e-mail e grava
`pii_access_log` a cada falha de cache). None = fora do `chat`: `metas_do_turno` lê na hora."""


def metas_do_turno(uid: int) -> bool:
    from core.services import metas_conversa
    v = METAS_DO_TURNO.get()
    return v if v is not None else metas_conversa.metas_ligadas(uid)
