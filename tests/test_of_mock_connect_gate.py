"""POST /open-finance/{user_id}/mock-connect — portão de ambiente e teto de bancos.

A rota cria conexão FALSA (`provider='mock_pluggy'`) e existe para o E2E do app
no staging. Decisão do dono: sem `OF_MOCK_CONNECT_ENABLED` ligada ela é 404,
igual a rota inexistente; ligada, respeita o teto de bancos do plano; a falsa
não ocupa vaga do teto (`count_open_finance_connections` conta só `pluggy`).

CONTROLES (§3), medidos por mutação na rota:
  * negativo — tirar o `dependencies=[Depends(_exige_mock_connect)]` deixa
    `test_desligado_*` vermelhos; tirar o `_enforce_bank_limit` deixa
    `test_limite_*` vermelhos; aceitar tudo que não é "0" deixa o desligado com
    o switch ausente/"" vermelho; mover o portão para depois da autenticação
    deixa `test_desligado_anonimo_*` vermelho (401 no lugar de 404); voltar a
    `payload: OpenFinanceMockConnectPayload` na assinatura deixa
    `test_desligado_corpo_hostil_*` vermelho (o FastAPI decodifica o JSON antes
    do portão: `{not json` vira 422 e revela a rota).
  * positivo — `test_ligado_*` (cookie e Bearer do app),
    `test_falsa_nao_ocupa_vaga` e `test_ligado_corpo_*` (o contrato do corpo de
    antes, agora lido à mão) provam que o caminho legítimo do staging segue.

Cego a: o valor real da env no Railway — isto mede o código, não a config.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from starlette.requests import Request
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

os.environ.setdefault("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())

import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.open_finance as of_routes
from conftest import em_carencia, promote_to_pro
from core.services.plan_service import get_user_limits
from db import ensure_user
from db.connection import get_conn

ENV = "OF_MOCK_CONNECT_ENABLED"
DESLIGADOS = [None, "", " ", "0", "false", "no", "off", "2", "sim", "enabled"]
LIGADOS = ["1", "true", "TRUE ", " yes", "on"]


def _switch(monkeypatch, valor):
    if valor is None:
        monkeypatch.delenv(ENV, raising=False)
    else:
        monkeypatch.setenv(ENV, valor)


def _auth(client: TestClient, user_id: int) -> dict:
    client.cookies.set(dashboard.AUTH_COOKIE_NAME, dashboard._make_jwt(user_id, "of-mock@t.com"))
    client.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, dashboard.make_dashboard_token(user_id, hours=1))
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, "test-csrf-token")
    return {dashboard.CSRF_HEADER_NAME: "test-csrf-token"}


def _conexoes(user_id: int, provider: str | None = None) -> int:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select count(*) as n from open_finance_connections"
            " where user_id = %s and (%s::text is null or provider = %s)",
            (user_id, provider, provider),
        )
        n = cur.fetchone()["n"]
        conn.commit()
    return n


def _semeia_pluggy(user_id: int, n: int) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        for i in range(n):
            cur.execute(
                "insert into open_finance_connections "
                "(user_id, provider, provider_item_id, status, institution_id, institution_name) "
                "values (%s, 'pluggy', %s, 'UPDATED', '612', 'Nubank')",
                (user_id, f"mock-gate-{user_id}-{i}"),
            )
        conn.commit()


def _url(user_id) -> str:
    return f"/open-finance/{user_id}/mock-connect"


def _inexistente(client, user_id, headers=None):
    return client.post(f"/open-finance/{user_id}/mock-connect-inexistente", headers=headers or {}, json={})


# ── G1: desligado = 404 igual a rota inexistente, nada gravado ─────────────────

@pytest.mark.parametrize("valor", DESLIGADOS)
def test_desligado_dono_autenticado_recebe_404_de_rota_inexistente(user_id, monkeypatch, valor):
    promote_to_pro(user_id, "essencial")
    _switch(monkeypatch, valor)
    client = TestClient(dashboard.app)
    headers = _auth(client, user_id)
    ref = _inexistente(client, user_id, headers)
    assert ref.status_code == 404
    for kwargs in ({"json": {}}, {"json": {"institution": "itau"}}, {}):
        r = client.post(_url(user_id), headers=headers, **kwargs)
        assert (r.status_code, r.content) == (ref.status_code, ref.content), (valor, kwargs, r.text)
    assert _conexoes(user_id) == 0


@pytest.mark.parametrize("valor", [None, "", "0"])
def test_desligado_anonimo_e_path_invalido_tambem_404(user_id, monkeypatch, valor):
    _switch(monkeypatch, valor)
    client = TestClient(dashboard.app)
    ref = _inexistente(client, user_id)
    assert ref.status_code == 404
    for url in (_url(user_id), _url("abc")):
        r = client.post(url, json={})
        assert (r.status_code, r.content) == (ref.status_code, ref.content), (valor, url, r.text)
    assert _conexoes(user_id) == 0


# ── G2: ligado, caminho legítimo ───────────────────────────────────────────────

@pytest.mark.parametrize("valor", LIGADOS)
def test_ligado_essencial_sem_banco_conecta_a_falsa(user_id, monkeypatch, valor):
    promote_to_pro(user_id, "essencial")
    _switch(monkeypatch, valor)
    client = TestClient(dashboard.app)
    r = client.post(_url(user_id), headers=_auth(client, user_id), json={})
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["sync"]["accounts_synced"] == 2 and corpo["sync"]["transactions_synced"] == 7
    assert _conexoes(user_id, "mock_pluggy") == 1 and _conexoes(user_id) == 1


def test_ligado_pelo_bearer_do_app_sem_cookie(user_id, monkeypatch):
    promote_to_pro(user_id, "essencial")
    _switch(monkeypatch, "1")
    client = TestClient(dashboard.app)
    token = dashboard.make_dashboard_token(user_id, hours=1)
    r = client.post(
        _url(user_id),
        headers={"Authorization": f"Bearer {token}", dashboard.APP_CLIENT_HEADER: "app"},
        json={},
    )
    assert r.status_code == 200, r.text
    assert _conexoes(user_id, "mock_pluggy") == 1


# ── G3: teto de bancos ─────────────────────────────────────────────────────────

def _assert_402(r, limit):
    assert r.status_code == 402, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "OF_BANK_LIMIT" and detail["limit"] == limit, detail


def test_limite_carencia_sem_open_finance_recusa_e_nao_grava(user_id, monkeypatch):
    em_carencia(user_id)
    _switch(monkeypatch, "1")
    client = TestClient(dashboard.app)
    _assert_402(client.post(_url(user_id), headers=_auth(client, user_id), json={}), 0)
    assert _conexoes(user_id) == 0


def test_limite_essencial_com_banco_real_recusa_e_nao_grava(user_id, monkeypatch):
    promote_to_pro(user_id, "essencial")
    _semeia_pluggy(user_id, 1)
    _switch(monkeypatch, "1")
    client = TestClient(dashboard.app)
    _assert_402(client.post(_url(user_id), headers=_auth(client, user_id), json={}), 1)
    assert _conexoes(user_id, "mock_pluggy") == 0


@pytest.mark.parametrize("plano", ["pro", "pro_max"])
def test_limite_plano_maior_com_uma_vaga_passa(user_id, monkeypatch, plano):
    promote_to_pro(user_id, plano)
    teto = get_user_limits(user_id)["of_banks_max"]
    assert teto and teto >= 2, (plano, teto)
    _semeia_pluggy(user_id, teto - 1)
    _switch(monkeypatch, "1")
    client = TestClient(dashboard.app)
    r = client.post(_url(user_id), headers=_auth(client, user_id), json={})
    assert r.status_code == 200, r.text
    assert _conexoes(user_id, "mock_pluggy") == 1


def test_falsa_nao_ocupa_vaga(user_id, monkeypatch):
    promote_to_pro(user_id, "essencial")
    _switch(monkeypatch, "1")
    client = TestClient(dashboard.app)
    headers = _auth(client, user_id)
    for inst in ("nubank", "itau", "bradesco"):
        r = client.post(_url(user_id), headers=headers, json={"institution": inst})
        assert r.status_code == 200, (inst, r.text)
    assert _conexoes(user_id, "mock_pluggy") == 3
    # A mesma função que o POST /pluggy-item chama: banco REAL novo ainda cabe.
    asyncio.run(of_routes._enforce_bank_limit(user_id, f"real-{user_id}"))


# ── G4: isolamento ─────────────────────────────────────────────────────────────

def test_ligado_um_usuario_nao_cria_falsa_no_outro(user_id, monkeypatch):
    promote_to_pro(user_id, "essencial")
    outro = int(uuid.uuid4().int % 10_000_000_000)
    ensure_user(outro)
    promote_to_pro(outro, "essencial")
    _switch(monkeypatch, "1")
    client = TestClient(dashboard.app)
    r = client.post(_url(outro), headers=_auth(client, user_id), json={})
    assert r.status_code == 403, r.text
    assert _conexoes(user_id) == 0 and _conexoes(outro) == 0


def test_ligado_anonimo_recebe_401(user_id, monkeypatch):
    _switch(monkeypatch, "1")
    r = TestClient(dashboard.app).post(_url(user_id), json={})
    assert r.status_code == 401, r.text
    assert _conexoes(user_id) == 0


# ── G5: desligado + corpo hostil = o mesmo 404, e o corpo nem é lido ──────────

_JSON = {"content-type": "application/json"}
CORPOS_HOSTIS = [
    {"content": b"{not json", "headers": _JSON},
    {"content": b"", "headers": _JSON},
    {"json": []},
    {"json": "x"},
    {"content": b"null", "headers": _JSON},
    {"json": {"institution": 123}},
    {"json": {"institution": {"a": 1}}},
    {"content": b"\xff" * (1024 * 1024 + 1), "headers": _JSON},
    {"content": b'{"institution": "itau"}', "headers": {"content-type": "text/plain"}},
    {"files": {"f": ("a.txt", b"{not json")}},
    {"data": {"institution": "itau"}},
    {"content": lambda: iter((b"{not", b" json")), "headers": _JSON},  # Transfer-Encoding: chunked
]


def _explode(*_a, **_k):
    raise AssertionError("corpo lido antes da hora")


@pytest.mark.parametrize("autenticado", [False, True])
@pytest.mark.parametrize("kw", CORPOS_HOSTIS, ids=range(len(CORPOS_HOSTIS)))
def test_desligado_corpo_hostil_404_de_rota_inexistente(user_id, monkeypatch, autenticado, kw):
    promote_to_pro(user_id, "essencial")
    _switch(monkeypatch, None)
    client = TestClient(dashboard.app)
    auth = _auth(client, user_id) if autenticado else {}
    kw = {**kw, "headers": {**auth, **kw.get("headers", {})}}
    fresco = lambda: {**kw, "content": kw["content"]()} if callable(kw.get("content")) else kw  # noqa: E731
    ref = client.post(f"/open-finance/{user_id}/mock-connect-inexistente", **fresco())
    # 403: anônimo sem application/json morre no CSRF, que roda antes do roteamento.
    assert ref.status_code in (403, 404)
    for metodo in ("body", "json", "form"):  # `stream` não: o BaseHTTPMiddleware o embrulha
        monkeypatch.setattr(Request, metodo, _explode)
    r = client.post(_url(user_id), **fresco())
    assert (r.status_code, r.content) == (ref.status_code, ref.content), r.text
    assert _conexoes(user_id) == 0


# ── G6: ligado, o contrato do corpo é o de antes (só lido depois da sessão) ────

@pytest.mark.parametrize("kw", [
    {"content": b"{not json", "headers": _JSON},
    {"content": b"", "headers": _JSON},
    {},
    {"json": []},
    {"content": b"null", "headers": _JSON},
    {"json": {"institution": 123}},
    {"content": b'{"institution": "itau"}', "headers": {"content-type": "text/plain"}},
    {"data": {"institution": "itau"}},
    {"content": b'{"institution": "itau"}', "headers": {"content-type": "application/x-www-form-urlencoded"}},
], ids=range(9))
def test_ligado_corpo_invalido_422_sem_gravar(user_id, monkeypatch, kw):
    promote_to_pro(user_id, "essencial")
    _switch(monkeypatch, "1")
    client = TestClient(dashboard.app)
    kw = {**kw, "headers": {**_auth(client, user_id), **kw.get("headers", {})}}
    r = client.post(_url(user_id), **kw)
    assert r.status_code == 422, r.text
    assert r.json()["detail"][0]["loc"][0] == "body", r.text
    assert _conexoes(user_id) == 0


@pytest.mark.parametrize("kw, esperado", [
    ({"json": {}}, "Nubank"),
    ({"json": {"institution": None}}, "Nubank"),
    ({"json": {"institution": "xpto"}}, "Nubank"),
    ({"json": {"institution": " Itaú "}}, "Itaú"),
    ({"content": b'{"institution": "bradesco"}', "headers": {"content-type": "application/vnd.x+json"}}, "Bradesco"),
    ({"content": b'{"institution": "itau"}', "headers": {"content-type": "application/json; charset=utf-8"}}, "Itaú"),
])
def test_ligado_corpo_valido_conecta_como_antes(user_id, monkeypatch, kw, esperado):
    promote_to_pro(user_id, "essencial")
    _switch(monkeypatch, "1")
    client = TestClient(dashboard.app)
    kw = {**kw, "headers": {**_auth(client, user_id), **kw.get("headers", {})}}
    r = client.post(_url(user_id), **kw)
    assert r.status_code == 200, r.text
    assert [c["institution_name"] for c in r.json()["connections"]] == [esperado]


def test_ligado_anonimo_com_json_malformado_e_401_nao_422(user_id, monkeypatch):
    _switch(monkeypatch, "1")
    r = TestClient(dashboard.app).post(_url(user_id), content=b"{not json", headers=_JSON)
    assert r.status_code == 401, r.text
