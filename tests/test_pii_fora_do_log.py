"""Dado pessoal fora dos logs (N4, PR 1).

O root logger está em INFO e o `_DashboardHandler` grava WARNING+ em
`system_event_logs`, sem retenção. O que este arquivo prova:

- a MENSAGEM FORMATADA das linhas de log tocadas não leva texto do usuário
  (valor, loja, nome), telefone completo (só `mask_phone`), IP nem os valores
  da query string (só `query_keys`);
- o `details` do `message_processing_failed` (`core/handle_incoming.py`) não
  leva o texto — só `chars`.

O `details` estruturado (`wa_id`, `to`) e as linhas INFO com telefone são do
PR 2 do N4, em `tests/test_pii_telefone_no_details.py`: o número vai mascarado
e o dedup do aviso de vinculação é por `user_id`. As linhas antigas da tabela
continuam com o número cru — não foram reescritas.

Os casos passam pela função pública e, onde a linha é WARNING+, olham o que
chegaria à tabela pelo handler real (`_system_event_logs`).
"""
from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import openai
import pytest
import requests
from fastapi.testclient import TestClient

import adapters.whatsapp.wa_runtime as wa_runtime
import adapters.whatsapp.wa_tutorial as wa_tutorial
import api.v2.erros as erros
import core.admin_dashboard as admin_dashboard
import core.handle_incoming as hi
import frontend.finance_bot_websocket_custom as dashboard
from adapters.whatsapp.wa_parse import InboundMessage
from core.services import ipgeo, media_service
from core.types import IncomingMessage
from tests.test_api_v2_erros import rota_temporaria  # noqa: F401 (fixture)
from tests.test_category_normalization import _wa
from tests.test_error_pages import boom_route  # noqa: F401 (fixture)
from tests.test_log_falha_traceback import _system_event_logs

FONE = "5511987654321"
MASCARA = "5511******4321"
TEXTO = "gastei R$ 1.234,56 no Açaí do João"


def _msg(texto: str = TEXTO) -> InboundMessage:
    return InboundMessage(wa_id=FONE, text=texto, timestamp=None, attachments=[],
                          raw={"id": "wamid.pii.1", "type": "text"})


def test_process_message_loga_tamanho_e_telefone_mascarado(user_id, monkeypatch, caplog):
    _wa(monkeypatch, user_id)
    with caplog.at_level(logging.INFO):
        wa_runtime.process_message(_msg())

    linhas = [r.getMessage() for r in caplog.records
              if r.getMessage().startswith("WA process_message")]
    assert len(linhas) == 1, linhas
    assert FONE not in linhas[0] and "Açaí" not in linhas[0] and "1.234" not in linhas[0], linhas[0]
    assert f"from={MASCARA}" in linhas[0] and f"chars={len(TEXTO)}" in linhas[0], linhas[0]


def test_falha_no_processamento_nao_grava_telefone_cru(user_id, monkeypatch, caplog):
    """:1462, :1472 e :1486 — as três linhas de falha do `process_message`."""
    _wa(monkeypatch, user_id)

    def explode(*a, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(wa_runtime, "handle_incoming", explode)
    monkeypatch.setattr(wa_runtime, "log_system_event_sync", explode)
    monkeypatch.setattr(wa_runtime, "_send_reply", explode)
    with caplog.at_level(logging.WARNING):
        wa_runtime.process_message(_msg())

    linhas = [r for r in caplog.records if r.name == wa_runtime.__name__ and r.levelno >= logging.WARNING]
    msgs = [r.getMessage() for r in linhas]
    assert any(m.startswith("WA message processing failed") for m in msgs), msgs
    assert any(m.startswith("WA processing failure could not be recorded") for m in msgs), msgs
    assert any(m.startswith("WA failure notice could not be sent") for m in msgs), msgs
    gravados = json.dumps(_system_event_logs(monkeypatch, linhas), ensure_ascii=False)
    assert FONE not in gravados and gravados.count(MASCARA) >= 3, gravados


def _send_reply_falha(monkeypatch):
    def explode(**kw):
        raise RuntimeError("rede")
    monkeypatch.setattr(wa_runtime, "send_text", explode)
    with pytest.raises(RuntimeError):
        wa_runtime._send_reply(FONE, "oi")


def _tutorial_desconhecido(monkeypatch):
    wa_tutorial.handle_tutorial_button(FONE, "botao_que_nao_existe")


@pytest.mark.parametrize("disparo", [_send_reply_falha, _tutorial_desconhecido],
                         ids=["send_text:165", "tutorial:377"])
def test_warning_sem_telefone_cru(disparo, monkeypatch, caplog):
    with caplog.at_level(logging.WARNING):
        disparo(monkeypatch)
    linhas = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert linhas, "o disparo não chegou à linha de log"
    gravados = json.dumps(_system_event_logs(monkeypatch, linhas), ensure_ascii=False)
    assert FONE not in gravados and MASCARA in gravados, gravados


@pytest.mark.parametrize("botao", [wa_runtime.WA_DAILY_REPORT_DISABLE_ID,
                                   wa_runtime.WA_WEEKLY_REPORT_DISABLE_ID,
                                   wa_runtime.WA_MONTHLY_REPORT_DISABLE_ID,
                                   "parar atualizações"])
def test_botao_de_opt_out_com_falha_sem_telefone_cru(botao, user_id, monkeypatch, caplog):
    def explode(*a, **kw):
        raise RuntimeError("rede")

    monkeypatch.setattr(wa_runtime, "_send_reply", explode)
    monkeypatch.setattr(wa_runtime, "log_system_event_sync", lambda *a, **kw: None)
    with caplog.at_level(logging.WARNING):
        assert wa_runtime._tratar_opt_out(user_id, FONE, botao) is True
    linhas = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(linhas) == 1, [r.getMessage() for r in caplog.records]
    gravados = json.dumps(_system_event_logs(monkeypatch, linhas), ensure_ascii=False)
    assert FONE not in gravados and MASCARA in gravados, gravados


def test_handle_incoming_failed_nao_leva_o_texto(user_id, monkeypatch, caplog):
    def explode(*a, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(hi, "_paywall_gate", explode)  # 1º passo do try
    eventos: list[dict] = []
    monkeypatch.setattr(hi, "log_system_event_sync", lambda *a, **kw: eventos.append(kw))
    with caplog.at_level(logging.ERROR):
        hi.handle_incoming(IncomingMessage(platform="whatsapp", user_id=user_id, text=TEXTO))

    # O evento `message_processing_failed`, gravado direto (sem passar pelo logger).
    assert len(eventos) == 1, eventos
    details = eventos[0]["details"]
    assert "Açaí" not in json.dumps(details, ensure_ascii=False), details
    assert details["chars"] == len(TEXTO) and details["platform"] == "whatsapp", details
    assert "RuntimeError" in details["traceback"], details

    linhas = [r for r in caplog.records if r.getMessage().startswith("handle_incoming FAILED")]
    assert len(linhas) == 1, [r.getMessage() for r in caplog.records]
    gravados = _system_event_logs(monkeypatch, linhas)
    dump = json.dumps(gravados, ensure_ascii=False)
    assert "Açaí" not in dump and "1.234" not in dump, dump
    assert f"chars={len(TEXTO)}" in gravados[0]["message"], gravados


class _OpenAIFake:
    def __init__(self, *, transcricao: str = "", conteudo: str = "{}", **kw):
        self.audio = SimpleNamespace(transcriptions=SimpleNamespace(create=lambda **k: transcricao))
        resp = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=conteudo))])
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **k: resp))


