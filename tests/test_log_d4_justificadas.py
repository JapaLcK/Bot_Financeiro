"""Teste próprio das entradas de `JUSTIFICADAS` de `tests/test_log_d4_guarda.py` (D4 da #657).

A entrada tira a ocorrência do ratchet, então TROCAR o que ela cobre passaria sem nada acusar. Cada
caso prende a forma justificada. `send_email` tem `test_assunto_dinamico_de_send_email`, e o `api_key=`
do `media_service`, `test_t4_audio_nao_imprime_o_nome_do_arquivo`.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

import utils_date
from core.services import meta_capi
from test_fuso_do_app import _com_env, _restaura_fuso  # noqa: F401


def test_fuso_invalido_imprime_so_a_linha_do_boot(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("REPORT_TIMEZONE", raising=False)
    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.setattr(utils_date, "_TZ_ENV_ORIGINAL", None)

    with pytest.raises(SystemExit):
        _com_env(tmp_path, monkeypatch, "REPORT_TIMEZONE=Nao/Existe\n")

    linhas = [ln for ln in capsys.readouterr().err.splitlines() if ln.startswith("ERROR:")]
    assert linhas == ["ERROR: REPORT_TIMEZONE/TZ inválido ('No time zone found with key Nao/Existe'). "
                      "Use um nome IANA, ex.: America/Sao_Paulo."], linhas


def test_corpo_2xx_do_meta_sai_cortado(monkeypatch, caplog):
    inicio = '{"events_received":1,"fbtrace_id":"abc"'
    corpo = inicio.ljust(200) + "RESTO-DO-CORPO"
    monkeypatch.setenv("META_PIXEL_ID", "111")
    monkeypatch.setenv("META_PIXEL_ACCESS_TOKEN", "t")
    monkeypatch.setattr(meta_capi.requests, "post",
                        lambda *a, **k: SimpleNamespace(status_code=200, text=corpo))

    with caplog.at_level(logging.INFO, logger=meta_capi.logger.name):
        assert meta_capi.send_event(event_name="Purchase", event_id="n1",
                                    event_time=1700000000, email="a@b.com") is True

    assert inicio in caplog.text and "RESTO-DO-CORPO" not in caplog.text, caplog.text
