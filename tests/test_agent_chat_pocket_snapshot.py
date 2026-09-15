"""A consulta pura do Banqueiro explicita a data e os limites do saldo guardado."""
from datetime import date

import pytest

import db
from core.services.agent_chat import execute_read
from core.services.ai_chat.tools.pockets import _list_pockets


def pocket(last_interest_date):
    return {"id": 1, "name": "Reserva", "balance": 1000, "target_amount": 1100,
            "target_date": date(2026, 12, 1), "interest_enabled": True,
            "interest_rate": 1, "interest_period": "monthly",
            "last_interest_date": last_interest_date}


@pytest.mark.parametrize("last_interest_date", [date(2026, 1, 1), None])
def test_banqueiro_identifica_saldo_sem_atualizar_juros(monkeypatch, last_interest_date):
    calls = []

    def list_pockets(uid, *, accrue=True):
        calls.append((uid, accrue))
        assert accrue is False
        return [pocket(last_interest_date)]

    monkeypatch.setattr(db, "list_pockets", list_pockets)
    result = execute_read(42, "cofre", "list_pockets", {})
    assert calls == [(42, False)]
    row = result["pockets"][0]
    assert row["last_interest_date"] == last_interest_date
    assert row["balance"] == 1000
    assert row["remaining_to_goal"] == 100
    assert "último saldo registrado" in result["note"]
    assert "sem atualizar juros" in result["note"]
    assert "data de atualização é desconhecida" in result["note"]
    assert "não confirma o saldo nem o atingimento da meta hoje" in result["note"]


def test_chat_geral_preserva_consulta_que_atualiza_juros(monkeypatch):
    calls = []

    def list_pockets(uid):
        calls.append(uid)
        return [pocket(date(2026, 9, 13))]

    monkeypatch.setattr(db, "list_pockets", list_pockets)
    result = _list_pockets(42, {})
    assert calls == [42]
    assert "note" not in result
    assert "last_interest_date" not in result["pockets"][0]
