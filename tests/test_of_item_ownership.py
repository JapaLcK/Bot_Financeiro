"""G5 — de quem é o item.

Fatos que originaram o grupo: `open_finance_connections` é única em
(user_id, provider, provider_item_id) — ou seja, DOIS usuários podem ter o mesmo
item —, `get_open_finance_connection_by_item_id` fazia `limit 1` SEM `order by`
(sorteava o dono) e o `POST /pluggy-item` aceitava o dict `item` cru do navegador.

CONTROLE NEGATIVO do grupo:
  • remover a comparação `clientUserId == session_uid` do /pluggy-item
    → `test_item_de_outro_client_user_id_e_recusado` vermelho;
  • voltar o `limit 1` em `get_connections_by_item_id`
    → `test_webhook_de_item_com_dois_donos_recusa_sync` vermelho.
"""

from __future__ import annotations

import json
import os

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

os.environ.setdefault("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())

import db
import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.open_finance as of_routes
from db.connection import get_conn

SEGREDO = "test-webhook-secret"


@pytest.fixture()
def eventos(monkeypatch):
    """Captura os `log_system_event` da rota (a versão real grava no banco)."""
    capturados: list[dict] = []

    async def _log(level, event_type, message, **kw):
        capturados.append({"level": level, "event": event_type, **kw})

    monkeypatch.setattr(of_routes, "log_system_event", _log)
    return capturados


@pytest.fixture()
def sem_indice_unico():
    """Deixa o item aparecer em duas conexões — o estado que o código precisa tratar.

    O índice `uq_of_conn_provider_item` é criado dentro de um bloco que só emite
    WARNING se falhar (db/schema.py): em produção, se já houver duplicata, ele NÃO
    existe. Este teste reproduz exatamente esse banco.
    """
    with get_conn() as c:
        c.execute("drop index if exists uq_of_conn_provider_item")
        c.commit()
    yield
    with get_conn() as c:
        try:
            c.execute("create unique index if not exists uq_of_conn_provider_item "
                      "on open_finance_connections(provider, provider_item_id)")
            c.commit()
        except Exception:
            c.rollback()


def _auth(client: TestClient, user_id: int, email: str = "of@t.com") -> dict:
    client.cookies.set(dashboard.AUTH_COOKIE_NAME, dashboard._make_jwt(user_id, email))
    client.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, dashboard.make_dashboard_token(user_id, hours=1))
    token = "test-csrf-token"
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, token)
    return {dashboard.CSRF_HEADER_NAME: token, "Content-Type": "application/json"}


def _item_remoto(item_id: str, client_user_id) -> dict:
    return {"id": item_id, "status": "UPDATED", "clientUserId": str(client_user_id),
            "connector": {"id": 612, "name": "Nubank"}}


# ── 25. clientUserId ≠ usuário da sessão ─────────────────────────────────────

def test_item_de_outro_client_user_id_e_recusado(user_id, monkeypatch, eventos):
    monkeypatch.setattr(of_routes, "get_pluggy_item",
                        lambda item_id, api_key=None: _item_remoto(item_id, 999_999_999))
    client = TestClient(dashboard.app)
    headers = _auth(client, user_id)

    resp = client.post(f"/open-finance/{user_id}/pluggy-item",
                       json={"item": {"id": "item-alheio"}}, headers=headers)

    assert resp.status_code == 403, resp.text
    assert db.get_connections_by_item_id("item-alheio") == [], "nada podia ter sido gravado"
    assert any(e["event"] == "of_item_owner_conflict" for e in eventos), eventos


def test_o_que_e_gravado_e_o_item_REMOTO_nao_o_do_navegador(user_id, monkeypatch, eventos):
    monkeypatch.setattr(of_routes, "get_pluggy_item",
                        lambda item_id, api_key=None: _item_remoto(item_id, user_id))
    monkeypatch.setattr(of_routes, "_schedule_pluggy_sync", lambda item_id: None)
    client = TestClient(dashboard.app)
    headers = _auth(client, user_id)

    # o navegador mente o nome do banco; o servidor usa o que a Pluggy respondeu
    resp = client.post(
        f"/open-finance/{user_id}/pluggy-item",
        json={"item": {"id": "item-meu", "connector": {"id": 1, "name": "Banco Falso"}}},
        headers=headers,
    )

    assert resp.status_code == 200, resp.text
    linha = db.get_connections_by_item_id("item-meu")[0]
    assert linha["institution_name"] == "Nubank"
    assert int(linha["user_id"]) == user_id


