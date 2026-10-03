"""Falha de rede nas integrações não grava o segredo no log.

O `requests` põe a URL inteira — query incluída — no `str()` da exceção. O Meta
levava o `access_token` na query, o GA4 leva o `api_secret` (exigência do
Measurement Protocol) e o webhook do Discord tem o token no path. Os três
`except` logavam `exc` com `exc_info=True`, e o `_DashboardHandler` persiste a
mensagem e o traceback em `system_event_logs`.

O teste passa pela função pública, com a exceção do `requests` real, e olha o
que chegaria à tabela pelo handler real.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
import requests

from core.services import admin_notify, ga4_mp, meta_capi
from tests.test_log_falha_traceback import _system_event_logs

_SEGREDO = "SEGREDO-n1-a9f3"

_CASOS = {
    "meta": (
        meta_capi,
        {"META_PIXEL_ID": "111", "META_PIXEL_ACCESS_TOKEN": _SEGREDO},
        "HTTPSConnectionPool(host='graph.facebook.com', port=443): Max retries exceeded "
        f"with url: /v21.0/111/events?access_token={_SEGREDO} (Caused by NewConnectionError(...))",
        lambda: meta_capi.send_event(event_name="Purchase", event_id="n1",
                                     event_time=1700000000, email="a@b.com"),
        "Purchase",
    ),
    "ga4": (
        ga4_mp,
        {"GA4_MEASUREMENT_ID": "G-X", "GA4_API_SECRET": _SEGREDO},
        "HTTPSConnectionPool(host='www.google-analytics.com', port=443): Max retries exceeded "
        f"with url: /mp/collect?measurement_id=G-X&api_secret={_SEGREDO} (Caused by NewConnectionError(...))",
        lambda: ga4_mp.send_event(name="purchase", client_id="123.456"),
        "purchase",
    ),
    "discord": (
        admin_notify,
        {"ADMIN_NOTIFY_WEBHOOK_URL": f"https://discord.com/api/webhooks/123/{_SEGREDO}"},
        "HTTPSConnectionPool(host='discord.com', port=443): Max retries exceeded "
        f"with url: /api/webhooks/123/{_SEGREDO} (Caused by NewConnectionError(...))",
        lambda: admin_notify.notify_security_alert("x"),
        "[admin_notify]",
    ),
}


@pytest.mark.parametrize("caso", list(_CASOS))
def test_falha_de_rede_nao_vaza_o_segredo(caso, monkeypatch, caplog):
    mod, envs, msg, chamar, contexto = _CASOS[caso]
    monkeypatch.delenv("META_TEST_EVENT_CODE", raising=False)
    for k, v in envs.items():
        monkeypatch.setenv(k, v)

    def explode(*a, **kw):
        raise requests.exceptions.ConnectionError(msg)

    monkeypatch.setattr(mod, "requests", SimpleNamespace(post=explode))

    with caplog.at_level(logging.WARNING):
        assert chamar() is False

    assert _SEGREDO not in caplog.text, caplog.text
    linhas = [r for r in caplog.records
              if r.name == mod.__name__ and r.levelno == logging.WARNING]
    assert len(linhas) == 1, [(r.name, r.getMessage()) for r in caplog.records]
    assert "ConnectionError" in linhas[0].getMessage()
    assert contexto in linhas[0].getMessage()
    assert not linhas[0].exc_info

    gravados = _system_event_logs(monkeypatch, linhas)
    assert _SEGREDO not in repr(gravados), gravados
    assert all("traceback" not in g["details"] for g in gravados), gravados


def _captura(monkeypatch, mod, resposta):
    chamadas: list[dict] = []

    def post(url, **kw):
        chamadas.append({"url": url, **kw})
        return resposta

    monkeypatch.setattr(mod, "requests", SimpleNamespace(post=post))
    return chamadas


def test_meta_manda_o_token_no_corpo_e_nao_na_url(monkeypatch):
    monkeypatch.setenv("META_PIXEL_ID", "111")
    monkeypatch.setenv("META_PIXEL_ACCESS_TOKEN", _SEGREDO)
    monkeypatch.delenv("META_TEST_EVENT_CODE", raising=False)
    chamadas = _captura(monkeypatch, meta_capi,
                        SimpleNamespace(status_code=200, text='{"events_received":1}'))

    assert meta_capi.send_event(event_name="Purchase", event_id="n1",
                                event_time=1700000000, email="a@b.com") is True

    assert len(chamadas) == 1
    c = chamadas[0]
    assert c["json"]["access_token"] == _SEGREDO
    assert c["json"]["data"][0]["event_name"] == "Purchase"
    assert not c.get("params")
    assert _SEGREDO not in c["url"]


def test_ga4_continua_com_api_secret_na_query(monkeypatch):
    monkeypatch.setenv("GA4_MEASUREMENT_ID", "G-X")
    monkeypatch.setenv("GA4_API_SECRET", _SEGREDO)
    chamadas = _captura(monkeypatch, ga4_mp, SimpleNamespace(status_code=204, text=""))

    assert ga4_mp.send_event(name="purchase", client_id="123.456") is True

    assert len(chamadas) == 1
    assert chamadas[0]["params"]["api_secret"] == _SEGREDO


def test_webhook_envia_normalmente(monkeypatch):
    url = f"https://discord.com/api/webhooks/123/{_SEGREDO}"
    monkeypatch.setenv("ADMIN_NOTIFY_WEBHOOK_URL", url)
    chamadas = _captura(monkeypatch, admin_notify, SimpleNamespace(status_code=204, text=""))

    assert admin_notify.notify_security_alert("x") is True

    assert len(chamadas) == 1
    assert chamadas[0]["url"] == url
