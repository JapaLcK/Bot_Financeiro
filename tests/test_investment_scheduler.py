import sys
from types import SimpleNamespace

from core.services import investment_scheduler


def test_accrue_all_users_investments_atualiza_todos_os_usuarios(monkeypatch):
    calls = []
    fake_db = SimpleNamespace(
        list_users_with_unfrozen_interest=lambda: [10, 20],
        accrue_all_investments=lambda user_id: calls.append(user_id),
        accrue_all_pockets=lambda user_id: calls.append(-user_id),
    )
    monkeypatch.setitem(sys.modules, "db", fake_db)

    result = investment_scheduler.accrue_all_users_investments()

    assert calls == [10, -10, 20, -20]
    assert result == {"users": 2, "updated": 2, "failed": 0}


def test_accrue_all_users_investments_continua_apos_erro(monkeypatch):
    calls = []

    def accrue(user_id):
        calls.append(user_id)
        if user_id == 10:
            raise RuntimeError("boom")

    fake_db = SimpleNamespace(
        list_users_with_unfrozen_interest=lambda: [10, 20],
        accrue_all_investments=accrue,
        accrue_all_pockets=lambda user_id: calls.append(-user_id),
    )
    monkeypatch.setitem(sys.modules, "db", fake_db)

    result = investment_scheduler.accrue_all_users_investments()

    # A falha nos investimentos do 10 não pula as caixinhas dele (-10).
    assert calls == [10, -10, 20, -20]
    assert result == {"users": 2, "updated": 1, "failed": 1}
