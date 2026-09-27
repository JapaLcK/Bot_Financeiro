"""Issue #541 (item 15): `of_refresh_claimed` grava a coluna NULL — o evento tem
vários donos —, então `details` não pode carregar o `user_id` de nenhum deles.

`request_pluggy_refresh` (`core/services/pluggy_sync.py`) devolve `claimed` como
`[{"item_id", "user_id"}]`; o tick (`_open_finance_refresh`, no monólito) gravava
essa lista inteira. Aqui o tick roda de verdade, uma volta, com o refresh real
restrito ao usuário do teste; só o PATCH na Pluggy e o log são dublados.

CONTROLE NEGATIVO: voltar `"items": res["claimed"]` no monólito → vermelho.
"""
from __future__ import annotations

import asyncio

import pytest

import core.admin_dashboard as admin_dashboard
import core.services.pluggy_sync as ps
import frontend.finance_bot_websocket_custom as dashboard
from test_of_refresh_schedule import _conexao


def test_tick_do_refresh_nao_grava_uid_dos_donos(user_id, monkeypatch):
    _conexao(user_id, "i541-claimed")
    monkeypatch.setenv("OF_REFRESH_ENABLED", "1")
    monkeypatch.setenv("OF_REFRESH_JITTER_PCT", "0")
    monkeypatch.setattr(ps, "update_pluggy_item", lambda item, key=None: True)
    monkeypatch.setattr(ps, "_hold_aggregate_emails", lambda uid, origem: None)
    monkeypatch.setattr(ps, "run_of_health_check", lambda **kw: {})
    real = ps.request_pluggy_refresh  # real, só sem varrer as conexões de outros testes
    monkeypatch.setattr(ps, "request_pluggy_refresh", lambda **kw: real(**kw, user_id=user_id))

    vistos: list[dict] = []

    async def _log(level, event_type, message, **kw):
        vistos.append({"event": event_type, "message": message, **kw})
    monkeypatch.setattr(admin_dashboard, "log_system_event", _log)

    voltas = []
    sleep_real = asyncio.sleep

    async def _uma_volta(segundos):
        voltas.append(segundos)
        if len(voltas) > 1:
            raise asyncio.CancelledError  # corta na 2ª espera
        await sleep_real(0)

    async def _corre():
        monkeypatch.setattr(asyncio, "sleep", _uma_volta)
        with pytest.raises(asyncio.CancelledError):
            await dashboard._open_finance_refresh()

    asyncio.run(_corre())

    claimed = [v for v in vistos if v["event"] == "of_refresh_claimed"]
    assert len(claimed) == 1, vistos
    assert str(user_id) not in str(claimed[0]["details"]) + claimed[0]["message"], claimed
    assert claimed[0].get("user_id") is None, claimed
    assert claimed[0]["details"]["items"] == ["i541-claimed"], claimed
