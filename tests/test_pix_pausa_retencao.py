"""Pausa da concessão do Pix (#1574): a purga da outbox e o aviso de pausa longa.

Decisão do dono (M2): a compra guardada na pausa não se perde na purga de `RETENCAO_OUTBOX_DIAS`, e o
aviso diz quanto falta para os 7 dias, mais insistente no último dia. A guarda (`pix_pausa_guarda`)
vale enquanto a pausa está ativa e por mais 7 dias depois da retomada: uma guarda só "enquanto ativa"
perde a compra quando a purga diária roda entre a retomada e a varredura que a drenaria
(`test_compra_de_8_dias_sobrevive_a_purga_e_concede_na_retomada`, passo da purga logo depois da retomada).
"""
from __future__ import annotations

import threading
import time

import psycopg
import pytest

from _dreno_pix_helpers import entregar, evento, ler
from core.services.pix_drain import drenar_evento
from core.services.pix_sweeps import drenar_pendentes
from db.webhook_outbox import RETENCAO_OUTBOX_DIAS, purgar_payloads_antigos
from db.pix_pausa import FALHA_PAUSA, avisar_se_esquecida
from test_pix_pausa import (  # noqa: F401
    _grants_pix_ativos, _sql, _venda, ativar, externo, retomar, undone_que_reconcede_na_pausa, varredura,
)


def _envelhecer(event_id, dias):
    _sql("update pix_webhook_events set received_at = now() - make_interval(days => %s) where event_id = %s",
         (dias, event_id))


def _purgado(event_id):
    return evento(event_id)["purged_at"] is not None


def test_compra_de_8_dias_sobrevive_a_purga_e_concede_na_retomada(externo):
    """O A3 do Tester. Controle negativo: sem a guarda na purga, a compra é zerada e a venda se perde."""
    uid, cob = _venda(afiliado=False)
    ativar()
    compra = entregar("PAYMENT_RECEIVED", cob)
    _envelhecer(compra, RETENCAO_OUTBOX_DIAS + 1)
    purgar_payloads_antigos()
    assert not _purgado(compra), "a purga zerou a compra guardada na pausa"
    retomar()
    purgar_payloads_antigos()  # a purga diária roda antes da varredura que drenaria a compra
    assert not _purgado(compra), "logo depois da retomada a compra ainda não foi drenada"
    drenar_evento(compra)
    assert evento(compra)["processed_at"] is not None
    assert ler(cob["id"])["status"] == "paid" and len(_grants_pix_ativos(uid)) == 1


def test_undone_recusado_pela_pausa_sobrevive_a_purga(externo, monkeypatch):
    """O R2 do Tester: o UNDONE que reconcede (aberto com `last_error = FALHA_PAUSA`) numa pausa longa não é
    zerado; na retomada, o Pix real volta a dar acesso. Sem a guarda dele, a purga o fecha calado."""
    uid, e = undone_que_reconcede_na_pausa(externo, monkeypatch, passadas=1)
    assert evento(e)["last_error"] == FALHA_PAUSA
    _envelhecer(e, RETENCAO_OUTBOX_DIAS + 1)
    purgar_payloads_antigos()
    retomar()
    purgar_payloads_antigos()
    assert not _purgado(e)
    drenar_evento(e)
    assert evento(e)["processed_at"] is not None and len(_grants_pix_ativos(uid)) == 1


def test_a_guarda_da_purga_e_estreita(externo):
    """POSITIVO: na pausa a purga segue valendo para evento que não é compra aberta; e, 7 dias depois da
    retomada, também para a compra aberta (a guarda não vira retenção sem fim)."""
    _, cob = _venda(afiliado=False)
    processada = entregar("PAYMENT_RECEIVED", cob)
    estorno = entregar("PAYMENT_REFUNDED", cob, drenar=False)
    ativar()
    compra = entregar("PAYMENT_CONFIRMED", cob, drenar=False)
    for e in (processada, estorno, compra):
        _envelhecer(e, RETENCAO_OUTBOX_DIAS + 1)
    purgar_payloads_antigos()
    assert (_purgado(processada), _purgado(estorno), _purgado(compra)) == (True, True, False)
    _sql("update pix_pausa_concessao set retomada_em = now() - make_interval(days => %s)", (RETENCAO_OUTBOX_DIAS + 1,))
    purgar_payloads_antigos()
    assert _purgado(compra)


# ── o aviso ──────────────────────────────────────────────────────────────────

def _pausada_ha(dias=0, minutos=0, avisada_ha_min=None):
    _sql("update pix_pausa_concessao set ativa_desde = now() - make_interval(days => %s, mins => %s),"
         " avisada_em = now() - make_interval(mins => %s)", (dias, minutos, avisada_ha_min))


