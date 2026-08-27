"""G6 — o /refresh conta o que aconteceu com CADA banco.

Fato medido: `refresh_and_sync_pluggy_user` engolia o 404 do PATCH e o do
`get_pluggy_item`, devolvia `still_updating: 0`, e o settings.html mostrava
"Tudo em dia!" para uma conexão que não existia mais na Pluggy. E o
`pluggy_sync_done` era logado em nível `info` mesmo com `ok:false` (397 true ×
41 false na mesma prateleira).

CONTROLE NEGATIVO: restaurar o `ok: True` fixo no retorno de
`refresh_and_sync_pluggy_user` (ou o log `info` incondicional em
`_run_pluggy_sync_bg`) deixa este grupo vermelho.
"""

from __future__ import annotations

import asyncio
import os

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

os.environ.setdefault("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())

import db
import core.services.pluggy_sync as ps
import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.open_finance as of_routes
from core.services.pluggy import PluggyApiError

ITEM_OK = {
    "id": "item-vivo", "status": "UPDATED", "executionStatus": "SUCCESS",
    "statusDetail": {"accounts": {"isUpdated": True, "lastUpdatedAt": "2026-08-20T11:00:00Z",
                                  "warnings": []}},
}


def _auth(client: TestClient, user_id: int) -> dict:
    client.cookies.set(dashboard.AUTH_COOKIE_NAME, dashboard._make_jwt(user_id, "of@t.com"))
    client.cookies.set(dashboard.DASHBOARD_COOKIE_NAME,
                       dashboard.make_dashboard_token(user_id, hours=1))
    token = "test-csrf-token"
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, token)
    return {dashboard.CSRF_HEADER_NAME: token, "Content-Type": "application/json"}


def _mundo_remoto(monkeypatch):
    """Um item vivo (Nubank) e um item que sumiu da Pluggy (Inter)."""
    def _get_item(item_id, api_key=None):
        if item_id == "item-sumiu":
            raise PluggyApiError("nao existe", status_code=404)
        return ITEM_OK

    monkeypatch.setattr(ps, "create_pluggy_api_key", lambda: "k")
    monkeypatch.setattr(ps, "get_pluggy_item", _get_item)
    monkeypatch.setattr(ps, "update_pluggy_item", lambda i, k=None: _get_item(i, k))
    monkeypatch.setattr(ps, "list_pluggy_accounts", lambda i, k=None: [
        {"id": f"acc-{i}", "name": "Conta", "type": "BANK", "currencyCode": "BRL",
         "balance": "10.00"}])
    monkeypatch.setattr(ps, "list_pluggy_transactions", lambda acc, k=None, **kw: [])
    monkeypatch.setattr(ps, "list_pluggy_investments", lambda i, k=None: [])
    monkeypatch.setattr(ps, "_hold_aggregate_emails", lambda uid, origem: None)


def test_refresh_com_item_sumido_nao_diz_que_esta_tudo_em_dia(user_id, monkeypatch):
    db.save_pluggy_open_finance_item(user_id, {"id": "item-vivo", "status": "UPDATED",
                                               "connector": {"id": 612, "name": "Nubank"}})
    db.save_pluggy_open_finance_item(user_id, {"id": "item-sumiu", "status": "UPDATED",
                                               "connector": {"id": 77, "name": "Inter"}})
    _mundo_remoto(monkeypatch)

    eventos: list[dict] = []

    async def _log(level, event_type, message, **kw):
        eventos.append({"level": level, "event": event_type, **kw})

    monkeypatch.setattr(of_routes, "log_system_event", _log)

    client = TestClient(dashboard.app)
    resp = client.post(f"/open-finance/{user_id}/refresh?wait=0", headers=_auth(client, user_id))

    assert resp.status_code == 200, resp.text
    sync = resp.json()["sync"]
    assert sync["ok"] is False, "um item perdido não pode virar 'tudo em dia'"

    por_item = {i["item_id"]: i for i in sync["items"]}
    assert por_item["item-sumiu"]["state"] == "item_missing"
    assert por_item["item-sumiu"]["institution"] == "Inter"
    assert por_item["item-sumiu"]["reason"] == "item_missing"
    assert por_item["item-vivo"]["state"] == "updated"
    assert por_item["item-vivo"]["products"] == {"BANK": "updated"}
    assert isinstance(sync["duration_ms"], int)

    # o clique fica registrado com o usuário ANONIMIZADO e sem segredo nenhum
    clique = [e for e in eventos if e["event"] == "of_manual_refresh"]
    assert len(clique) == 1, eventos
    detalhes = clique[0]["details"]
    assert clique[0]["level"] == "warning"
    assert detalhes["ok"] is False
    assert isinstance(detalhes["duration_ms"], int)
    assert str(user_id) not in str(detalhes), "o id do usuário não pode aparecer em claro"
    assert len(detalhes["user_hash"]) == 16
    assert {i["item_id"] for i in detalhes["items"]} == {"item-vivo", "item-sumiu"}
    texto = str(detalhes).lower()
    for proibido in ("token", "apikey", "secret", "balance", "valor"):
        assert proibido not in texto, f"'{proibido}' vazou no evento: {detalhes}"


