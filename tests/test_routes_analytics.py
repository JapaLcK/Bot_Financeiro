"""Rotas de frontend/routes/analytics.py (refactor Fase 1, Etapa 2).

Smoke da extração: as 7 rotas continuam registradas e protegidas pela
cadeia de auth do dashboard (agora em frontend/routes/shared.py).
Não toca dados — valida 401 sem token e 403 com token de outro user.
"""

from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard
from token_utils import make_dashboard_token

client = TestClient(dashboard.app)

ANALYTICS_PATHS = [
    "/analytics/{uid}/kpis",
    "/analytics/{uid}/evolution",
    "/analytics/{uid}/categories",
    "/analytics/{uid}/weekday-pattern",
    "/analytics/{uid}/top-merchants",
    "/analytics/{uid}/patterns",
    "/insights/{uid}/current",
]


def test_analytics_sem_token_401():
    for path in ANALYTICS_PATHS:
        resp = client.get(path.format(uid=832398038))
        assert resp.status_code == 401, f"{path}: {resp.status_code}"


def test_analytics_token_de_outro_user_403():
    token = make_dashboard_token(832398038, hours=1)
    for path in ANALYTICS_PATHS:
        resp = client.get(
            path.format(uid=999999999),
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403, f"{path}: {resp.status_code}"