def test_aviso_de_pausa_longa(externo, varredura, monkeypatch):
    import core.services.admin_notify as an

    alertas = externo["alerta"]
    ativar()
    drenar_pendentes()
    assert alertas == [], "com menos de 30 min não avisa"
    _pausada_ha(minutos=31)
    monkeypatch.setattr(an, "notify_pix_alerta", lambda msg: alertas.append(msg) and False)  # canal fora
    drenar_pendentes()
    drenar_pendentes()
    assert len(alertas) == 2, "aviso que falhou tenta de novo na passada seguinte"
    monkeypatch.setattr(an, "notify_pix_alerta", lambda msg: alertas.append(msg) or True)
    drenar_pendentes()
    drenar_pendentes()
    assert len(alertas) == 3
    assert "ficam guardados, sem ser apagados" in alertas[-1] and "Faltam 6 d 23 h para os 7 dias" in alertas[-1], alertas[-1]

    _pausada_ha(dias=2, avisada_ha_min=61)  # fora do último dia: de 6 em 6 h
    drenar_pendentes()
    assert len(alertas) == 3
    _pausada_ha(dias=2, avisada_ha_min=6 * 60 + 1)
    drenar_pendentes()
    assert len(alertas) == 4
    _pausada_ha(dias=RETENCAO_OUTBOX_DIAS - 1, minutos=10, avisada_ha_min=61)  # último dia: de hora em hora
    drenar_pendentes()
    assert len(alertas) == 5 and "Faltam 23 h" in alertas[-1], alertas[-1]
    _pausada_ha(dias=RETENCAO_OUTBOX_DIAS + 1, avisada_ha_min=61)
    drenar_pendentes()
    assert len(alertas) == 6 and "Passou dos 7 dias" in alertas[-1], alertas[-1]

    retomar()
    _pausada_ha(dias=RETENCAO_OUTBOX_DIAS + 1, avisada_ha_min=24 * 60)
    drenar_pendentes()
    assert len(alertas) == 6, "pausa retomada não avisa"
    assert _sql("select pix_pausa_ativa() as a")[0]["a"] is False, "o aviso nunca reativa nem retoma"


@pytest.mark.parametrize("ja_ativa", [False, True])
def test_reativar_depois_da_retomada_recomeca_a_pausa(externo, ja_ativa):
    """`pix_pausa_ativar` sobre pausa retomada recomeça `ativa_desde` e o aviso; sobre pausa ativa, nada muda."""
    ativar()
    _pausada_ha(dias=2, avisada_ha_min=5)
    if not ja_ativa:
        retomar()
    ativar()
    linha = _sql("select ativa_desde > now() - interval '1 minute' as nova, avisada_em is null as sem_aviso,"
                 " retomada_em is null as ativa from pix_pausa_concessao")[0]
    assert dict(linha) == {"nova": not ja_ativa, "sem_aviso": not ja_ativa, "ativa": True}


def test_ativar_e_retomar_nao_esperam_o_canal_do_aviso(externo):
    """O R4 do Tester: o HTTP do aviso sai depois do commit do claim; ativar (sobre pausa ativa) e retomar
    não esperam o canal pendurado. E o aviso que demorou e deu certo fica marcado."""
    ativar()
    _pausada_ha(minutos=31)
    canal = threading.Thread(target=avisar_se_esquecida, args=(lambda linha: time.sleep(2) or True, RETENCAO_OUTBOX_DIAS))
    canal.start()
    time.sleep(0.3)
    for passo in (ativar, retomar):
        inicio = time.monotonic()
        passo()
        assert time.monotonic() - inicio < 1, f"{passo.__name__} esperou o canal do aviso"
    canal.join()
    assert _sql("select avisada_em is not null as marcada from pix_pausa_concessao")[0]["marcada"]


def test_delete_e_truncate_da_linha_sao_recusados(externo):
    """O R7 do Tester: o `delete` do roteiro antigo (ou um `truncate`) desligaria a guarda e perderia a compra."""
    _, cob = _venda(afiliado=False)
    ativar()
    compra = entregar("PAYMENT_RECEIVED", cob)
    _envelhecer(compra, RETENCAO_OUTBOX_DIAS + 1)
    for comando in ("delete from pix_pausa_concessao", "truncate pix_pausa_concessao"):
        with pytest.raises(psycopg.Error, match="não apague a linha"):
            _sql(comando)
    purgar_payloads_antigos()
    assert not _purgado(compra) and _sql("select pix_pausa_ativa() as a")[0]["a"] is True
