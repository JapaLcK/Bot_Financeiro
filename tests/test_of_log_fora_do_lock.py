"""Diagnósticos de conflito não podem prender o lock nem a resposta 409."""
import asyncio
from contextlib import contextmanager

import pytest
from fastapi import HTTPException
import frontend.routes.open_finance as routes


@pytest.mark.parametrize("propria", [False, True])
def test_log_ocorre_fora_do_lock_e_tem_prazo(monkeypatch, propria):
    preso = False
    logs = []

    @contextmanager
    def lock(*args, **kwargs):
        nonlocal preso
        preso = True
        try:
            yield True
        finally:
            preso = False

    async def log(*args, **kwargs):
        assert not preso, "diagnóstico executado dentro do lock"
        logs.append(args[1])
        await asyncio.sleep(10)

    monkeypatch.setattr(routes, "pluggy_item_lock", lock)
    monkeypatch.setattr(routes, "get_connections_by_item_id",
                        lambda *a, **kw: [] if propria else [{"user_id": 2}])
    monkeypatch.setattr(routes, "log_system_event", log)
    monkeypatch.setattr(routes, "_LOG_DIAG_TIMEOUT_S", 0.01)

    async def executar():
        with pytest.raises(HTTPException) as exc:
            await asyncio.wait_for(routes._grava_reconexao(
                1, {}, "item-log", tinha_conexao_propria=propria), timeout=1)
        assert exc.value.status_code == 409

    asyncio.run(executar())
    assert logs == ["of_reconnect_aborted_state_gone" if propria else "of_item_owner_conflict"]