def test_sync_sem_sucesso_e_logado_como_warning_com_motivo(monkeypatch):
    eventos: list[dict] = []

    async def _log(level, event_type, message, **kw):
        eventos.append({"level": level, "event": event_type, **kw})

    monkeypatch.setattr(of_routes, "log_system_event", _log)
    monkeypatch.setattr(of_routes, "sync_pluggy_item",
                        lambda item_id: {"ok": False, "reason": "no_accounts",
                                         "item_id": item_id})

    asyncio.run(of_routes._run_pluggy_sync_bg("item-x"))

    assert len(eventos) == 1
    assert eventos[0]["level"] == "warning", "ok:false não pode ser logado como info"
    assert eventos[0]["details"]["reason"] == "no_accounts"


def test_sync_com_item_perdido_e_logado_como_error(monkeypatch):
    eventos: list[dict] = []

    async def _log(level, event_type, message, **kw):
        eventos.append({"level": level, "event": event_type, **kw})

    monkeypatch.setattr(of_routes, "log_system_event", _log)
    monkeypatch.setattr(of_routes, "sync_pluggy_item",
                        lambda item_id: {"ok": False, "reason": "item_missing",
                                         "item_id": item_id})

    asyncio.run(of_routes._run_pluggy_sync_bg("item-x"))

    assert eventos[0]["level"] == "error"
    assert eventos[0]["event"] == "of_item_missing"


# ── RODADA 3: relatório coerente e refresh manual que ainda sincroniza ───────

def test_label_acompanha_o_state_sobreposto(user_id, monkeypatch):
    """`state` virava `partial`/`rate_limited` e `label` continuava "Atualizado".
    Ninguém consome hoje — é armadilha para o próximo."""
    _conexao_saudavel(user_id, "item-label")
    monkeypatch.setattr(ps, "get_connections_by_item_id", db.get_connections_by_item_id)

    out = ps._refresh_items_report(
        ["item-label"], {"item-label": "Nubank"},
        {"item-label": False}, {}, set(), rate_limited=set())
    assert (out[0]["state"], out[0]["label"]) == ("partial", "Parcial"), out

    out = ps._refresh_items_report(
        ["item-label"], {"item-label": "Nubank"},
        {}, {}, set(), rate_limited={"item-label"})
    assert out[0]["state"] == "rate_limited"
    assert out[0]["label"] == "Atualizado" and out[0]["detail"] == "Atualizado há pouco"


def test_detalhe_do_parcial_de_verdade_nao_e_sobrescrito(user_id, monkeypatch):
    """CONTROLE POSITIVO: `partial` vindo do `connection_ui_state` mantém o
    detalhe dele ("Cartão desatualizado desde 12/08"), que diz muito mais."""
    conexao = db.save_pluggy_open_finance_item(user_id, {
        "id": "item-parcial-real", "status": "UPDATED",
        "connector": {"id": 612, "name": "Nubank"}})
    db.mark_sync_result(conexao["id"], ok=True, status="ACTIVE", status_reason="", health={
        "observed_at": "2026-08-20T12:00:00-03:00", "item_status": "UPDATED",
        "execution_status": "PARTIAL_SUCCESS", "stale_products": ["CREDIT"],
        "products": {"CREDIT": {"updated": False, "last_updated_at": "2026-08-12T03:10:00Z",
                                "warnings": []}}})
    monkeypatch.setattr(ps, "get_connections_by_item_id", db.get_connections_by_item_id)

    out = ps._refresh_items_report(["item-parcial-real"], {}, {}, {}, set())
    assert out[0]["state"] == "partial"
    assert out[0]["detail"] == "Cartão desatualizado desde 12/08", out[0]


def test_manual_dentro_do_cooldown_ainda_sincroniza(user_id, monkeypatch):
    """O cooldown do manual protege a cota de COLETA (o PATCH). Reler o que a
    Pluggy já tem é GET — e era exatamente o que o usuário queria ao apertar o
    botão. Antes: early-return sem PATCH E SEM SYNC, com "tudo em dia"."""
    _conexao_saudavel(user_id, "item-cooldown")
    _mundo_remoto(monkeypatch)
    patches: list[str] = []
    monkeypatch.setattr(ps, "update_pluggy_item", lambda i, k=None: patches.append(i))
    monkeypatch.setattr(ps, "claim_manual_refresh", lambda *a, **kw: [])
    syncs: list[int] = []
    monkeypatch.setattr(ps, "sync_pluggy_user",
                        lambda uid: syncs.append(uid) or {"ok": True, "results": []})

    saida = ps.refresh_and_sync_pluggy_user(user_id, wait_seconds=0)

    assert patches == [], "dentro do cooldown NÃO se pede coleta nova"
    assert syncs == [user_id], "mas o que a Pluggy já tem tem que ser lido"
    assert saida["refreshed"] == 0
    assert [i["state"] for i in saida["items"]] == ["rate_limited"]


