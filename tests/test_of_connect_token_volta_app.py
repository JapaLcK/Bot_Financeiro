"""`app_scheme` no connect-token: o app pede para a Pluggy voltar a ele depois do
OAuth do banco (`<scheme>://open-finance-volta`), por lista fechada.

Usa banco e TestClient, por isso não mora no `test_of_connect_token_gate.py`.
A Pluggy é dublada no transporte do httpx: o teste lê o corpo que iria para
`/connect_token`, e o site (sem o campo) tem de mandar o mesmo de antes.

CONTROLES (CLAUDE.md §3):
  • passar o valor cru, sem a lista → `test_app_scheme_invalido_da_400` vermelho;
  • tirar o `options["oauthRedirectUri"] = …` do serviço → `test_app_scheme_valido_vira_volta` vermelho
    (é também o controle positivo: a rota não recusa tudo);
  • gravar a chave sempre, mesmo com None → `test_site_manda_o_mesmo_de_antes` vermelho;
  • `except ValueError` sem o RecursionError → caso do corpo aninhado vermelho (500);
  • `await request.json()` sem teto, ou teto maior → `test_corpo_acima_do_teto_...` 4097 vermelho;
  • tirar o `asyncio.timeout` do helper → `test_corpo_lento_vale_sem_o_campo` vermelho (pelo prazo
    externo, sem travar);
  • ler/validar o corpo antes dos portões → `..._nao_passa_na_frente_dos_portoes` vermelho.
"""
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

import core.services.pluggy as pluggy
from core.services import plan_service
import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.open_finance as of_routes
from conftest import promote_to_pro
from test_of_item_ownership import _auth

WEBHOOK = "https://exemplo.test/open-finance/pluggy/webhook"
CHAVES_RESPOSTA = {"ok", "accessToken", "includeSandbox", "provider"}


