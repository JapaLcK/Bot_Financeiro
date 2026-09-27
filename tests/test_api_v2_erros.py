"""Envelope de erro da /api/v2, pelo monólito real (mount + middlewares do pai).

As rotas que precisam lançar (422, 499, 500 e 503) são registradas no sub-app REAL só
durante o teste, sem `usuario_atual` — com ele o 401 viria antes do que se mede.
"""
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from psycopg_pool import PoolTimeout
from pydantic import BaseModel

import frontend.finance_bot_websocket_custom as dashboard
from api.v2 import app as app_v2

SENHA = "senhaforte123"


class Login(BaseModel):
    email: str
    password: str


@pytest.fixture
def rota_temporaria():
    rotas = []

    def registra(path, fn, metodo):
        app_v2.router.add_api_route(path, fn, methods=[metodo])
        rotas.append(app_v2.router.routes[-1])

    yield registra
    for r in rotas:
        app_v2.router.routes.remove(r)


def test_rota_inexistente_404():
    r = TestClient(dashboard.app).get("/api/v2/nao-existe")
    assert r.status_code == 404
    assert r.json() == {"error": {"code": "not_found", "message": "Not Found"}}


def test_metodo_errado_405_com_allow():
    # Cliente novo, sem cookie e com JSON (a isenção de CSRF do cliente nativo):
    # fora disso o CSRF do pai responde 403 antes de o sub-app ver o método.
    r = TestClient(dashboard.app).post("/api/v2/me", json={})
    assert r.status_code == 405, r.text
    assert r.json()["error"]["code"] == "method_not_allowed"
    assert r.headers.get("allow") == "GET"


def _corpo_do_422(client, path):
    r = client.post(path, json={"password": SENHA})
    assert r.status_code == 422, r.text
    return r


def test_422_nao_ecoa_a_senha(rota_temporaria):
    def login(corpo: Login):  # pragma: no cover - a validação recusa antes
        return {}

    rota_temporaria("/_teste_422", login, "POST")
    r = _corpo_do_422(TestClient(dashboard.app), "/api/v2/_teste_422")
    assert SENHA not in r.text
    erro = r.json()["error"]
    assert erro["code"] == "validation_error"
    assert erro["details"] == [{"loc": ["body", "email"], "msg": "Field required", "type": "missing"}]


def test_controle_negativo_do_422_o_tratador_padrao_ecoa_a_senha():
    """Prova que a sonda acima mede: sem o tratador da v2, a mesma requisição
    devolve a senha no `input`."""
    app = FastAPI()

    @app.post("/x")
    def login(corpo: Login):  # pragma: no cover
        return {}

    assert SENHA in _corpo_do_422(TestClient(app), "/x").text


def test_500_no_envelope_registra_uma_vez_e_nao_vaza_traceback(monkeypatch, rota_temporaria):
    import api.v2.erros as erros
    import core.admin_dashboard as admin

    chamadas = []

    async def grava(*args, **kwargs):
        chamadas.append((args, kwargs))

    monkeypatch.setattr(erros, "log_system_event", grava)
    monkeypatch.setattr(admin, "log_system_event", grava)

    def explode():
        raise RuntimeError("detalhe-interno-secreto")

    rota_temporaria("/_teste_500", explode, "GET")
    # Obrigatório, não cosmético: o starlette re-levanta a exceção depois do envelope
    # (ver `erro_interno`), e com o default `True` o próprio teste a levantaria.
    r = TestClient(dashboard.app, raise_server_exceptions=False).get("/api/v2/_teste_500")
    assert r.status_code == 500, r.text
    assert r.json() == {"error": {"code": "internal_error", "message": "Erro interno do servidor."}}
    assert "detalhe-interno-secreto" not in r.text and "Traceback" not in r.text
    # Uma vez: o pai não enxerga o 500 do sub-app, e dois registros dobrariam o
    # erro no painel de admin.
    assert len(chamadas) == 1, chamadas
    args, kwargs = chamadas[0]
    assert args[:2] == ("error", "http_unhandled_exception")
    assert kwargs["source"] == "GET /api/v2/_teste_500"


async def _cliente_some(request: Request):
    async def _receive():
        return {"type": "http.disconnect"}

    # ClientDisconnect do ponto REAL onde o starlette a levanta (Request.stream).
    await Request(request.scope, _receive).body()


def _pool_esgotado():
    # A mensagem real do psycopg_pool (pool.py) quando o pool não entrega conexão.
    raise PoolTimeout("couldn't get a connection after 30.00 sec")


def _bug():
    raise RuntimeError("bug de verdade")


@pytest.mark.parametrize("fn, status, corpo, eventos", [
    pytest.param(_pool_esgotado, 503, {"error": {
        "code": "service_unavailable",
        "message": "Serviço temporariamente indisponível. Tente novamente em instantes."}},
        503, id="banco_fora"),
    pytest.param(_cliente_some, 499, None, None, id="cliente_sumiu"),
    pytest.param(_bug, 500, {"error": {
        "code": "internal_error", "message": "Erro interno do servidor."}},
        500, id="bug_de_verdade"),
])
def test_classificacao_do_erro_e_a_mesma_do_pai(monkeypatch, rota_temporaria, fn, status, corpo, eventos):
    """Mesma regra do `admin_error_logging_middleware` (`status_do_erro`): banco
    fora → 503 com um evento; cliente sumiu → 499 sem evento nenhum; bug → 500.
    Desligar a classificação na v2 põe os dois primeiros em 500 — o terceiro é
    o controle positivo contra "tudo vira 503/499"."""
    import api.v2.erros as erros
    import core.admin_dashboard as admin

    chamadas = []

    async def grava(*args, **kwargs):
        chamadas.append(kwargs)

    monkeypatch.setattr(erros, "log_system_event", grava)
    monkeypatch.setattr(admin, "log_system_event", grava)

    rota_temporaria("/_teste_classe", fn, "GET")
    r = TestClient(dashboard.app, raise_server_exceptions=False).get("/api/v2/_teste_classe")
    assert r.status_code == status, r.text
    if corpo is None:
        assert r.content == b"" and chamadas == []
    else:
        assert r.json() == corpo
        assert [c["details"]["status_code"] for c in chamadas] == [eventos]
