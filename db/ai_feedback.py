"""db/ai_feedback.py — nota 👍/👎 do usuário numa resposta do chat IA (`ai_messages.feedback`)."""
from __future__ import annotations

from typing import Optional

from .connection import get_conn


def set_feedback(user_id: int, message_id: int, nota: Optional[int]) -> bool:
    """Grava (ou limpa, com None) a nota 👍/👎 numa resposta DO USUÁRIO. False
    quando a linha não existe, é de outro usuário ou não é `assistant`: o chamador
    não distingue os três casos."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "update ai_messages set feedback = %s"
            " where id = %s and user_id = %s and role = 'assistant'",
            (nota, int(message_id), int(user_id)),
        )
        ok = cur.rowcount == 1
        conn.commit()
        return ok
