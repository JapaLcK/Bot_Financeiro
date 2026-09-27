"""Issue #541 — log de Open Finance que sabe o dono grava o dono na COLUNA.

`system_event_logs.user_id` é o que a exportação (`build_user_export_zip`) e a
exclusão de conta (`delete_user_data` + cascata) enxergam. Com a coluna
preenchida, texto cru de exceção (host/porta do psycopg) sai na exportação; com
ela NULL, uid no texto sobrevive à exclusão. Um caso por ponto, pela porta real;
a contraprova com o log REAL mora em `tests/test_of_log_dono_exportacao.py`.

CONTROLES (CLAUDE.md §3): tirar o `user_id=`/`extra=` de cada ponto, tirar o
rastro em stderr do `_log_com_teto`, ou voltar `str(exc)`, deixa o respectivo
caso vermelho. CLASSE CEGA: log NOVO
com uid no texto — só uma varredura (AST) pegaria.
"""
from __future__ import annotations

import asyncio
import time

import psycopg
import pytest
from fastapi.testclient import TestClient

# Importado ANTES de qualquer `coletor`: o `wa_client` faz `from core.observability
# import log_system_event_sync` no import, e se o 1º import acontecer com o
# `coletor` ativo ele guarda o dublê para o resto da sessão (medido: quebrava
# `test_send_template_real_recebe_parametro_nomeado` rodando depois deste arquivo).
import adapters.whatsapp.wa_client  # noqa: F401
import core.observability as observability
import db
import db.open_finance as of_mod
import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.open_finance as of_routes
from db.connection import get_conn
from conftest import promote_to_pro
from core.services import plan_service
from tests._fusao_of_helpers import ia_fora, manda, tx, uid_pro  # noqa: F401 — fixtures
from tests.test_reconciliacao_manual_depois_do_import import _banco
from utils_date import today_tz
from test_account_reset import SENHA, _semeia, _zera_rate_limit  # noqa: F401
from test_account_reset import _auth as _auth_reset
from test_log_falha_user_id import coletor  # noqa: F401 — fixture
from test_of_item_ownership import _auth, _webhook, eventos  # noqa: F401
from test_of_webhook_adopt_guards import _limpa_item, _mock_item, webhook_pluggy  # noqa: F401
from test_open_finance_disconnect_route import _semeia_conexao_pluggy
from test_open_finance_proactive_dedupe import _CASOS, _armar, _espiao, _event_logs  # noqa: F401

HOST = 'connection to server at "db.interno" (10.9.8.7), port 5432 failed'
_PEDACOS_DO_HOST = ("db.interno", "10.9.8.7", "5432")


def _host_fora(*textos) -> None:
    junto = " ".join(str(t) for t in textos)
    for pedaco in _PEDACOS_DO_HOST:
        assert pedaco not in junto, f"texto cru da exceção vazou ({pedaco}): {junto}"


def _levanta(exc):
    def _f(*a, **k):
        raise exc
    return _f


def _de(eventos, nome):
    return [e for e in eventos if e["event"] == nome]


@pytest.fixture()
def sync_log(monkeypatch):
    """`log_system_event_sync` capturado por kwargs (settings e disconnect)."""
    vistos: list[dict] = []
    monkeypatch.setattr(observability, "log_system_event_sync",
                        lambda level, event_type=None, message=None, **kw:
                        vistos.append({"event": event_type, "message": message, **kw}))
    return vistos


# ── adoção pelo webhook (1293 e 1370) ────────────────────────────────────────

def test_adocao_com_falha_de_infra_grava_o_dono_na_coluna_sem_host(
        user_id, monkeypatch, eventos, webhook_pluggy):
    promote_to_pro(user_id)
    _mock_item(monkeypatch, user_id)
    monkeypatch.setattr(of_routes, "register_item", _levanta(psycopg.OperationalError(HOST)))
    try:
        assert _webhook(TestClient(dashboard.app), "item/created", "i541-infra").status_code == 200
        skip = _de(eventos, "of_webhook_adopt_skipped")
        assert len(skip) == 1, eventos
        assert skip[0]["user_id"] == user_id, skip
        assert "user_id" not in skip[0]["details"], skip
        assert skip[0]["details"]["motivo"] == "OperationalError", skip
        assert "error" not in skip[0]["details"], "str(exc) de psycopg com dono na coluna"
        _host_fora(skip[0]["details"])
    finally:
        _limpa_item("i541-infra")


