"""Origem de tentativa OAuth: UUID canônico no path da URI montada pelo servidor.

O transporte Pluggy é o mesmo dublê dos contratos legados. A prova discrimina
ignorar o nonce, aceitá-lo cru, aceitar duplicatas e descartar corpo grande.
"""
import asyncio
import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.open_finance as of_routes
from conftest import promote_to_pro
from core.services import plan_service
from test_of_connect_token_reconectar import _semeia, remoto  # noqa: F401
from test_of_connect_token_volta_app import _options, _post, _req, pluggy_dublada  # noqa: F401
from test_of_item_ownership import _auth

ATTEMPT = "bcf9e875-7197-4e10-8bc2-92e34c04d965"


@pytest.mark.parametrize("scheme", ["pigbank", "pigbank-staging", "pigbank-dev"])
def test_attempt_vai_no_path_oficial_sem_url_livre(user_id, pluggy_dublada, remoto, scheme):
    promote_to_pro(user_id)
    item = _semeia(user_id, f"attempt-{user_id}")
    remoto["dono"] = user_id
    client = TestClient(dashboard.app)
    corpo = json.dumps({"app_scheme": scheme, "attempt_id": ATTEMPT, "item_id": item,
                        "oauthRedirectUri": "https://outro.test/roubar"})
    r = _post(client, user_id, _auth(client, user_id), corpo)
    assert r.status_code == 200, r.text
    payload = json.loads(pluggy_dublada.corpos[0])
    assert payload["itemId"] == item
    assert payload["options"]["oauthRedirectUri"] == f"{scheme}://open-finance-volta/{ATTEMPT}"
    assert "attempt_id" not in payload and "attempt_id" not in payload["options"]
    assert ATTEMPT not in repr(pluggy_dublada.registros)


@pytest.mark.parametrize("attempt", [
    None, "", 123, True, [], {}, ATTEMPT.upper(), ATTEMPT.replace("-", ""),
    "{" + ATTEMPT + "}", "urn:uuid:" + ATTEMPT, " " + ATTEMPT, ATTEMPT + "\n",
    ATTEMPT + "/x", ATTEMPT + "?itemId=x", pytest.param("a" * 2000, id="longo"),
])
def test_attempt_invalido_recusa_antes_de_qualquer_remoto(user_id, pluggy_dublada, remoto, attempt):
    promote_to_pro(user_id)
    item = _semeia(user_id, f"attempt-{user_id}")
    remoto["dono"] = user_id
    client = TestClient(dashboard.app)
    r = _post(client, user_id, _auth(client, user_id), json.dumps({
        "app_scheme": "pigbank", "attempt_id": attempt, "item_id": item}))
    assert r.status_code == 400, r.text
    assert remoto["chamadas"] == []
    assert pluggy_dublada.api_key == 0 and pluggy_dublada.corpos == []
    assert pluggy_dublada.registros == []


@pytest.mark.parametrize("scheme", [{}, {"app_scheme": None}])
def test_attempt_exige_scheme_permitido(user_id, pluggy_dublada, scheme):
    promote_to_pro(user_id)
    client = TestClient(dashboard.app)
    r = _post(client, user_id, _auth(client, user_id), json.dumps({"attempt_id": ATTEMPT, **scheme}))
    assert r.status_code == 400, r.text
    assert pluggy_dublada.api_key == 0 and pluggy_dublada.corpos == []


@pytest.mark.parametrize("corpo", [
    '{"app_scheme":"pigbank","attempt_id":"' + ATTEMPT + '","attempt_id":"' + ATTEMPT + '"}',
    '{"app_scheme":"pigbank","app_scheme":"pigbank"}',
    '{"item_id":"x","item_id":"y"}',
    '{"app_scheme":"pigbank","app_\\u0073cheme":"pigbank"}',
    '{nao e json', b"\x80\x81",
    pytest.param(b"[" * 1500 + b"]" * 1499, id="aninhado-malformado"),
])
def test_corpo_ambiguo_ou_malformado_recusa(user_id, pluggy_dublada, remoto, corpo):
    promote_to_pro(user_id)
    client = TestClient(dashboard.app)
    r = _post(client, user_id, _auth(client, user_id), corpo)
    assert r.status_code == 400, r.text
    assert remoto["chamadas"] == []
    assert pluggy_dublada.api_key == 0 and pluggy_dublada.corpos == []


@pytest.mark.parametrize("tamanho", [4096, 4097])
def test_attempt_no_limite_de_bytes(user_id, pluggy_dublada, tamanho):
    promote_to_pro(user_id)
    casca = json.dumps({"app_scheme": "pigbank", "attempt_id": ATTEMPT, "pad": ""}).encode()
    corpo = casca[:-2] + b"A" * (tamanho - len(casca)) + b'"}'
    assert len(corpo) == tamanho
    client = TestClient(dashboard.app)
    r = _post(client, user_id, _auth(client, user_id), corpo)
    assert r.status_code == (200 if tamanho == 4096 else 400), r.text
    if tamanho == 4096:
        assert dict(_options(pluggy_dublada.corpos[0]))["oauthRedirectUri"] == f"pigbank://open-finance-volta/{ATTEMPT}"
    else:
        assert pluggy_dublada.api_key == 0 and pluggy_dublada.corpos == []


def test_corpo_lento_estrito_recusa(monkeypatch):
    monkeypatch.setattr(of_routes, "_CORPO_SEGUNDOS", 0.01)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(asyncio.wait_for(of_routes._corpo_json_limitado(
            _req(b'{"attempt_id":', pausa=10), estrito=True), 1))
    assert exc.value.status_code == 400


@pytest.mark.parametrize("corpo", [
    json.dumps({"app_scheme": "pigbank", "attempt_id": "invalido"}),
    '{"attempt_id":"x","attempt_id":"y"}', b"{" + b" " * 4096,
])
def test_attempt_nao_antecipa_os_portoes(user_id, pluggy_dublada, monkeypatch, corpo):
    anon = TestClient(dashboard.app)
    assert _post(anon, user_id, {"Content-Type": "application/json"}, corpo).status_code == 401
    client = TestClient(dashboard.app)
    headers = _auth(client, user_id)
    assert _post(client, user_id + 1, headers, corpo).status_code == 403
    promote_to_pro(user_id)
    monkeypatch.setattr(plan_service, "get_user_limits", lambda uid: {"of_banks_max": 0})
    r = _post(client, user_id, headers, corpo)
    assert r.status_code == 402 and r.json()["detail"]["code"] == "OF_BANK_LIMIT"
    assert pluggy_dublada.api_key == 0 and pluggy_dublada.corpos == []
