"""A prateleira distingue o direito de ativar do direito de conversar."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
from core.services import agent_chat, plan_service
from frontend.routes import agents


@pytest.mark.parametrize('active', [False, True])
@pytest.mark.parametrize('v2,budget,pro,allowed', [
    (False, 0, False, False),
    (False, 0, True, True),
    (True, 0, False, False),
    (True, 14, False, True),
])
def test_prateleira_e_chat_compartilham_gate_do_plano(monkeypatch, active, v2, budget, pro, allowed):
    monkeypatch.setattr(agents.shared, 'authorize_dashboard_access', lambda *a: None)
    monkeypatch.setattr(agents, '_require_agents_beta', lambda *a: None)
    monkeypatch.setattr(plan_service, 'plans_v2_enabled', lambda: v2)
    monkeypatch.setattr(plan_service, 'agents_energy_budget', lambda uid: budget)
    monkeypatch.setattr(plan_service, 'is_pro', lambda uid: pro)
    monkeypatch.setattr(agents, '_plan_allows_multiple', lambda uid: pro)
    monkeypatch.setattr(db, 'agents_summary', lambda uid: {})
    monkeypatch.setattr(db, 'list_agents', lambda uid: [{'kind': 'detetive', 'status': 'active'}] if active else [])
    app = FastAPI()
    app.include_router(agents.router)
    with TestClient(app) as client:
        response = client.get('/agents/42')
    assert response.status_code == 200
    shelf = response.json()
    assert shelf['can_chat'] is allowed
    assert agent_chat.access_state(42, 'detetive') == (
        ('ready' if active else 'activate') if allowed else 'upgrade'
    )
    if not v2:
        assert shelf['can_activate'] is True, 'o direito legado de ativar um agente continua independente do chat'
