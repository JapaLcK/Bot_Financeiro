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


def test_disconnect_com_lock_de_reconexao_ocupado_recusa_com_503(user_id, monkeypatch):
    """Fecha o interleaving da rodada 5: o disconnect deletava a conexão SEM o
    lock do item, então entre a releitura do guard da reconexão
    (`_salva_item_sob_lock`) e o insert cabia um disconnect — e a conexão
    ressuscitava. Com o lock, o interleaving não existe mais: ou o disconnect
    espera a reconexão (janela de ms; aqui o teto estoura → 503 e NADA é
    tocado), ou completa antes e o guard responde 409
    (tests/test_account_reset.py).

    CONTROLE NEGATIVO: no código sem o lock, este teste fica vermelho
    (200, conexão deletada e Pluggy tocada por baixo do lock)."""
    from db.open_finance_state import pluggy_item_lock

    item = f"disc-route-lk-{user_id}"
    _semeia_conexao_pluggy(user_id, item)
    monkeypatch.setenv("OF_SYNC_LOCK_WAIT_MS", "100")

    tocada: list[str] = []
    monkeypatch.setattr(of_routes, "create_pluggy_api_key",
                        lambda: (tocada.append("auth") or "api-key"))
    monkeypatch.setattr(of_routes, "delete_pluggy_item",
                        lambda item_id, api_key=None: tocada.append(item_id))

    client = TestClient(dashboard.app)
    headers = _auth(client, user_id)
    with pluggy_item_lock(item) as segurei:  # a reconexão está com o lock
        assert segurei, "pré-condição: o teste precisa estar segurando o lock"
        resp = client.delete(f"/open-finance/{user_id}", headers=headers)

    assert resp.status_code == 503, resp.text
    assert _conexoes(user_id) == 1, "com o lock ocupado nada local podia ser deletado"
    assert tocada == [], "com o lock ocupado a Pluggy não podia ter sido tocada"


def test_item_salvo_durante_a_janela_do_disconnect_e_deletado_na_pluggy(user_id, monkeypatch):
    """Codex PR #217 (P2, 12º — irmão do 11º no reset): item salvo DEPOIS de a
    limpeza remota enumerar (T1) e ANTES do delete local (T2) era varrido do
    banco sem ser deletado na Pluggy — órfão que bloqueia reconexão. O 2º
    passe compara o varrido (RETURNING) com a enumeração e deleta o que ela
    não viu. A injeção vai na 2ª chamada de list_pluggy_item_ids: a 1ª é a
    dos locks (antes de T1), a 2ª é a enumeração do helper (T1).
    CONTROLE NEGATIVO: sem o 2º passe (código anterior), fica vermelho."""
    import db

    item_velho = f"disc-janela-{user_id}"
    item_novo = f"{item_velho}-tardio"
    _semeia_conexao_pluggy(user_id, item_velho)

    deletados: list[str] = []
    monkeypatch.setattr(of_routes, "create_pluggy_api_key", lambda: "api-key")
    monkeypatch.setattr(
        of_routes, "delete_pluggy_item",
        lambda item_id, api_key=None: deletados.append(item_id),
    )

    real_list = of_routes.list_pluggy_item_ids
    chamadas = {"n": 0}

    def _lista_e_injeta(uid):
        chamadas["n"] += 1
        itens = real_list(uid)
        if chamadas["n"] == 2:  # T1: enumeração dentro do helper
            db.save_pluggy_open_finance_item(
                uid, {"id": item_novo, "status": "UPDATED",
                      "connector": {"id": 613, "name": "Inter"}})
        return itens

    monkeypatch.setattr(of_routes, "list_pluggy_item_ids", _lista_e_injeta)

    client = TestClient(dashboard.app)
    headers = _auth(client, user_id)
    resp = client.delete(f"/open-finance/{user_id}", headers=headers)

    assert resp.status_code == 200, resp.text
    assert _conexoes(user_id) == 0, "o delete local tinha que varrer o item novo também"
    assert item_velho in deletados, "o item enumerado tinha que ser deletado no 1º passe"
    assert item_novo in deletados, \
        "item salvo na janela T1→T2 ficou órfão na Pluggy (bloqueia reconexão)"


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
