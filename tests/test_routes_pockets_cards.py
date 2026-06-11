"""Rotas de frontend/routes/{pockets,cards}.py (refactor Fase 1, Etapa 5).

Smoke da extração: as 21 rotas movidas continuam registradas e protegidas
pela cadeia de auth do dashboard. Não toca dados — 401 sem token.
(test_pockets_endpoints.py cobre o comportamento de pockets com HTTP real.)
"""

from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard

client = TestClient(dashboard.app)

UID = 832398038

PROTECTED_GETS = [
    f"/goals/{UID}/status",
    f"/pockets/{UID}/teste/history",
    f"/cards/{UID}/summary",
    f"/installments/{UID}/list",
    f"/bills/{UID}",
]


def test_rotas_movidas_exigem_token():
    for path in PROTECTED_GETS:
        resp = client.get(path)
        assert resp.status_code == 401, f"{path}: {resp.status_code}"


def test_paths_registrados_no_app():
    paths = {r.path for r in dashboard.app.routes}
    expected = {
        "/pockets/{user_id}",
        "/pockets/{user_id}/{pocket_id}/meta",
        "/goals/{user_id}/status",
        "/pockets/{user_id}/{pocket_name:path}",
        "/pockets/{user_id}/{pocket_name:path}/deposit",
        "/pockets/{user_id}/{pocket_name:path}/withdraw",
        "/pockets/{user_id}/{pocket_name:path}/history",
        "/cards/{user_id}",
        "/cards/{user_id}/summary",
        "/cards/{user_id}/reorder",
        "/cards/{user_id}/{card_id}",
        "/cards/{user_id}/{card_id}/delete-impact",
        "/installments/{user_id}/list",
        "/installments/{user_id}/{group_id}/delete-impact",
        "/installments/{user_id}/{group_id}/anticipate",
        "/installments/{user_id}/{group_id}",
        "/bills/{user_id}",
        "/bills/{user_id}/{bill_id}",
        "/bills/{user_id}/{bill_id}/pay",
    }
    missing = expected - paths
    assert not missing, f"rotas sumiram do app: {missing}"
