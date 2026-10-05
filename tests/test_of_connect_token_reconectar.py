"""`item_id` no connect-token: reconectar um banco já conectado sem cair no
"already exists" da Pluggy (`avoidDuplicates`), sem abrir caminho para item alheio.

Dono pelo NOSSO banco (`list_pluggy_item_ids`, filtra `user_id` e tira PAUSED)
antes de qualquer chamada à Pluggy; depois o `clientUserId` remoto. Todo "não é
seu / não existe" responde o MESMO 404, byte a byte.

CONTROLES (CLAUDE.md §3):
  • tirar a checagem no banco → `test_item_de_outro_usuario_...` vermelho;
  • tirar o `payload["itemId"]` do serviço (ou pô-lo dentro de `options`) →
    `test_item_proprio_vai_no_nivel_de_cima` vermelho (é também o controle positivo);
  • tirar a comparação do `clientUserId` → caso `client_user_id_diferente` vermelho;
  • corpos diferentes entre alheio e inexistente → `test_item_de_outro_usuario_...` vermelho;
  • tirar o 404 do `/connect_token` → `test_item_some_antes_do_connect_token[True-404-404]` vermelho.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.open_finance as of_routes
from conftest import _cleanup_user, promote_to_pro
from core.services import plan_service, pluggy
from core.services.pluggy import PluggyApiError
from db.connection import get_conn
from test_of_connect_token_volta_app import _options_de_hoje, _post, pluggy_dublada  # noqa: F401
from test_of_item_ownership import _auth, eventos  # noqa: F401

NAO_ACHOU = {"code": "OF_ITEM_NAO_ENCONTRADO", "message": "Não achamos esse banco nas suas conexões."}


def _semeia(uid: int, item: str, status: str = "UPDATED") -> str:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into open_finance_connections "
            "(user_id, provider, provider_item_id, status, institution_id, institution_name) "
            "values (%s, 'pluggy', %s, %s, '612', 'Nubank')",
            (uid, item, status),
        )
        conn.commit()
    return item


@pytest.fixture()
def remoto(monkeypatch):
    """`get_pluggy_item` dublado: registra as chamadas; `dono`/`erro` decidem a resposta."""
    estado = {"chamadas": [], "dono": None, "erro": None}

    def _get(item_id, api_key=None):
        estado["chamadas"].append(item_id)
        if estado["erro"]:
            raise estado["erro"]
        return {"id": item_id, "status": "UPDATED", "clientUserId": str(estado["dono"])}

    monkeypatch.setattr(of_routes, "get_pluggy_item", _get)
    return estado


def _corpo(item_id, **extra) -> str:
    return json.dumps({"item_id": item_id, **extra})


def _nada_foi_a_pluggy(dublada) -> None:
    assert dublada.corpos == [] and dublada.api_key == 0 and dublada.registros == []


@pytest.mark.parametrize("extra", [{}, {"app_scheme": "pigbank"}])
def test_item_proprio_vai_no_nivel_de_cima(user_id, pluggy_dublada, remoto, extra):
    promote_to_pro(user_id)
    item = _semeia(user_id, f"rc-{user_id}")
    remoto["dono"] = user_id
    client = TestClient(dashboard.app)
    r = _post(client, user_id, _auth(client, user_id), _corpo(item, **extra))
    assert r.status_code == 200, r.text
    assert remoto["chamadas"] == [item]
    payload = json.loads(pluggy_dublada.corpos[0])
    assert set(payload) == {"options", "itemId"} and payload["itemId"] == item
    esperado = dict(_options_de_hoje(user_id))
    if extra:
        esperado["oauthRedirectUri"] = "pigbank://open-finance-volta"
    assert payload["options"] == esperado  # sem `itemId` dentro, com `avoidDuplicates`


def test_item_de_outro_usuario_404_igual_ao_inexistente(user_id, pluggy_dublada, remoto):
    promote_to_pro(user_id)
    outro = user_id + 1
    db.ensure_user(outro)
    try:
        alheio = _semeia(outro, f"rc-{outro}")
        remoto["dono"] = user_id  # mesmo se a Pluggy dissesse que é dele, nem pergunta
        client = TestClient(dashboard.app)
        headers = _auth(client, user_id)
        r_alheio = _post(client, user_id, headers, _corpo(alheio))
        r_nada = _post(client, user_id, headers, _corpo("rc-nao-existe"))
        r_fora = _post(client, user_id, headers, _corpo("../../accounts"))
    finally:
        _cleanup_user(outro)
    assert r_alheio.status_code == 404, r_alheio.text
    assert r_alheio.json() == {"detail": NAO_ACHOU}
    assert r_alheio.content == r_nada.content == r_fora.content
    assert remoto["chamadas"] == []
    _nada_foi_a_pluggy(pluggy_dublada)


@pytest.mark.parametrize("caso", ["paused", "removido", "client_user_id_diferente", "pluggy_404"])
def test_demais_casos_dao_o_mesmo_404(user_id, pluggy_dublada, remoto, eventos, caso):  # noqa: F811
    promote_to_pro(user_id)
    item = _semeia(user_id, f"rc-{user_id}", "PAUSED" if caso == "paused" else "UPDATED")
    remoto["dono"] = user_id
    if caso == "removido":
        db.disconnect_open_finance_connection(user_id)
    if caso == "client_user_id_diferente":
        remoto["dono"] = user_id + 1
    if caso == "pluggy_404":
        remoto["erro"] = PluggyApiError("sumiu", status_code=404)
    client = TestClient(dashboard.app)
    headers = _auth(client, user_id)
    r = _post(client, user_id, headers, _corpo(item))
    assert r.status_code == 404, r.text
    assert r.content == _post(client, user_id, headers, _corpo("rc-nao-existe")).content
    vai_a_pluggy = caso in ("client_user_id_diferente", "pluggy_404")
    assert remoto["chamadas"] == ([item] if vai_a_pluggy else [])
    conflitos = [e["details"] for e in eventos if e["event"] == "of_item_owner_conflict"]
    assert conflitos == ([{"item_id": item, "origin": "connect_token"}]
                         if caso == "client_user_id_diferente" else [])
    _nada_foi_a_pluggy(pluggy_dublada)


@pytest.mark.parametrize("com_item,status,esperado", [(True, 404, 404), (True, 500, 502), (False, 404, 502)])
def test_item_some_antes_do_connect_token(user_id, pluggy_dublada, remoto, monkeypatch,
                                          com_item, status, esperado):
    """Passa no GET e some antes do POST `/connect_token`: o mesmo 404. Sem `item_id`, ou 5xx, segue 502."""
    promote_to_pro(user_id)
    item = _semeia(user_id, f"rc-{user_id}")
    remoto["dono"] = user_id
    client = TestClient(dashboard.app)
    headers = _auth(client, user_id)
    nada = _post(client, user_id, headers, _corpo("rc-nao-existe")).content
    monkeypatch.setattr(pluggy, "httpx", SimpleNamespace(Response=httpx.Response, Client=lambda *a, **kw: httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(status, json={})), **kw)))
    r = _post(client, user_id, headers, _corpo(item) if com_item else b"{}")
    assert r.status_code == esperado, r.text
    assert (r.content == nada) is (esperado == 404)
    assert pluggy_dublada.registros == []


@pytest.mark.parametrize("valor", [123, [], {}, ""])
def test_item_id_invalido_da_400(user_id, pluggy_dublada, remoto, valor):
    promote_to_pro(user_id)
    client = TestClient(dashboard.app)
    r = _post(client, user_id, _auth(client, user_id), _corpo(valor))
    assert r.status_code == 400, r.text
    assert r.json() == {"detail": "item_id inválido."}
    assert remoto["chamadas"] == []
    _nada_foi_a_pluggy(pluggy_dublada)


def test_item_alheio_nao_passa_na_frente_dos_portoes(user_id, pluggy_dublada, remoto, monkeypatch):
    """Anônimo, outra sessão e 402 sem OF respondem o mesmo que com `{}`."""
    outro = user_id + 1
    db.ensure_user(outro)
    try:
        alheio = _corpo(_semeia(outro, f"rc-{outro}"))

        def _igual_a_hoje(client, uid, headers, status):
            hoje = _post(client, uid, headers, b"{}")
            r = _post(client, uid, headers, alheio)
            assert (r.status_code, r.json()) == (hoje.status_code, hoje.json())
            assert r.status_code == status, r.text

        _igual_a_hoje(TestClient(dashboard.app), user_id, {"Content-Type": "application/json"}, 401)
        client = TestClient(dashboard.app)
        headers = _auth(client, user_id)
        _igual_a_hoje(client, outro, headers, 403)
        promote_to_pro(user_id)
        monkeypatch.setattr(plan_service, "get_user_limits", lambda uid: {"of_banks_max": 0})
        _igual_a_hoje(client, user_id, headers, 402)
    finally:
        _cleanup_user(outro)
    assert remoto["chamadas"] == []
    _nada_foi_a_pluggy(pluggy_dublada)
