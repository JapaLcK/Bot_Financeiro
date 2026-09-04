"""db/checkout_funnel.py — telemetria do funil de checkout (tabela dedicada
checkout_funnel_events, fora do system_event_logs purgável).

Duas pontas: record_checkout_started (no /billing/create-checkout) e
record_checkout_completed (no webhook checkout.session.completed). O
session_id (id da Checkout Session do Stripe) liga a abertura à conclusão
da MESMA tentativa — é o que permite a conversão por sessão no painel.

Falha de gravação nunca propaga: telemetria não pode derrubar o checkout
nem o processamento do webhook.
"""
from __future__ import annotations

import logging

from .connection import get_conn

_log = logging.getLogger(__name__)


def _record(user_id: int, session_id: str | None, kind: str) -> None:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    # Quem impede a linha de CONCLUSÃO duplicada na reentrega
                    # do webhook é a unique parcial de db/schema.py (só
                    # `completed`; o `started` repete de propósito quando o
                    # checkout reaproveita uma sessão). Aqui não há
                    # `on conflict`: existiu um `do nothing` SEM alvo, e ele
                    # (a) não era medido por teste nenhum — removê-lo deixava a
                    # suíte verde, porque o `except` abaixo já dá o mesmo
                    # resultado — e (b) engoliria em silêncio qualquer unique
                    # futura desta tabela. O `except` faz o mesmo trabalho e
                    # deixa rastro no log.
                    "insert into checkout_funnel_events (user_id, session_id, kind) "
                    "values (%s, %s, %s)",
                    (int(user_id), session_id, kind),
                )
            conn.commit()
    except Exception as exc:  # nunca derruba o fluxo principal
        _log.warning("checkout_funnel %s falhou user=%s: %s", kind, user_id, exc)


def record_checkout_started(user_id: int, session_id: str | None) -> None:
    _record(user_id, session_id, "started")


def record_checkout_completed(user_id: int, session_id: str | None) -> None:
    _record(user_id, session_id, "completed")