def test_adocao_feita_com_auditoria_falhando_grava_o_dono_na_coluna(
        user_id, monkeypatch, eventos, webhook_pluggy):
    promote_to_pro(user_id)
    _mock_item(monkeypatch, user_id)
    monkeypatch.setattr(of_routes, "record_audit_event", _levanta(psycopg.OperationalError(HOST)))
    try:
        assert _webhook(TestClient(dashboard.app), "item/created", "i541-inc").status_code == 200
        assert len(db.get_connections_by_item_id("i541-inc")) == 1, "a adoção tinha de acontecer"
        inc = _de(eventos, "of_webhook_adopt_incompleto")
        assert len(inc) == 1, eventos
        assert inc[0]["user_id"] == user_id, inc
        assert "user_id" not in inc[0]["details"], inc
        _host_fora(inc[0]["details"])
    finally:
        db.disconnect_open_finance_connection(user_id)
        _limpa_item("i541-inc")


# ── posse no POST /pluggy-item (1731 e 1746) ─────────────────────────────────

def test_item_de_outro_client_user_id_loga_o_uid_da_sessao(user_id, monkeypatch, eventos):
    promote_to_pro(user_id)
    _mock_item(monkeypatch, 999_999_999)
    client = TestClient(dashboard.app)
    r = client.post(f"/open-finance/{user_id}/pluggy-item",
                    json={"item": {"id": "i541-alheio"}}, headers=_auth(client, user_id))
    assert r.status_code == 403, r.text
    conflito = _de(eventos, "of_item_owner_conflict")
    assert [e["user_id"] for e in conflito] == [user_id], conflito


def test_item_de_outra_conta_local_loga_o_uid_da_sessao_sem_o_outro(
        user_id, monkeypatch, eventos):
    promote_to_pro(user_id)
    outro = user_id + 1
    db.ensure_user(outro)
    try:
        db.save_pluggy_open_finance_item(outro, {"id": "i541-disputa", "status": "UPDATED",
                                                 "connector": {"id": 612, "name": "Nubank"}})
        _mock_item(monkeypatch, user_id)
        client = TestClient(dashboard.app)
        r = client.post(f"/open-finance/{user_id}/pluggy-item",
                        json={"item": {"id": "i541-disputa"}}, headers=_auth(client, user_id))
        assert r.status_code == 409, r.text
        conflito = _de(eventos, "of_item_owner_conflict")
        assert [e["user_id"] for e in conflito] == [user_id], conflito
    finally:
        db.disconnect_open_finance_connection(outro)
        _limpa_item("i541-disputa")


# ── resultado do sync em background (871 e 883) ──────────────────────────────

def test_sync_em_background_grava_o_dono_na_coluna(user_id, monkeypatch, eventos):
    monkeypatch.setattr(of_routes, "sync_pluggy_item", lambda item_id: {
        "ok": True, "user_id": user_id, "stale_products": ["INVESTMENTS"]})
    asyncio.run(of_routes._run_pluggy_sync_bg("i541-sync"))

    por_evento = {e["event"]: e for e in eventos}
    for nome in ("of_product_stale", "pluggy_sync_done"):
        assert por_evento[nome]["user_id"] == user_id, (nome, eventos)
        assert "user_id" not in por_evento[nome]["details"], (nome, eventos)
    assert por_evento["pluggy_sync_done"]["details"]["stale_products"] == ["INVESTMENTS"]


def test_sync_que_cai_em_fk_real_nao_grava_uid_com_coluna_nula(monkeypatch):
    # A FK real põe `Key (user_id)=(…)` no texto; coluna NULL sobrevive à exclusão.
    alheio, vistos = 987654321955, []
    with get_conn() as c:
        try:
            c.execute("insert into pockets (user_id, name) values (%s, 'x')", (alheio,))
        except psycopg.errors.ForeignKeyViolation as e:
            c.rollback()
            fk = e
    assert str(alheio) in str(fk), "pré-condição: o texto cru traz o uid"
    monkeypatch.setattr(of_routes, "sync_pluggy_item", _levanta(fk))

    async def _log(level, event_type, message, **kw):
        vistos.append({"event": event_type, "message": message, **kw})
    monkeypatch.setattr(of_routes, "log_system_event", _log)
    asyncio.run(of_routes._run_pluggy_sync_bg("i541-syncfail"))

    falha = _de(vistos, "pluggy_sync_failed")
    assert len(falha) == 1 and falha[0].get("user_id") is None, vistos
    assert falha[0]["details"] == {"item_id": "i541-syncfail", "motivo": "ForeignKeyViolation",
                                   "sqlstate": "23503", "status_code": None}, falha
    assert str(alheio) not in falha[0]["message"], falha


