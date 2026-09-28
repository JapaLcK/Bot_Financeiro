"""Issue #657, PR A: os avisos locais do Open Finance vão para stderr, sem uid e
sem texto cru de exceção, e o rastro do teto estourado diz QUAL item.

1. `_log_com_teto` estourado imprime `motivo=` e `item_id=`. Antes o `motivo` só
   existia no `/pluggy-item`, e nenhum dos três casos da reconexão (409, lock
   ocupado, infra) dizia o item.
2. Os três avisos locais (`of_reconnect_lock_retry`, `of_reconnect_lock_timeout`,
   `of_item_registry_failed`) eram `logging.warning`, que o `_DashboardHandler`
   espelha com INSERT SÍNCRONO no event loop: com `system_event_logs` travada o
   prazo dobrava. Molde: `test_of_log_teto_e_status.py::
   test_teto_vale_com_o_espelho_do_logging_lento`.
3. O `print` do tick de refresh não leva o `claimed` (uid dos donos), e o do
   `_hold_aggregate_emails` não leva `str(exc)` (a FK traz `Key (user_id)=`).

CONTROLES (CLAUDE.md §3), cada um medido num caso verde:
  • tirar `item_id=` do print do `_log_com_teto` → os 3 casos do item 1 vermelhos;
  • voltar qualquer um dos três avisos para `logging.getLogger(__name__).warning`
    → o caso daquele aviso no item 2 vermelho;
  • voltar `{res}` no print do tick → `test_tick_do_refresh_nao_imprime_uid`;
  • voltar `{exc}` no `_hold_aggregate_emails` → `test_hold_..._nao_imprime_uid`.
CLASSE CEGA: aviso NOVO com `logging.warning` antes de um `_log_com_teto` — só
uma varredura pegaria (PR B da #657).
"""
from __future__ import annotations

import asyncio
import time

import psycopg
import pytest
from fastapi.testclient import TestClient

import core.admin_dashboard as admin_dashboard
import core.observability as observability
import core.services.pluggy_sync as ps
import db
import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.open_finance as of_routes
from conftest import promote_to_pro
from db.connection import get_conn
from test_of_item_ownership import _auth, eventos  # noqa: F401 — fixture
from test_of_log_dono_coluna import HOST, _host_fora, _levanta
from test_of_refresh_schedule import _conexao
from test_of_webhook_adopt_guards import _limpa_item, _mock_item, webhook_pluggy  # noqa: F401

_INFRA = "sorry, too many clients already"


def _conflito(uid, *a, **k):
    raise of_routes._ConflitoReconexao(
        "x", "warning", "of_reconnect_aborted_state_gone", "Reconexão abortada",
        source="open_finance", user_id=uid, details={"item_id": "i657-409"})


def _ocupado(*a, **k):
    return None, False


def _infra(*a, **k):
    raise psycopg.errors.TooManyConnections(_INFRA)


@pytest.mark.parametrize("salva, esperado, status", [
    (_conflito, "of_reconnect_aborted_state_gone motivo=None item_id=i657-409", 409),
    (_ocupado, "of_reconnect_lock_timeout motivo=lock_ocupado item_id=i657-409", 503),
    (_infra, "of_reconnect_lock_timeout motivo=TooManyConnections item_id=i657-409", 503),
], ids=["409", "lock_ocupado", "infra"])
def test_teto_estourado_diz_o_item_e_o_motivo(user_id, monkeypatch, capsys,
                                              salva, esperado, status):
    monkeypatch.setattr(of_routes, "_salva_item_sob_lock", salva)
    monkeypatch.setattr(of_routes, "_RECONNECT_DEADLINE_MS", 300)
    monkeypatch.setattr(of_routes, "_LOG_DIAG_TIMEOUT_S", 0.05)
    monkeypatch.setattr(of_routes, "_backoff_sec", lambda t: 0)

    async def _pendura(*a, **k):
        await asyncio.sleep(3)
    monkeypatch.setattr(of_routes, "log_system_event", _pendura)

    with pytest.raises(of_routes.HTTPException) as exc:
        asyncio.run(of_routes._grava_reconexao(user_id, {"id": "i"}, "i657-409"))
    assert exc.value.status_code == status
    err = capsys.readouterr().err
    assert f"log com teto estourado: {esperado}" in err, err
    assert str(user_id) not in err and _INFRA not in err, err


# ── 2. os três avisos locais não pagam o espelho síncrono ────────────────────

@pytest.fixture()
def espelho_lento(monkeypatch):
    """O `_DashboardHandler` de verdade no root, com o INSERT dublado por um sono
    (a tabela travada), e o `log_system_event` async voltando na hora: o único
    jeito de passar de 1s é um `logging.warning` no caminho."""
    async def _rapido(*a, **k):
        return None
    monkeypatch.setattr(of_routes, "log_system_event", _rapido)
    monkeypatch.setattr(observability, "log_system_event_sync",
                        lambda *a, **k: time.sleep(1.5))
    observability._configure_root_logger()
    monkeypatch.setattr(of_routes, "_backoff_sec", lambda t: 0)


