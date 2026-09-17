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
import core.services.pluggy_sync as ps
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
        # `origin` separa este 409 do 409 de `_salva_item_sob_lock`, que tem
        # `detail` IDÊNTICO: sem o campo, o log não responde "houve corrida?".
        assert [(e["level"], e["details"].get("origin")) for e in eventos
                if e["event"] == "of_item_owner_conflict"] == [("error", "pluggy_item_route")], eventos
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


# ── 29, 30 e 31. isolamento em `_refresh_items_report` e `_sync_item_contido`
# (#446) — o mesmo `item_id` pode estar ligado a conexões de usuários
# diferentes (sem o índice único, ou por webhook), e as duas funções faziam a
# leitura por item_id SEM filtrar por quem está pedindo.
#
# CONTROLE NEGATIVO do grupo: tirar o `if int(r["user_id"]) == int(user_id)`
# de `_refresh_items_report` (core/services/pluggy_sync.py) deixa os DOIS
# primeiros vermelhos — medido. O terceiro (`test_sync_pluggy_user_nao_devolve_
# dono_alheio`) não passa por `_refresh_items_report`; quem o prende é o
# filtro de `_sync_item_contido`, no mesmo arquivo.

def _health_parcial() -> dict:
    return {
        "observed_at": "2026-09-16T12:00:00-03:00", "item_status": "UPDATED",
        "execution_status": "PARTIAL_SUCCESS", "stale_products": ["CREDIT"],
        "products": {"CREDIT": {"updated": False, "last_updated_at": "2026-08-12T03:10:00Z",
                                "warnings": []}},
    }


def test_refresh_report_nao_traz_health_de_conexao_de_outro_usuario(user_id, monkeypatch):
    """Só o OUTRO usuário tem a linha do item: o relatório do MEU refresh não
    pode trazer o `health` dele — `products` tem que voltar vazio."""
    outro = user_id + 1
    db.ensure_user(outro)
    try:
        conexao_outro = db.save_pluggy_open_finance_item(outro, {
            "id": "item-so-do-outro", "status": "UPDATED",
            "connector": {"id": 612, "name": "Nubank"}})
        db.mark_sync_result(conexao_outro["id"], ok=True, status="ACTIVE", status_reason="",
                            health=_health_parcial())
        monkeypatch.setattr(ps, "get_connections_by_item_id", db.get_connections_by_item_id)

        out = ps._refresh_items_report(user_id, ["item-so-do-outro"], {}, {}, {}, set())

        assert out[0]["products"] == {}, f"vazou health de outro usuário: {out[0]}"
        assert out[0]["state"] != "partial", out[0]
    finally:
        db.disconnect_open_finance_connection(outro)
        with get_conn() as c:
            c.execute("delete from users where id=%s", (outro,))
            c.commit()


def test_refresh_report_com_dois_donos_devolve_a_PROPRIA_linha(user_id, monkeypatch, sem_indice_unico):
    """CONTROLE POSITIVO: com duas conexões do mesmo item (índice derrubado), o
    requerente recebe a PRÓPRIA linha — hoje (sem filtro) `len(rows) == 2` cai
    no `row = {}` do `_refresh_items_report`, mesmo a minha linha existindo."""
    outro = user_id + 1
    db.ensure_user(outro)
    try:
        conexao_minha = db.save_pluggy_open_finance_item(user_id, {
            "id": "item-2donos-refresh", "status": "UPDATED",
            "connector": {"id": 612, "name": "Nubank"}})
        db.save_pluggy_open_finance_item(outro, {
            "id": "item-2donos-refresh", "status": "UPDATED",
            "connector": {"id": 612, "name": "Nubank"}})
        assert len(db.get_connections_by_item_id("item-2donos-refresh")) == 2
        db.mark_sync_result(conexao_minha["id"], ok=True, status="ACTIVE", status_reason="",
                            health=_health_parcial())
        monkeypatch.setattr(ps, "get_connections_by_item_id", db.get_connections_by_item_id)

        out = ps._refresh_items_report(user_id, ["item-2donos-refresh"], {}, {}, {}, set())

        assert out[0]["state"] == "partial", (
            f"esperava a MINHA linha (partial); {out[0]}")
    finally:
        db.disconnect_open_finance_connection(outro)
        with get_conn() as c:
            c.execute("delete from users where id=%s", (outro,))
            c.commit()