@pytest.fixture()
def pluggy_dublada(monkeypatch):
    """Captura o que iria para a Pluggy e quem foi chamado."""
    monkeypatch.setenv("PLUGGY_WEBHOOK_URL", WEBHOOK)
    monkeypatch.delenv("PLUGGY_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("PLUGGY_PRODUCTS", raising=False)
    cap = SimpleNamespace(corpos=[], api_key=0, registros=[])

    def _api_key():
        cap.api_key += 1
        return "chave-de-teste"

    def _responde(req):
        cap.corpos.append(req.content)
        return httpx.Response(200, json={"accessToken": "tok-de-teste"})

    def _fabrica(*a, **kw):
        return httpx.Client(transport=httpx.MockTransport(_responde), **kw)

    monkeypatch.setattr(pluggy, "create_pluggy_api_key", _api_key)
    monkeypatch.setattr(pluggy, "httpx", SimpleNamespace(Client=_fabrica, Response=httpx.Response))
    monkeypatch.setattr(of_routes, "register_item",
                        lambda *a, **kw: cap.registros.append((a, kw)))
    return cap


def _options_de_hoje(uid: int) -> list:
    # Lista ordenada de pares, não dict: no caso do site a ORDEM das chaves também tem de ser a de hoje.
    # (O serializador é o do httpx, que não muda; o caso positivo abaixo compara como dict, porque a
    # ordem de `oauthRedirectUri` entre as chaves não é contrato.)
    return [("clientUserId", str(uid)), ("avoidDuplicates", True), ("webhookUrl", WEBHOOK),
            ("products", ["ACCOUNTS", "TRANSACTIONS", "CREDIT_CARDS", "INVESTMENTS"])]


def _options(corpo: bytes) -> list:
    payload = json.loads(corpo)
    assert list(payload) == ["options"]
    return list(payload["options"].items())


def _post(client, uid, headers, corpo):
    url = f"/open-finance/{uid}/connect-token"
    if corpo is None:
        return client.post(url, headers=headers)
    return client.post(url, headers=headers, content=corpo)


@pytest.mark.parametrize("corpo", [
    b"{}", None, b'{"app_scheme": null}', b"{nao e json", b"[]", b"\x80\x81",
    pytest.param(b"[" * 20000 + b"]" * 20000, id="aninhado"),  # RecursionError, não ValueError
])
def test_site_manda_o_mesmo_de_antes(user_id, pluggy_dublada, corpo):
    promote_to_pro(user_id)
    client = TestClient(dashboard.app)
    r = _post(client, user_id, _auth(client, user_id), corpo)
    assert r.status_code == 200, r.text
    assert set(r.json()) == CHAVES_RESPOSTA
    assert len(pluggy_dublada.corpos) == 1
    assert _options(pluggy_dublada.corpos[0]) == _options_de_hoje(user_id)


@pytest.mark.parametrize("scheme", ["pigbank", "pigbank-staging", "pigbank-dev"])
def test_app_scheme_valido_vira_volta(user_id, pluggy_dublada, scheme):
    promote_to_pro(user_id)
    client = TestClient(dashboard.app)
    r = _post(client, user_id, _auth(client, user_id), json.dumps({"app_scheme": scheme}))
    assert r.status_code == 200, r.text
    assert set(r.json()) == CHAVES_RESPOSTA
    volta = f"{scheme}://open-finance-volta"
    assert dict(_options(pluggy_dublada.corpos[0])) == {
        **dict(_options_de_hoje(user_id)), "oauthRedirectUri": volta}
    # A URI não vai para o banco: o rastro guarda só o hash do token.
    assert len(pluggy_dublada.registros) == 1
    assert "open-finance-volta" not in repr(pluggy_dublada.registros)


def _valido_com_tamanho(n: int) -> bytes:
    """JSON válido com app_scheme válido e exatamente `n` bytes."""
    casca = b'{"app_scheme":"pigbank","pad":""}'
    corpo = casca[:-2] + b"A" * (n - len(casca)) + b'"}'
    assert len(corpo) == n and json.loads(corpo)["app_scheme"] == "pigbank"
    return corpo


@pytest.mark.parametrize("n,aplica", [
    (4096, True), (4097, False), (1024 * 1024, False),
])
def test_corpo_acima_do_teto_vale_sem_o_campo(user_id, pluggy_dublada, n, aplica):
    """Teto de leitura (_CORPO_MAX): até 4096 bytes o corpo vale; acima, a rota para de
    ler e segue como se não houvesse corpo — 200 com o payload de hoje, nunca 400/500."""
    promote_to_pro(user_id)
    client = TestClient(dashboard.app)
    r = _post(client, user_id, _auth(client, user_id), _valido_com_tamanho(n))
    assert r.status_code == 200, r.text
    assert len(pluggy_dublada.corpos) == 1
    esperado = dict(_options_de_hoje(user_id))
    if aplica:
        esperado["oauthRedirectUri"] = "pigbank://open-finance-volta"
    assert dict(_options(pluggy_dublada.corpos[0])) == esperado


def _req(*pedacos, pausa=0.0):
    """Request falso: entrega `pedacos` e, se `pausa`, fica parado antes de encerrar."""
    async def stream():
        for p in pedacos:
            yield p
        if pausa:
            await asyncio.sleep(pausa)
    return SimpleNamespace(stream=stream)


def _le(req):
    # Prazo externo: se o helper regredir (sem prazo próprio), o teste falha em 2 s, não trava.
    return asyncio.run(asyncio.wait_for(of_routes._corpo_json_limitado(req), 2))


def test_corpo_lento_vale_sem_o_campo(monkeypatch):
    """Cliente autenticado manda poucos bytes e segura a conexão: o helper desiste no
    prazo (_CORPO_SEGUNDOS) e devolve None, em vez de manter a requisição viva."""
    monkeypatch.setattr(of_routes, "_CORPO_SEGUNDOS", 0.05)
    t0 = time.monotonic()
    assert _le(_req(b'{"app_scheme":', pausa=10)) is None
    assert time.monotonic() - t0 < 1


def test_corpo_rapido_valido_e_acima_do_teto():
    assert _le(_req(b'{"app_scheme":', b'"pigbank"}')) == {"app_scheme": "pigbank"}
    assert _le(_req(b"{", b" " * of_routes._CORPO_MAX, b"}")) is None


@pytest.mark.parametrize("valor", [
    "", " pigbank", "PIGBANK", "pigbank://x", "pigbank-staging\n", "exp", "https",
    123, True, ["pigbank"], {},
])
def test_app_scheme_invalido_da_400(user_id, pluggy_dublada, valor):
    promote_to_pro(user_id)
    client = TestClient(dashboard.app)
    r = _post(client, user_id, _auth(client, user_id), json.dumps({"app_scheme": valor}))
    assert r.status_code == 400, r.text
    assert r.json() == {"detail": "app_scheme inválido."}
    assert pluggy_dublada.corpos == [] and pluggy_dublada.api_key == 0
    assert pluggy_dublada.registros == []


def test_outro_usuario_e_anonimo_nao_mudam(user_id, pluggy_dublada):
    promote_to_pro(user_id)
    valido = json.dumps({"app_scheme": "pigbank"})
    outro = user_id + 1

    client = TestClient(dashboard.app)
    headers = _auth(client, user_id)
    hoje = _post(client, outro, headers, b"{}").status_code
    assert hoje in (401, 403)
    assert _post(client, outro, headers, valido).status_code == hoje

    anon = TestClient(dashboard.app)
    h = {"Content-Type": "application/json"}
    hoje_anon = _post(anon, user_id, h, b"{}").status_code
    assert hoje_anon in (401, 403)
    assert _post(anon, user_id, h, valido).status_code == hoje_anon

    assert pluggy_dublada.corpos == [] and pluggy_dublada.api_key == 0


@pytest.mark.parametrize("valor", ["PIGBANK", ["pigbank"]])
def test_app_scheme_invalido_nao_passa_na_frente_dos_portoes(user_id, pluggy_dublada,
                                                             monkeypatch, valor):
    """Anônimo, outro usuário e plano sem vaga de banco (OF_BANK_LIMIT) respondem o
    mesmo que com `{}`: o corpo só é lido depois dos portões, então um anônimo nunca
    vê o 400."""
    invalido = json.dumps({"app_scheme": valor})

    def _igual_a_hoje(client, uid, headers, status):
        hoje = _post(client, uid, headers, b"{}")
        r = _post(client, uid, headers, invalido)
        assert (r.status_code, r.json()) == (hoje.status_code, hoje.json())
        assert r.status_code == status, r.text
        return r

    anon = TestClient(dashboard.app)
    _igual_a_hoje(anon, user_id, {"Content-Type": "application/json"}, 401)

    client = TestClient(dashboard.app)
    headers = _auth(client, user_id)
    _igual_a_hoje(client, user_id + 1, headers, 403)

    # Com assinatura (senão o 402 é o `subscription_required` do authorize_dashboard_access,
    # que roda antes) e plano sem vaga: o 402 tem de ser o de `_ensure_of_access_allowed`.
    promote_to_pro(user_id)
    monkeypatch.setattr(plan_service, "get_user_limits", lambda uid: {"of_banks_max": 0})
    r = _igual_a_hoje(client, user_id, headers, 402)
    assert r.json()["detail"]["code"] == "OF_BANK_LIMIT", r.text

    assert pluggy_dublada.corpos == [] and pluggy_dublada.api_key == 0
    assert pluggy_dublada.registros == []