# ── logging espelhado pelo `_DashboardHandler` (1396 e 1856) ────────────────

def test_falha_na_copia_do_402_espelha_com_dono_e_sem_traceback(user_id, monkeypatch, coletor):
    # Plano sem vaga de OF (`of_banks_max=0`) com acesso ao app: sem plano nenhum
    # o gate de assinatura da rota responde 402 ANTES de chegar à cópia.
    promote_to_pro(user_id)
    monkeypatch.setattr(plan_service, "get_user_limits", lambda uid: {"of_banks_max": 0})
    _mock_item(monkeypatch, user_id)
    monkeypatch.setattr(of_routes.billing_copy, "estado_sem_plano_pago",
                        _levanta(psycopg.OperationalError(HOST)))
    client = TestClient(dashboard.app)
    r = client.post(f"/open-finance/{user_id}/pluggy-item",
                    json={"item": {"id": "i541-gratis"}}, headers=_auth(client, user_id))
    assert r.status_code == 402 and "OF_BANK_LIMIT" in r.text, r.text
    linhas = [g for g in coletor if "of_msg_sem_open_finance_falhou" in g["message"]]
    assert len(linhas) == 1, coletor
    assert linhas[0]["user_id"] == user_id, linhas
    assert "traceback" not in linhas[0]["details"], linhas
    _host_fora(linhas[0]["message"], linhas[0]["details"])


def test_aviso_local_do_rastro_espelha_com_dono(user_id, monkeypatch, coletor, eventos,
                                                webhook_pluggy):
    promote_to_pro(user_id)
    _mock_item(monkeypatch, user_id)
    monkeypatch.setattr(of_routes, "register_item", _levanta(psycopg.OperationalError(HOST)))
    client = TestClient(dashboard.app)
    try:
        r = client.post(f"/open-finance/{user_id}/pluggy-item",
                        json={"item": {"id": "i541-rastro"}}, headers=_auth(client, user_id))
        assert r.status_code == 200, r.text
        linhas = [g for g in coletor if "of_item_registry_failed item_id=" in g["message"]]
        assert len(linhas) == 1, coletor
        assert linhas[0]["user_id"] == user_id, linhas
    finally:
        db.disconnect_open_finance_connection(user_id)
        _limpa_item("i541-rastro")


# ── connect-token (1671): o log com teto não pendura a emissão ──────────────

def test_rastro_do_connect_token_nao_pendura_a_resposta(user_id, monkeypatch, capsys):
    promote_to_pro(user_id)
    monkeypatch.setattr(of_routes, "create_pluggy_connect_token",
                        lambda uid, webhook_url=None: {"accessToken": "tok"})
    monkeypatch.setattr(of_routes, "register_item", _levanta(psycopg.OperationalError(HOST)))
    monkeypatch.setattr(of_routes, "_LOG_DIAG_TIMEOUT_S", 0.05)

    async def _pendura(*a, **k):
        await asyncio.sleep(3)
    monkeypatch.setattr(of_routes, "log_system_event", _pendura)

    client = TestClient(dashboard.app)
    t0 = time.monotonic()
    r = client.post(f"/open-finance/{user_id}/connect-token", headers=_auth(client, user_id))
    assert r.status_code == 200, r.text
    assert time.monotonic() - t0 < 2, "o log de diagnóstico pendurou a emissão do token"
    # Teto estourado: o rastro em stderr (sem banco) é o único canal que sobra.
    err = capsys.readouterr().err
    assert "teto estourado: of_item_registry_failed motivo=OperationalError" in err, err
    assert str(user_id) not in err, err


def test_conflito_da_reconexao_deixa_rastro_local_quando_o_teto_estoura(user_id, monkeypatch, capsys):
    promote_to_pro(user_id)
    _mock_item(monkeypatch, user_id)

    def _conflito(*a, **k):
        raise of_routes._ConflitoReconexao(
            "x", "warning", "of_reconnect_aborted_state_gone", "Reconexão abortada: i541-conf",
            source="open_finance", user_id=user_id, details={"item_id": "i541-conf"})
    monkeypatch.setattr(of_routes, "_salva_item_sob_lock", _conflito)
    monkeypatch.setattr(of_routes, "_LOG_DIAG_TIMEOUT_S", 0.05)

    async def _pendura(*a, **k):
        await asyncio.sleep(3)
    monkeypatch.setattr(of_routes, "log_system_event", _pendura)
    client = TestClient(dashboard.app)
    try:
        r = client.post(f"/open-finance/{user_id}/pluggy-item",
                        json={"item": {"id": "i541-conf"}}, headers=_auth(client, user_id))
        assert r.status_code == 409, r.text
        err = capsys.readouterr().err
        assert "teto estourado: of_reconnect_aborted_state_gone" in err, err
        assert str(user_id) not in err, err
    finally:
        _limpa_item("i541-conf")


