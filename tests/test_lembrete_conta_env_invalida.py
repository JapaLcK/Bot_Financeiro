"""#646 — WA_BILL_REMINDER_HOUR / _DAYS_BEFORE inválidas não calam o lembrete de contas.

Controle negativo: voltar `int(os.getenv(...))` cru no `_bill_reminder_tick` deixa
vermelhos os casos de hora ("nove" levanta ValueError; "-1" manda às 3h; "25"
nunca manda) e o de days_before. Positivo: hora válida "7" manda às 7h.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from adapters.whatsapp import wa_app
from core.services import recurring_charger


@pytest.fixture
def tick(monkeypatch):
    """Roda o tick às `hora`; devolve os `days_before` com que o banco foi consultado."""
    chamadas: list[int] = []

    def rodar(hora: int) -> list[int]:
        chamadas.clear()
        monkeypatch.setattr(wa_app, "_bill_reminder_template_config", lambda: {"name": "x", "language_code": "pt_BR"})
        monkeypatch.setattr(wa_app, "now_tz", lambda: datetime(2026, 10, 1, hora, 0))
        import db.bills as bills

        monkeypatch.setattr(bills, "list_users_with_pending_bills", lambda: [1])
        monkeypatch.setattr(bills, "list_due_bill_reminders", lambda uid, today, days_before: chamadas.append(days_before) or [])
        wa_app._bill_reminder_tick()
        return list(chamadas)

    return rodar


@pytest.mark.parametrize("hora_env", ["nove", "-1", "25"])
def test_hora_invalida_vale_o_padrao_9(tick, monkeypatch, hora_env):
    monkeypatch.setenv("WA_BILL_REMINDER_HOUR", hora_env)
    assert tick(8) == []  # antes das 9: não manda (-1 mandaria; "nove" estouraria)
    assert tick(9) == [3]  # às 9: manda (25 nunca mandaria)


def test_hora_valida_continua_valendo(tick, monkeypatch):
    monkeypatch.setenv("WA_BILL_REMINDER_HOUR", "7")
    assert tick(6) == []
    assert tick(7) == [3]


def test_days_before_invalido_vale_o_padrao_3(tick, monkeypatch):
    monkeypatch.setenv("WA_BILL_REMINDER_DAYS_BEFORE", "tres")
    assert tick(9) == [3]


def test_a_regra_da_hora_tem_uma_fonte(monkeypatch):
    monkeypatch.setenv("WA_BILL_REMINDER_HOUR", "25")
    assert recurring_charger.bill_reminder_hour() == 9
