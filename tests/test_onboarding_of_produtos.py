"""O wizard (/onboarding, passo 2) diz o que o Open Finance lê; isso é texto de
consentimento e tem de ser o que o connect token pede à Pluggy (§0.7).

A lista sai do servidor (`of_produtos` em GET /onboarding/state) pela mesma
`pluggy_products()` que `create_pluggy_connect_token` usa — então vale também
quando `PLUGGY_PRODUCTS` foge do default. O cliente (comecar.js) só a desenha;
tests/frontend/onboarding_guia.test.mjs cobre o desenho.
"""
import pytest
from fastapi.testclient import TestClient

import core.services.pluggy as pl
import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.onboarding as onboarding_routes

client = TestClient(dashboard.app)

DEFAULT = ["ACCOUNTS", "TRANSACTIONS", "CREDIT_CARDS", "INVESTMENTS"]
CONFIGS = [(None, DEFAULT), ("ACCOUNTS,TRANSACTIONS", ["ACCOUNTS", "TRANSACTIONS"])]


def _set_env(monkeypatch, env):
    if env is None:
        monkeypatch.delenv("PLUGGY_PRODUCTS", raising=False)
    else:
        monkeypatch.setenv("PLUGGY_PRODUCTS", env)


def _produtos_da_rota(monkeypatch):
    monkeypatch.setattr(onboarding_routes.shared, "resolve_dashboard_user_id", lambda req: 7)
    monkeypatch.setattr(onboarding_routes, "get_onboarding_state",
                        lambda uid: {"step": 2, "completed": False})
    resp = client.get("/onboarding/state")
    assert resp.status_code == 200
    return resp.json()["of_produtos"]


def _produtos_do_connect_token(monkeypatch):
    """O que `create_pluggy_connect_token` manda à Pluggy, com o HTTP simulado na borda."""
    captured = {}

    def fake_post(self, url, headers=None, json=None):  # noqa: A002
        captured["products"] = json["options"]["products"]

        class _R:
            status_code = 200

            def json(self_inner):
                return {"accessToken": "tok"}

        return _R()

    monkeypatch.setattr(pl, "create_pluggy_api_key", lambda: "k")
    monkeypatch.setattr(pl, "_raise_for_pluggy_response", lambda resp, msg: None)
    monkeypatch.setattr(pl.httpx.Client, "post", fake_post)
    pl.create_pluggy_connect_token(1, None)
    return captured["products"]


@pytest.mark.parametrize("env,esperado", CONFIGS)
def test_rota_entrega_os_produtos_efetivos(monkeypatch, env, esperado):
    _set_env(monkeypatch, env)
    assert _produtos_da_rota(monkeypatch) == esperado


@pytest.mark.parametrize("env,_esperado", CONFIGS)
def test_rota_e_connect_token_leem_a_mesma_fonte(monkeypatch, env, _esperado):
    _set_env(monkeypatch, env)
    assert _produtos_da_rota(monkeypatch) == _produtos_do_connect_token(monkeypatch)
