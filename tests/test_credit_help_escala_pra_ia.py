"""O help de fatura precisa continuar ESCALANDO pra IA.

O ramo morto que existia no `_build_credit_contextual_help` (bloco `try` com
`user_id` indefinido) devolvia frases sem os markers de
`core.handle_incoming._looks_like_help_fallback` — se ele voltasse a ser
alcançável, o usuário pararia de cair na IA, que é quem tem
`get_total_debt`/`forecast_next_bill`. Este teste é o guarda disso.
"""
from unittest.mock import Mock

from core.handle_incoming import handle_incoming
from core.types import IncomingMessage


def test_fatura_sem_cartao_vai_pra_ia(user_id, monkeypatch):
    monkeypatch.setattr("core.services.plan_service.ai_chat_allowed", lambda _uid: True)
    chat = Mock(return_value="resposta da IA")
    monkeypatch.setattr("core.services.ai_chat.chat", chat)

    msg = IncomingMessage(platform="whatsapp", user_id=user_id, text="como pago minha fatura")
    out = handle_incoming(msg)

    chat.assert_called_once()
    assert out[0].text == "resposta da IA"