def test_transcricao_loga_so_o_tamanho(monkeypatch, caplog):
    falado = "paguei 777,77 pro Dr. Fulano Segredo"
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: _OpenAIFake(transcricao=falado))
    with caplog.at_level(logging.INFO):
        assert media_service.transcribe_audio(b"ogg", "a.ogg") == falado
    assert "Fulano" not in caplog.text and "777" not in caplog.text, caplog.text
    assert f"Transcrição ok: {len(falado)} chars" in caplog.text, caplog.text


def test_analise_de_imagem_loga_so_a_contagem(monkeypatch, caplog):
    # A chave fora do esquema simula o modelo pondo texto da imagem na CHAVE.
    conteudo = json.dumps({"valor": 987.65, "descricao": "Farmácia Segredo", "Dr. Chave Segredo": 1})
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: _OpenAIFake(conteudo=conteudo))
    with caplog.at_level(logging.INFO):
        assert media_service.analyze_image(b"png", "a.png")["valor"] == 987.65
    assert "Segredo" not in caplog.text and "987" not in caplog.text, caplog.text
    assert "Análise de imagem: 3 campos" in caplog.text, caplog.text


def test_ai_patterns_resposta_sem_items_loga_so_o_tamanho(monkeypatch, caplog):
    from core import ai_patterns
    conteudo = json.dumps({"outra": "Padaria Segredo 55,90"})
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: _OpenAIFake(conteudo=conteudo))
    with caplog.at_level(logging.WARNING):
        assert ai_patterns._call_llm("sys", {}) is None
    gravados = json.dumps(_system_event_logs(monkeypatch, caplog.records), ensure_ascii=False)
    assert "Segredo" not in gravados and f"{len(conteudo)} chars" in gravados, gravados


def test_ipgeo_falha_nao_imprime_o_ip(monkeypatch, capsys):
    monkeypatch.delenv("IPGEO_DISABLED", raising=False)

    def explode(url, **kw):
        raise requests.exceptions.ConnectionError(
            f"HTTPSConnectionPool(host='ipapi.co', port=443): Max retries exceeded with url: {url}")

    monkeypatch.setattr(ipgeo.requests, "get", explode)
    assert ipgeo.lookup_city("8.8.4.4") is None
    err = capsys.readouterr().err
    assert "8.8.4.4" not in err and "ConnectionError" in err, err


def _espiao(monkeypatch, *mods):
    chamadas: list[dict] = []

    async def grava(*args, **kwargs):
        chamadas.append(kwargs)

    for m in mods:
        monkeypatch.setattr(m, "log_system_event", grava)
    return chamadas


def test_500_da_api_v2_grava_so_as_chaves_da_query(monkeypatch, rota_temporaria):  # noqa: F811
    chamadas = _espiao(monkeypatch, erros, admin_dashboard)

    def explode():
        raise RuntimeError("x")

    rota_temporaria("/_teste_pii_500", explode, "GET")
    r = TestClient(dashboard.app).get("/api/v2/_teste_pii_500?token=SEGREDO&mes=2026-09")
    assert r.status_code == 500 and len(chamadas) == 1, chamadas
    assert "SEGREDO" not in json.dumps(chamadas[0]["details"])
    assert chamadas[0]["details"]["query_keys"] == ["mes", "token"]


def test_500_do_app_pai_grava_so_as_chaves_da_query(monkeypatch, boom_route):  # noqa: F811
    chamadas = _espiao(monkeypatch, admin_dashboard)
    r = TestClient(dashboard.app, raise_server_exceptions=False).get(
        f"{boom_route}?token=SEGREDO&mes=2026-09")
    assert r.status_code == 500 and len(chamadas) == 1, chamadas
    assert "SEGREDO" not in json.dumps(chamadas[0]["details"])
    assert chamadas[0]["details"]["query_keys"] == ["mes", "token"]