def test_sync_pluggy_user_nao_devolve_dono_alheio(user_id, monkeypatch):
    """`sync_pluggy_item` faz a PRÓPRIA busca por item_id (sem filtro de
    usuário). Se a minha conexão sumiu do banco e o item hoje só existe para
    OUTRO usuário, o lote não pode devolver `user_id`/`connection_id` alheios —
    passa pelo caminho real, `sync_pluggy_user`."""
    outro = user_id + 1
    db.ensure_user(outro)
    try:
        db.save_pluggy_open_finance_item(outro, {
            "id": "item-vazamento-2", "status": "UPDATED",
            "connector": {"id": 612, "name": "Nubank"}})

        # snapshot "velho": ainda enxerga a MINHA conexão com este item, embora
        # ela já não exista mais no banco (apagada / nunca existiu de verdade
        # nesta rodada) — é o estado que a corrida descrita na issue produz.
        monkeypatch.setattr(ps, "get_open_finance_snapshot", lambda uid: {
            "connections": [{"provider": "pluggy", "provider_item_id": "item-vazamento-2"}]})
        monkeypatch.setattr(ps, "_hold_aggregate_emails", lambda *a, **kw: None)
        # a busca REAL de `sync_pluggy_item` (por item_id, sem filtro de
        # usuário) acharia hoje só a conexão do `outro` — é isso que o mock
        # reproduz, sem precisar da Pluggy de verdade.
        monkeypatch.setattr(ps, "sync_pluggy_item", lambda item_id: {
            "ok": True, "item_id": item_id, "user_id": outro,
            "connection_id": 999999, "accounts_synced": 3})

        resultado = ps.sync_pluggy_user(user_id)

        assert resultado["results"] == [
            {"ok": False, "reason": "connection_not_found", "item_id": "item-vazamento-2"}], (
            f"vazou dado de outro usuário: {resultado['results']}")
        assert resultado["accounts_synced"] == 0
    finally:
        db.disconnect_open_finance_connection(outro)
        with get_conn() as c:
            c.execute("delete from users where id=%s", (outro,))
            c.commit()


def test_sync_pluggy_user_nao_traz_reason_de_conexao_paused_alheia(user_id, monkeypatch):
    """Corrida: a MINHA conexão real sumiu (ou nunca existiu) e hoje o item só
    existe, PAUSADO, para o OUTRO usuário. Só o `user_id` no retorno de
    `sync_pluggy_item` permite ao `_sync_item_contido` filtrar.
    CONTROLE NEGATIVO: tirar `"user_id": connection["user_id"]` dos dois
    `return` de `sync_pluggy_item` deixa este vermelho, com
    `reason == "connection_paused"` no resultado."""
    outro = user_id + 1
    db.ensure_user(outro)
    try:
        conexao_outro = db.save_pluggy_open_finance_item(outro, {
            "id": "item-paused-alheio", "status": "UPDATED",
            "connector": {"id": 612, "name": "Nubank"}})
        db.pause_open_finance_connection(conexao_outro["id"])
        monkeypatch.setattr(ps, "get_open_finance_snapshot", lambda uid: {
            "connections": [{"provider": "pluggy", "provider_item_id": "item-paused-alheio"}]})
        monkeypatch.setattr(ps, "_hold_aggregate_emails", lambda *a, **kw: None)
        resultado = ps.sync_pluggy_user(user_id)

        assert resultado["results"] == [
            {"ok": False, "reason": "connection_not_found", "item_id": "item-paused-alheio"}], (
            f"vazou reason de conexão alheia: {resultado['results']}")
    finally:
        db.disconnect_open_finance_connection(outro)
        with get_conn() as c:
            c.execute("delete from users where id=%s", (outro,))
            c.commit()