# ── 26. item já vinculado a outra conta ──────────────────────────────────────

def test_item_de_outra_conta_local_devolve_409(user_id, monkeypatch, eventos):
    outro = user_id + 1
    db.ensure_user(outro)
    try:
        db.save_pluggy_open_finance_item(
            outro, {"id": "item-disputado", "status": "UPDATED",
                    "connector": {"id": 612, "name": "Nubank"}})

        # a Pluggy diz que o dono é o usuário da sessão, mas alguém já registrou aqui
        monkeypatch.setattr(of_routes, "get_pluggy_item",
                            lambda item_id, api_key=None: _item_remoto(item_id, user_id))
        client = TestClient(dashboard.app)
        headers = _auth(client, user_id)

        resp = client.post(f"/open-finance/{user_id}/pluggy-item",
                           json={"item": {"id": "item-disputado"}}, headers=headers)

        assert resp.status_code == 409, resp.text
        linhas = db.get_connections_by_item_id("item-disputado")
        assert len(linhas) == 1 and int(linhas[0]["user_id"]) == outro, "o dono original ficou intacto"
        assert any(e["event"] == "of_item_owner_conflict" and e["level"] == "error"
                   for e in eventos), eventos
    finally:
        db.disconnect_open_finance_connection(outro)
        with get_conn() as c:
            c.execute("delete from users where id=%s", (outro,))
            c.commit()


# ── 27 e 28. webhook ─────────────────────────────────────────────────────────

def _webhook(client: TestClient, evento: str, item_id: str):
    return client.post(
        f"/open-finance/pluggy/webhook?token={SEGREDO}",
        content=json.dumps({"event": evento, "itemId": item_id}).encode(),
        headers={"Content-Type": "application/json"},
    )


def test_webhook_de_item_com_dois_donos_recusa_sync(user_id, monkeypatch, eventos, sem_indice_unico):
    outro = user_id + 1
    db.ensure_user(outro)
    try:
        for uid in (user_id, outro):
            db.save_pluggy_open_finance_item(
                uid, {"id": "item-2donos", "status": "UPDATED",
                      "connector": {"id": 612, "name": "Nubank"}})
        assert len(db.get_connections_by_item_id("item-2donos")) == 2

        agendados = []
        monkeypatch.setenv("PLUGGY_WEBHOOK_SECRET", SEGREDO)
        monkeypatch.setattr(of_routes, "_schedule_pluggy_sync", lambda i: agendados.append(i))

        resp = _webhook(TestClient(dashboard.app), "transactions/created", "item-2donos")

        assert resp.status_code == 200, resp.text
        assert agendados == [], "sincronizar um dos dois é sincronizar a carteira errada"
        assert any(e["event"] == "of_item_owner_conflict" and e["level"] == "error"
                   for e in eventos), eventos
    finally:
        for uid in (outro,):
            db.disconnect_open_finance_connection(uid)
            with get_conn() as c:
                c.execute("delete from users where id=%s", (uid,))
                c.commit()


def test_webhook_de_item_desconhecido_registra_e_nao_sincroniza(monkeypatch, eventos):
    agendados = []
    monkeypatch.setenv("PLUGGY_WEBHOOK_SECRET", SEGREDO)
    monkeypatch.setattr(of_routes, "_schedule_pluggy_sync", lambda i: agendados.append(i))

    resp = _webhook(TestClient(dashboard.app), "item/updated", "item-fantasma")

    assert resp.status_code == 200, resp.text
    assert agendados == []
    assert any(e["event"] == "of_webhook_item_unknown" for e in eventos), eventos

    with get_conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "select origin, user_id from open_finance_item_registry where provider_item_id=%s",
                ("item-fantasma",))
            linhas = cur.fetchall()
            cur.execute("delete from open_finance_item_registry where provider_item_id=%s",
                        ("item-fantasma",))
        c.commit()
    assert [l["origin"] for l in linhas] == ["webhook"]
    assert linhas[0]["user_id"] is None, "item desconhecido não tem dono a atribuir"