def test_aviso_do_retry_nao_paga_o_espelho(user_id, monkeypatch, espelho_lento, capsys):
    tentativas = []

    def _infra_depois_grava(*a, **k):
        tentativas.append(1)
        if len(tentativas) == 1:
            _infra()
        return {"id": 1, "provider_item_id": "i657-retry"}, True
    monkeypatch.setattr(of_routes, "_salva_item_sob_lock", _infra_depois_grava)

    t0 = time.monotonic()
    conexao = asyncio.run(of_routes._grava_reconexao(user_id, {"id": "i"}, "i657-retry"))
    dt = time.monotonic() - t0
    assert conexao["provider_item_id"] == "i657-retry" and len(tentativas) == 2
    assert dt < 1.0, f"o aviso do retry pagou o espelho síncrono: {dt:.2f}s"
    err = capsys.readouterr().err
    assert "of_reconnect_lock_retry item_id=i657-retry tentativa=1 motivo=TooManyConnections" in err, err


def test_aviso_do_timeout_nao_paga_o_espelho(user_id, monkeypatch, espelho_lento, capsys):
    # Uma tentativa só: o aviso do retry nem roda, sobra o do timeout.
    monkeypatch.setattr(of_routes, "_RECONNECT_LOCK_ATTEMPTS", 1)
    monkeypatch.setattr(of_routes, "_salva_item_sob_lock", _infra)

    t0 = time.monotonic()
    with pytest.raises(of_routes.HTTPException) as exc:
        asyncio.run(of_routes._grava_reconexao(user_id, {"id": "i"}, "i657-timeout"))
    dt = time.monotonic() - t0
    assert exc.value.status_code == 503
    assert dt < 1.0, f"o aviso do timeout pagou o espelho síncrono: {dt:.2f}s"
    err = capsys.readouterr().err
    assert "of_reconnect_lock_timeout item_id=i657-timeout motivo=TooManyConnections" in err, err
    assert _INFRA not in err, err


def test_aviso_do_registry_nao_paga_o_espelho(user_id, monkeypatch, espelho_lento, capsys,
                                              webhook_pluggy):
    promote_to_pro(user_id)
    _mock_item(monkeypatch, user_id)
    monkeypatch.setattr(of_routes, "register_item", _levanta(psycopg.OperationalError(HOST)))
    client = TestClient(dashboard.app)
    headers = _auth(client, user_id)
    try:
        t0 = time.monotonic()
        r = client.post(f"/open-finance/{user_id}/pluggy-item",
                        json={"item": {"id": "i657-reg"}}, headers=headers)
        dt = time.monotonic() - t0
        assert r.status_code == 200, r.text
        assert dt < 1.0, f"o aviso do registry pagou o espelho síncrono: {dt:.2f}s"
        err = capsys.readouterr().err
        assert "of_item_registry_failed item_id=i657-reg motivo=OperationalError" in err, err
        assert str(user_id) not in err, err
        _host_fora(err)
    finally:
        db.disconnect_open_finance_connection(user_id)
        _limpa_item("i657-reg")


# ── 3. prints do tick de refresh e do hold ───────────────────────────────────

def test_tick_do_refresh_nao_imprime_uid(user_id, monkeypatch, capsys):
    """Tick real, uma volta, no molde de `test_of_refresh_claimed_sem_uid.py`."""
    _conexao(user_id, "i657-tick")
    monkeypatch.setenv("OF_REFRESH_ENABLED", "1")
    monkeypatch.setenv("OF_REFRESH_JITTER_PCT", "0")
    monkeypatch.setattr(ps, "update_pluggy_item", lambda item, key=None: True)
    monkeypatch.setattr(ps, "_hold_aggregate_emails", lambda uid, origem: None)
    monkeypatch.setattr(ps, "run_of_health_check", lambda **kw: {})
    real = ps.request_pluggy_refresh
    monkeypatch.setattr(ps, "request_pluggy_refresh", lambda **kw: real(**kw, user_id=user_id))

    async def _log(*a, **kw):
        return None
    monkeypatch.setattr(admin_dashboard, "log_system_event", _log)

    voltas = []
    sleep_real = asyncio.sleep

    async def _uma_volta(segundos):
        voltas.append(segundos)
        if len(voltas) > 1:
            raise asyncio.CancelledError
        await sleep_real(0)

    async def _corre():
        monkeypatch.setattr(asyncio, "sleep", _uma_volta)
        with pytest.raises(asyncio.CancelledError):
            await dashboard._open_finance_refresh()

    asyncio.run(_corre())
    linhas = [ln for ln in capsys.readouterr().out.splitlines() if "[open_finance_refresh]" in ln]
    assert linhas == ["[open_finance_refresh] triggered=1 total=1 origin=periodic failures=[]"], linhas
    assert str(user_id) not in linhas[0], linhas


def test_hold_com_fk_real_nao_imprime_uid(monkeypatch, capsys):
    alheio = 987654321657
    with get_conn() as c:
        try:
            c.execute("insert into pockets (user_id, name) values (%s, 'x')", (alheio,))
        except psycopg.errors.ForeignKeyViolation as e:
            c.rollback()
            fk = e
    assert str(alheio) in str(fk), "pré-condição: o texto cru traz o uid"
    monkeypatch.setattr(db, "hold_agent_emails", _levanta(fk))

    ps._hold_aggregate_emails(alheio, "refresh_all")
    out = capsys.readouterr().out
    assert "hold agregados (refresh_all): motivo=ForeignKeyViolation sqlstate=23503" in out, out
    assert str(alheio) not in out, out
