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
