"""B não vê o que é de A na /api/v2: o usuário vem da sessão, nunca da requisição.

A tem tier `pro`, B tem `essencial`, e a única rota (`/me`) devolve o tier — então
qualquer vazamento aparece como o tier do outro.
"""
import uuid

import pytest

from conftest import promote_to_pro
from db import ensure_user
from test_api_v2_sessao import ME, get_com_cookie, sessao


@pytest.fixture
def a_e_b(monkeypatch, user_id):
    a = user_id
    b = int(uuid.uuid4().int % 10_000_000_000)  # o autouse do conftest apaga depois
    ensure_user(b)
    promote_to_pro(a, plan="pro_max")
    promote_to_pro(b, plan="essencial")
    monkeypatch.setenv("DASHBOARD_V2_BETA_EMAILS", "")
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", f"{a},{b}")
    return a, b


def test_user_id_de_a_na_query_nao_troca_o_usuario_de_b(a_e_b):
    import frontend.finance_bot_websocket_custom as dashboard
    from fastapi.testclient import TestClient

    a, b = a_e_b
    client = TestClient(dashboard.app)
    client.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, sessao(b)["dashboard"])
    r = client.get(ME, params={"user_id": a, "uid": a})
    assert r.status_code == 200, r.text
    assert r.json() == {"plan_tier": "essencial"}


def test_bearer_de_a_com_cookie_de_b_vale_o_bearer(a_e_b):
    """Fixa o comportamento atual de `resolve_dashboard_user_id`: o Bearer vence
    o cookie. Os dois são sessões válidas; não é escalada, é precedência."""
    a, b = a_e_b
    r = get_com_cookie(sessao(b)["dashboard"], Authorization=f"Bearer {sessao(a)['access']}")
    assert r.status_code == 200, r.text
    assert r.json() == {"plan_tier": "pro"}