# ── disconnect (2351) e reset (2294, settings 170/236) ──────────────────────

def test_segundo_passe_do_disconnect_sem_uid_e_sem_texto_cru(user_id, monkeypatch, sync_log):
    promote_to_pro(user_id)
    _semeia_conexao_pluggy(user_id, f"i541-disc-{user_id}")

    def _helper(uid, item_ids=None, **kw):
        if item_ids is None:
            return []  # 1º passe não enumerou nada: todo varrido vira tardio
        raise RuntimeError(HOST)
    monkeypatch.setattr(of_routes, "delete_pluggy_items_best_effort", _helper)

    client = TestClient(dashboard.app)
    r = client.delete(f"/open-finance/{user_id}", headers=_auth(client, user_id))
    assert r.status_code == 200, r.text
    linhas = [e for e in sync_log if e["event"] == "pluggy_item_delete_failed"]
    assert len(linhas) == 1, sync_log
    assert linhas[0].get("user_id") is None, "coluna nula de propósito (D2)"
    assert set(linhas[0]["details"]) == {"items", "motivo", "sqlstate"}, linhas
    assert str(user_id) not in linhas[0]["message"], linhas
    _host_fora(linhas[0]["message"], linhas[0]["details"])


def test_reset_sem_apikey_grava_dono_sem_host(user_id, monkeypatch, sync_log):
    _semeia(user_id)
    monkeypatch.setattr(of_routes, "create_pluggy_api_key", _levanta(RuntimeError(HOST)))
    client = TestClient(dashboard.app)
    r = client.post("/settings/reset", json={"password": SENHA}, headers=_auth_reset(client, user_id))
    assert r.status_code == 200, r.text
    linhas = [e for e in sync_log if e["event"] == "pluggy_disconnect_auth_failed"]
    assert len(linhas) == 1, sync_log
    assert linhas[0]["user_id"] == user_id, linhas
    _host_fora(linhas[0]["message"], linhas[0]["details"])


def test_reset_com_limpeza_remota_quebrada_grava_dono_sem_host(user_id, monkeypatch, sync_log):
    _semeia(user_id)
    monkeypatch.setattr(of_routes, "delete_pluggy_items_best_effort", _levanta(RuntimeError(HOST)))
    client = TestClient(dashboard.app)
    r = client.post("/settings/reset", json={"password": SENHA}, headers=_auth_reset(client, user_id))
    assert r.status_code == 200, r.text
    linhas = [e for e in sync_log if e["event"] == "account_reset_pluggy_cleanup_failed"]
    assert len(linhas) == 2, f"1º e 2º passe: {sync_log}"
    for linha in linhas:
        assert linha["user_id"] == user_id, linha
        assert str(user_id) not in linha["message"], linha
        _host_fora(linha["message"], linha["details"])


# ── proativos (86, 92, 129, 134) e conciliação manual (db 2249) ─────────────

@pytest.mark.parametrize("recusar", [False, True], ids=["falhou", "recusado"])
@pytest.mark.parametrize("caso", list(_CASOS))
def test_aviso_proativo_espelha_com_dono(user_id, monkeypatch, _event_logs, coletor, caso, recusar):
    erro = None if recusar else RuntimeError("meta fora")
    _armar(monkeypatch, caso, [user_id], _espiao([], erro, recusar=recusar))
    assert _CASOS[caso][0]()["sent"] == 0
    linhas = [g for g in coletor if g["message"].startswith("[of_proactive]")]
    assert len(linhas) == 1, coletor
    assert linhas[0]["user_id"] == user_id, linhas


def test_conciliacao_manual_quebrada_espelha_com_dono_sem_traceback(
        uid_pro, ia_fora, monkeypatch, coletor):
    _banco(uid_pro, tx(uid_pro, "-50.00", today_tz(), "MERCADO"))
    monkeypatch.setattr(of_mod, "pick_reconciliation_match", _levanta(RuntimeError(HOST)))
    assert "registrada" in manda(uid_pro, "gastei 50 no mercado em dinheiro")
    linhas = [g for g in coletor if "propose_manual_reconciliation falhou" in g["message"]]
    assert len(linhas) == 1, coletor
    assert linhas[0]["user_id"] == uid_pro, linhas
    assert "traceback" not in linhas[0]["details"], linhas
    _host_fora(linhas[0]["message"], linhas[0]["details"])
