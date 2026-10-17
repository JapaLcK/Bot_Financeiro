"""db/ai_feedback.py — nota 👍/👎 do usuário numa resposta do chat IA (`ai_messages.feedback`),
com o motivo do 👎 (`feedback_motivo`) e o comentário do "Outro" (`feedback_texto`)."""
from __future__ import annotations

from typing import Optional

from .connection import get_conn

# Fonte única dos motivos (chave -> rótulo). A borda (`FeedbackBody`) deriva o Literal daqui.
MOTIVOS = {
    "numero_errado": "Número errado",
    "nao_entendeu": "Não entendeu",
    "faltou_card": "Faltou card",
    "outro": "Outro",
}


def set_feedback(user_id: int, message_id: int, nota: Optional[int],
                 motivo: Optional[str] = None, texto: Optional[str] = None) -> bool:
    """Grava o ESTADO INTEIRO do feedback numa resposta DO USUÁRIO (nota, motivo e
    texto juntos): gravar 👍, None ou 👎 sem motivo zera motivo e texto, então quem
    chama só precisa passar o que quer ver gravado. False quando a linha não
    existe, é de outro usuário ou não é `assistant`: o chamador não distingue os três."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "update ai_messages set feedback = %s, feedback_motivo = %s, feedback_texto = %s"
            " where id = %s and user_id = %s and role = 'assistant'",
            (nota, motivo, texto, int(message_id), int(user_id)),
        )
        ok = cur.rowcount == 1
        conn.commit()
        return ok
