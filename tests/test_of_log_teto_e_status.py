"""Issue #541, rodada 3: o teto do `_log_com_teto` vale mesmo com o espelho do
logging lento, e o `status_code` da Pluggy sobrevive à lista branca.

O `_DashboardHandler` (`core/observability.py`) espelha todo WARNING com INSERT
SÍNCRONO dentro do event loop. Um `logging.warning` "local" antes do
`_log_com_teto` dobrava o prazo com `system_event_logs` travada e parava o event
loop. O rastro de teto estourado é um `print` em stderr, sem banco.

CONTROLES (CLAUDE.md §3), cada um num caso verde:
  • recolocar um `logging.warning` antes do `_log_com_teto` do connect-token →
    `test_teto_vale_com_o_espelho_do_logging_lento` vermelho;
  • tirar `status_code` dos details de `pluggy_disconnect_auth_failed` ou de
    `pluggy_sync_failed` → o respectivo caso vermelho;
  • voltar `str(exc)` no skip "sem dono resolvível" → o caso do clientUserId vermelho.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import httpx
import psycopg
from fastapi.testclient import TestClient

import core.observability as observability
import core.services.pluggy as pluggy
import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.open_finance as of_routes
from conftest import promote_to_pro
from core.services.pluggy import PluggyApiError
from test_of_item_ownership import _auth, _webhook, eventos  # noqa: F401
from test_of_log_dono_coluna import HOST, _de, _levanta, sync_log  # noqa: F401
from test_of_webhook_adopt_guards import _limpa_item, webhook_pluggy  # noqa: F401
from test_open_finance_disconnect_route import _semeia_conexao_pluggy


def test_teto_vale_com_o_espelho_do_logging_lento(user_id, monkeypatch):
    promote_to_pro(user_id)
    monkeypatch.setattr(of_routes, "create_pluggy_connect_token",
                        lambda uid, webhook_url=None, oauth_redirect_uri=None: {"accessToken": "tok"})
    monkeypatch.setattr(of_routes, "register_item", _levanta(psycopg.OperationalError(HOST)))
    monkeypatch.setattr(of_routes, "_LOG_DIAG_TIMEOUT_S", 0.05)

    async def _pendura(*a, **k):
        await asyncio.sleep(3)
    monkeypatch.setattr(of_routes, "log_system_event", _pendura)
    # O espelho de verdade no root, com o INSERT dublado por um sono: é a tabela
    # travada da sonda do Tester, sem precisar de uma segunda conexão.
    monkeypatch.setattr(observability, "log_system_event_sync",
                        lambda *a, **k: time.sleep(1.5))
    observability._configure_root_logger()

    client = TestClient(dashboard.app)
    headers = _auth(client, user_id)
    t0 = time.monotonic()
    r = client.post(f"/open-finance/{user_id}/connect-token", headers=headers)
    dt = time.monotonic() - t0
    assert r.status_code == 200, r.text
    assert dt < 1.0, f"o espelho síncrono do logging furou o teto: {dt:.2f}s"


def test_disconnect_sem_apikey_guarda_o_status_da_pluggy(user_id, monkeypatch, sync_log):
    promote_to_pro(user_id)
    _semeia_conexao_pluggy(user_id, f"i541-401-{user_id}")
    monkeypatch.delenv("PLUGGY_API_KEY", raising=False)
    monkeypatch.setenv("PLUGGY_CLIENT_ID", "cid")
    monkeypatch.setenv("PLUGGY_CLIENT_SECRET", "segredo")

    def _fabrica(*a, **kw):
        return httpx.Client(transport=httpx.MockTransport(
            lambda req: httpx.Response(401, json={"code": "UNAUTHORIZED"})), **kw)
    monkeypatch.setattr(pluggy, "httpx", SimpleNamespace(Client=_fabrica, Response=httpx.Response))

    client = TestClient(dashboard.app)
    r = client.delete(f"/open-finance/{user_id}", headers=_auth(client, user_id))
    assert r.status_code == 200, r.text
    linhas = [e for e in sync_log if e["event"] == "pluggy_disconnect_auth_failed"]
    assert len(linhas) == 1, sync_log
    assert linhas[0]["details"]["motivo"] == "PluggyApiError", linhas
    assert linhas[0]["details"]["status_code"] == 401, linhas


def test_sync_que_cai_na_pluggy_guarda_o_status(monkeypatch, eventos):
    monkeypatch.setattr(of_routes, "sync_pluggy_item", _levanta(PluggyApiError(
        "Falha ao autenticar na Pluggy: Pluggy retornou HTTP 401", status_code=401)))
    asyncio.run(of_routes._run_pluggy_sync_bg("i541-sync401"))
    falha = _de(eventos, "pluggy_sync_failed")
    assert len(falha) == 1 and falha[0]["details"]["status_code"] == 401, eventos


def test_client_user_id_fora_da_forma_nao_vai_cru_para_o_log(monkeypatch, eventos, webhook_pluggy):
    monkeypatch.setattr(of_routes, "get_pluggy_item", lambda item_id, api_key=None: {
        "id": item_id, "status": "UPDATED", "clientUserId": "+123456789"})
    try:
        assert _webhook(TestClient(dashboard.app), "item/created", "i541-forma").status_code == 200
        skip = [e for e in _de(eventos, "of_webhook_adopt_skipped")
                if e["details"]["item_id"] == "i541-forma"]
        assert len(skip) == 1 and skip[0]["details"]["motivo"] == "ValueError", eventos
        assert skip[0].get("user_id") is None, skip
        assert "123456789" not in str(skip[0]["details"]), "clientUserId bruto com coluna NULL"
    finally:
        _limpa_item("i541-forma")
