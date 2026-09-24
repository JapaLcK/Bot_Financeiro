"""Porta 4 da pergunta comparativa: botão "Já paguei" (`bill_pay_amount`).

"gastei mais em 2025 ou 2026?" pagava a conta com R$ 2.025 e debitava o saldo.
Irmão de `test_multi_pergunta_comparativa_fila.py` (portas 2 e 3).
"""
from __future__ import annotations

import pytest

from tests.test_multi_pergunta_comparativa_fila import _PERGUNTAS

# ── Porta 4: botão "Já paguei", pergunta `bill_pay_amount` ────────────────────

@pytest.fixture
def porta4(monkeypatch):
    import adapters.whatsapp.wa_runtime as wr
    import db.bills as B
    from adapters.whatsapp.wa_parse import InboundMessage

    respostas, pagos = [], []
    pend = {"action_type": "bill_pay_amount", "payload": {"bill_id": 7, "name": "luz"}}
    for nome, f in {
        "get_or_create_canonical_user": lambda p, e: 5,
        "attempt_whatsapp_phone_link":
            lambda wa_id, current_user_id=None: {"status": "already_linked", "user_id": 5},
        "log_system_event_sync": lambda *a, **k: None,
        "send_typing_indicator": lambda *a, **k: None,
        "_seen_recent": lambda m: False,
        "_send_reply": lambda to, body: respostas.append(body),
        "_send_reply_with_optional_buttons": lambda to, body, user_id=None: respostas.append(body),
        "get_pending_action": lambda u: pend,
        "_bloqueado_pelo_corte": lambda *a: False,
        "consume_pending_action": lambda u, p: True,
    }.items():
        monkeypatch.setattr(wr, nome, f)
    monkeypatch.setattr(B, "mark_bill_paid",
                        lambda u, b, a: pagos.append(a) or {"name": "luz", "paid_amount": a})

    def manda(texto):
        wr.process_message(InboundMessage(wa_id="5511999998888", text=texto, timestamp="2",
                                          attachments=[], raw={"id": "w", "type": "text"}))
        return respostas, pagos
    return manda


@pytest.mark.parametrize("text", _PERGUNTAS)
def test_ja_paguei_recusa_pergunta_comparativa(porta4, text):
    respostas, pagos = porta4(text)
    assert pagos == []
    assert respostas == ["Isso parece uma pergunta, não o valor da conta de *luz*. "
                         "Manda só o número. Ex: *132,50* (ou *cancelar*)"]


def test_ja_paguei_valor_legitimo_ainda_paga(porta4):
    respostas, pagos = porta4("132")
    assert pagos == [132.0] and "Conta paga" in respostas[-1], respostas