def _conexao_saudavel(user_id: int, item_id: str) -> dict:
    """Conexão que JÁ sincronizou com sucesso — sem isso o estado é "Atualizando…"
    ("Ainda não sincronizou") e nada é sobreposto, que é o comportamento certo."""
    conexao = db.save_pluggy_open_finance_item(user_id, {
        "id": item_id, "status": "UPDATED", "connector": {"id": 612, "name": "Nubank"}})
    db.mark_sync_result(conexao["id"], ok=True, status="ACTIVE", status_reason="", health={
        "observed_at": "2026-08-20T12:00:00-03:00", "item_status": "UPDATED",
        "execution_status": "SUCCESS", "products": {}, "stale_products": []})
    return conexao


# ── RODADA FINAL: um banco com 429 não pode derrubar o /refresh dos outros ────

def test_um_item_com_429_nao_derruba_o_refresh_dos_demais(user_id, monkeypatch):
    """Fato medido antes do conserto, por esta MESMA rota: `GET /items` devolvendo
    429 num banco subia até a rota (o re-raise de `sync_pluggy_item` é o que faz o
    webhook retentar) e virava `502 {"detail":"rate"}` — sem relatório por item,
    sem `of_manual_refresh` no log, e o banco saudável do mesmo usuário com
    `last_sync_at: None`.

    CONTROLE NEGATIVO: trocar `_sync_item_contido` de volta por
    `sync_pluggy_item` direto no lote (`sync_pluggy_user`) deixa este teste
    vermelho já no `status_code == 200`.
    """
    db.save_pluggy_open_finance_item(user_id, {"id": "i-ok", "status": "UPDATED",
                                               "connector": {"id": 612, "name": "Nubank"}})
    # o item do 429 JÁ tinha sincronizado com sucesso — é essa conexão que dizia
    # "Atualizado" enquanto a leitura de hoje falhava.
    _conexao_saudavel(user_id, "i-429")
    _mundo_remoto(monkeypatch)

    def _get_item(item_id, api_key=None):
        if item_id == "i-429":
            raise PluggyApiError("rate", status_code=429)
        return ITEM_OK

    monkeypatch.setattr(ps, "get_pluggy_item", _get_item)
    monkeypatch.setattr(ps, "update_pluggy_item", lambda i, k=None: None)

    eventos: list[dict] = []

    async def _log(level, event_type, message, **kw):
        eventos.append({"level": level, "event": event_type, **kw})

    monkeypatch.setattr(of_routes, "log_system_event", _log)

    client = TestClient(dashboard.app)
    resp = client.post(f"/open-finance/{user_id}/refresh?wait=0", headers=_auth(client, user_id))

    assert resp.status_code == 200, resp.text
    sync = resp.json()["sync"]
    por_item = {i["item_id"]: i for i in sync["items"]}

    # 1. o banco saudável sincronizou de verdade (o 429 do vizinho não o alcançou)
    assert por_item["i-ok"]["state"] == "updated", por_item["i-ok"]
    assert db.get_connections_by_item_id("i-ok")[0]["last_sync_at"] is not None

    # 2. o que falhou volta como resultado DELE, com motivo próprio e sem verde
    assert por_item["i-429"]["state"] == "error_recoverable", por_item["i-429"]
    assert por_item["i-429"]["reason"] == "read_failed"
    assert sync["ok"] is False, "um item que não deu para ler não é 'tudo em dia'"

    # 3. a observabilidade do clique sai SEMPRE — inclusive quando um item falha
    clique = [e for e in eventos if e["event"] == "of_manual_refresh"]
    assert len(clique) == 1, eventos
    assert clique[0]["level"] == "warning"
    assert {i["item_id"] for i in clique[0]["details"]["items"]} == {"i-ok", "i-429"}


def test_falha_de_um_item_nao_impede_o_sync_do_outro_no_lote(user_id, monkeypatch):
    """CONTROLE POSITIVO do mesmo conserto no nível do lote: a contenção não pode
    engolir o caminho legítimo — o item bom continua devolvendo `ok: True` com as
    contas que leu, e só o item ruim vira `read_failed`."""
    db.save_pluggy_open_finance_item(user_id, {"id": "i-ok", "status": "UPDATED",
                                               "connector": {"id": 612, "name": "Nubank"}})
    db.save_pluggy_open_finance_item(user_id, {"id": "i-boom", "status": "UPDATED",
                                               "connector": {"id": 77, "name": "Inter"}})
    _mundo_remoto(monkeypatch)

    def _get_item(item_id, api_key=None):
        if item_id == "i-boom":
            raise RuntimeError("qualquer erro, não só HTTP")
        return ITEM_OK

    monkeypatch.setattr(ps, "get_pluggy_item", _get_item)

    saida = ps.sync_pluggy_user(user_id)
    por_item = {r["item_id"]: r for r in saida["results"]}

    assert por_item["i-ok"]["ok"] is True
    assert por_item["i-ok"]["accounts_synced"] == 1
    assert por_item["i-boom"] == {"ok": False, "reason": "read_failed", "item_id": "i-boom",
                                  "connection_id": por_item["i-boom"]["connection_id"],
                                  "error": "RuntimeError"}
