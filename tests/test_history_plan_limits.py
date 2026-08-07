from __future__ import annotations

import asyncio
from datetime import date

import db
import frontend.finance_bot_websocket_custom as dashboard
from core.services import plan_service


def test_history_list_clampa_data_inicial_do_plano(monkeypatch):
    captured = {}
    monkeypatch.setattr(dashboard, "_authorize_dashboard_access", lambda *_: None)
    monkeypatch.setattr(plan_service, "history_earliest_date", lambda _uid: date(2026, 8, 1))

    def fake_list_history(user_id, from_date, to_date, *args):
        captured["from"] = from_date
        captured["to"] = to_date
        return {"items": [], "total": 0, "page": 1, "limit": 50, "total_pages": 0}

    monkeypatch.setattr(db, "list_history", fake_list_history)

    asyncio.run(dashboard.history_list_route(
        request=None,
        user_id=123,
        from_="2020-01-01",
        to="2026-09-01",
        categoria=None,
        tipo=None,
        q=None,
        uncategorized=False,
        refunds_only=False,
        page=1,
        limit=50,
    ))

    assert captured == {"from": date(2026, 8, 1), "to": date(2026, 9, 1)}


def test_daily_free_usa_inicio_exato_do_mes(monkeypatch):
    captured = {}
    monkeypatch.setattr(dashboard, "_authorize_dashboard_access", lambda *_: None)
    first_day = date.today().replace(day=1)
    monkeypatch.setattr(plan_service, "history_earliest_date", lambda _uid: first_day)

    async def fake_daily(user_id, days, start_date=None):
        captured["start"] = start_date
        captured["days"] = days
        return []

    monkeypatch.setattr(dashboard, "get_daily_expenses_window", fake_daily)

    result = asyncio.run(dashboard.daily_expenses_window(
        request=None, user_id=123, days=90))

    assert captured["start"] == first_day
    assert result["data"] == []
