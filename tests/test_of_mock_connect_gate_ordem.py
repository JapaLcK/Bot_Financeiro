"""G7 — mock-connect ligado: o corpo só é lido depois da sessão e do teto.

Ler o corpo antes de `authorize_dashboard_access` deixa qualquer anônimo mandar
corpo grande para a memória do servidor (DoS sem login). Aqui `Request.body`,
`.json` e `.form` explodem: se a rota lê o corpo antes de recusar, o teste cai.

CONTROLES (§3), medidos por mutação na rota:
  * negativo — mover `request.body()` para antes do `authorize_dashboard_access`
    deixa `test_anonimo_*` vermelho; para entre ele e o `_enforce_bank_limit`,
    deixa `test_carencia_*` vermelho.
  * positivo — `test_valido_le_o_corpo` (sem a explosão): o corpo segue lido.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from conftest import em_carencia, promote_to_pro
from db import ensure_user
from test_of_mock_connect_gate import _JSON, _auth, _conexoes, _explode, _switch, _url, dashboard

_ITAU = b'{"institution": "itau"}'


def _ligado_sem_corpo(monkeypatch):
    _switch(monkeypatch, "1")
    for metodo in ("body", "json", "form"):
        monkeypatch.setattr(Request, metodo, _explode)


@pytest.mark.parametrize("corpo", [b"x" * 1024, b"\xff" * (1024 * 1024 + 1)], ids=["1KB", "1MB+1"])
def test_anonimo_401_sem_ler_o_corpo(user_id, monkeypatch, corpo):
    _ligado_sem_corpo(monkeypatch)
    r = TestClient(dashboard.app).post(_url(user_id), content=corpo, headers=_JSON)
    assert r.status_code == 401, r.text
    assert _conexoes(user_id) == 0


def test_outro_uid_403_sem_ler_o_corpo(user_id, monkeypatch):
    outro = int(uuid.uuid4().int % 10_000_000_000)
    ensure_user(outro)
    promote_to_pro(outro, "essencial")
    _ligado_sem_corpo(monkeypatch)
    client = TestClient(dashboard.app)
    r = client.post(_url(outro), content=_ITAU, headers={**_auth(client, user_id), **_JSON})
    assert r.status_code == 403, r.text
    assert _conexoes(outro) == 0


def test_carencia_402_sem_ler_o_corpo(user_id, monkeypatch):
    em_carencia(user_id)
    _ligado_sem_corpo(monkeypatch)
    client = TestClient(dashboard.app)
    r = client.post(_url(user_id), content=_ITAU, headers={**_auth(client, user_id), **_JSON})
    assert r.status_code == 402, r.text
    assert _conexoes(user_id) == 0


def test_valido_le_o_corpo(user_id, monkeypatch):
    promote_to_pro(user_id, "essencial")
    _switch(monkeypatch, "1")
    client = TestClient(dashboard.app)
    r = client.post(_url(user_id), content=_ITAU, headers={**_auth(client, user_id), **_JSON})
    assert r.status_code == 200, r.text
    assert [c["institution_name"] for c in r.json()["connections"]] == ["Itaú"]
