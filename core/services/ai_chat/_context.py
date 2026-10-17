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

CARDS_DO_APP: contextvars.ContextVar[tuple[str, ...]] = contextvars.ContextVar(
    "ai_chat_cards_do_app", default=()
)
"""Ids de card que o app SABE mostrar (`cards` do corpo de `/ai/chat/stream`). Os que saem da própria tool
(`cards.DA_TOOL`) só são emitidos se estiverem aqui: o build antigo não manda o campo, descartaria o bloco calado e o
dado sumiria. Setada por `streaming.executar`; vazio fora dele."""

FALHAS_DE_CARD_DO_TURNO: contextvars.ContextVar[list[str] | None] = contextvars.ContextVar(
    "ai_chat_falhas_de_card_do_turno", default=None
)
"""Ids dos cards cujo `show_card` NÃO saiu neste turno por plano, onboarding ou leitura (`montar`).
A tool que recusaria mandando a ESSE card devolve o dado (`_recusa_se_for_do_card`): ele já mostrou que não
resolve. Falha de outro card não libera. Setada pelo runner; None fora dele."""

JANELAS_DO_TURNO: contextvars.ContextVar[list[dict] | None] = contextvars.ContextVar(
    "ai_chat_janelas_do_turno", default=None
)
"""Janelas que `_recusa_se_for_do_card` mandou ao `show_card periodo` neste turno: `{"de", "ate", "extra"}` (AAAA-MM
e o `extra` de `_limita_periodo`, com os cortes). O `show_card` usa a janela da recusa quando o modelo diverge dela e
ela foi cortada por 12 meses (`mostrar_card._da_recusa`). Setada pelo runner; None fora dele."""

CATEGORIAS_NAO_ACHADAS_DO_TURNO: contextvars.ContextVar[list[str] | None] = contextvars.ContextVar(
    "ai_chat_categorias_nao_achadas_do_turno", default=None
)
"""Categorias que o `show_card` recebeu neste turno e o card NÃO achou (`categoria_pedida.encontrada=false`). O
`get_category_spend` dessa categoria devolve o dado em vez de mandar a um 2º card (`_recusa_se_for_do_card`). Setada
pelo runner; None fora dele."""

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
