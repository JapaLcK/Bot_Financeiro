"""DELETE /open-finance/{user_id} — a ROTA do disconnect, ponta a ponta.

Existia teste só para a função de db (`disconnect_open_finance_connection`,
tests/test_open_finance_mock.py); a rota — que ANTES disso deleta os items na
Pluggy via `delete_pluggy_items_best_effort` (síncrono desde o reset de conta,
chamado por `asyncio.to_thread`) — ficava sem cobertura. O refactor do helper
passou sem rede de proteção; estes dois fecham o buraco.

CONTROLE NEGATIVO do grupo (§3 do CLAUDE.md): remover a chamada
`delete_pluggy_items_best_effort` da rota → `test_disconnect_deleta_item_remoto_
e_local` vermelho (deletados == []). POSITIVO: `test_falha_remota_nao_impede_o_
disconnect_local` prova que o caminho legítimo (limpeza local) sobrevive à
Pluggy fora do ar — o contrato best-effort de antes do refactor.
"""

from __future__ import annotations

import os

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

os.environ.setdefault("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())

import core.observability as observability
import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.open_finance as of_routes
from db.connection import get_conn


def _auth(client: TestClient, user_id: int, email: str = "of-disc@t.com") -> dict:
    client.cookies.set(dashboard.AUTH_COOKIE_NAME, dashboard._make_jwt(user_id, email))
    client.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, dashboard.make_dashboard_token(user_id, hours=1))
    token = "test-csrf-token"
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, token)
    return {dashboard.CSRF_HEADER_NAME: token}


def _semeia_conexao_pluggy(user_id: int, item: str) -> None:
    """Conexão provider='pluggy' de verdade: a mock (`mock_pluggy`) fica fora
    do `list_pluggy_item_ids` e nunca alimentaria a limpeza remota."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into open_finance_connections "
            "(user_id, provider, provider_item_id, status, institution_id, institution_name) "
            "values (%s, 'pluggy', %s, 'UPDATED', '612', 'Nubank')",
            (user_id, item),
        )
        conn.commit()


def _conexoes(user_id: int) -> int:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select count(*) as n from open_finance_connections where user_id = %s",
            (user_id,),
        )
        n = cur.fetchone()["n"]
        conn.commit()
    return n


def test_disconnect_deleta_item_remoto_e_local(user_id, monkeypatch):
    item = f"disc-route-{user_id}"
    _semeia_conexao_pluggy(user_id, item)

    deletados: list[str] = []
    monkeypatch.setattr(of_routes, "create_pluggy_api_key", lambda: "api-key")
    monkeypatch.setattr(
        of_routes, "delete_pluggy_item",
        lambda item_id, api_key=None: deletados.append(item_id),
    )

    client = TestClient(dashboard.app)
    headers = _auth(client, user_id)
    resp = client.delete(f"/open-finance/{user_id}", headers=headers)

    assert resp.status_code == 200, resp.text
    assert deletados == [item], "a rota tinha que deletar o item na Pluggy antes do local"
    assert _conexoes(user_id) == 0


def test_falha_remota_nao_impede_o_disconnect_local(user_id, monkeypatch):
    _semeia_conexao_pluggy(user_id, f"disc-route2-{user_id}")

    def _pluggy_fora():
        raise RuntimeError("pluggy fora do ar")

    eventos: list[str] = []
    real_log = observability.log_system_event_sync
    monkeypatch.setattr(
        observability, "log_system_event_sync",
        lambda level, event_type, message, **kw: (eventos.append(event_type),
                                                  real_log(level, event_type, message, **kw)),
    )
    monkeypatch.setattr(of_routes, "create_pluggy_api_key", _pluggy_fora)

    client = TestClient(dashboard.app)
    headers = _auth(client, user_id)
    resp = client.delete(f"/open-finance/{user_id}", headers=headers)

    assert resp.status_code == 200, resp.text
    assert _conexoes(user_id) == 0, "falha remota (best-effort) não podia impedir o disconnect local"
    assert "pluggy_disconnect_auth_failed" in eventos, \
        f"o evento de log do best-effort sumiu no refactor: {eventos}"
