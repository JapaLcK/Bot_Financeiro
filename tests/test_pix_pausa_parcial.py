"""Pausa da concessão do Pix (#1574) × estorno parcial (thread do Codex no #1600).

A pausa reordena eventos que o Asaas mandou em ordem: retém a compra e deixa passar o resto. O estorno
parcial só age sobre cobrança já paga (`paid`/`paid_orphan`); drenado com a compra retida, ele fechava sem
efeito, e na retomada a compra gerava comissão cheia, sem revisão nem bloqueio de saque. Agora, com a pausa
ativa e a cobrança ainda antes do pagamento, o parcial espera como a compra (`FALHA_PAUSA`: sem tentativa,
guardado da purga) enquanto a pausa está ativa e por `JANELA_DO_PARCIAL_H` depois da retomada, e é drenado
depois dela. Só "pausa ativa" não bastava: na retomada a compra retida ainda pode drenar depois do parcial
(passada listada antes da retomada, outro drenador com a compra reservada, compra que falha uma vez).

Os irmãos (a mesma classe: evento cujo efeito depende de a compra do mesmo pagamento já ter sido aplicada)
estão no relato do PR. `PAYMENT_REFUNDED` fica de fora de propósito: drenado antes da compra, ele registra o
estorno (#1437) e a compra não concede nada, que é melhor do que concede-e-revoga.
"""
from __future__ import annotations

import threading

import psycopg

import core.services.pix_drain as pix_drain
import db.webhook_outbox as outbox
from _dreno_pix_helpers import entregar, evento, ler
from core.services.pix_drain import drenar_evento
from core.services.pix_sweeps import drenar_pendentes
from db.pix_pausa import FALHA_PAUSA, JANELA_DO_PARCIAL_H
from test_afiliado_comissao_pix import _revisao
from test_pix_pausa import _comissoes, _sql, _venda, ativar, externo, retomar, varredura  # noqa: F401

DEVOLVIDO = [{"value": 50.0, "status": "DONE"}]


def _estorno_registrado(uid, cob):
    """O desfecho certo: o parcial agiu sobre a cobrança paga (revisão aberta), e a comissão não ficou solta."""
    assert ler(cob["id"])["status"] == "refunded_partial"
    rev = _revisao(cob["external_reference"])
    assert rev is not None, "o estorno parcial se perdeu: comissão cheia, sem revisão"
    assert (rev["status"], rev["tipo"], rev["refunded_cents"]) == ("open", "partial", 5000)
    assert _comissoes(uid) == [(cob["external_reference"], "pending")]


def _compra_e_parcial_na_pausa(externo):
    uid, cob = _venda()
    externo["refunds"] = DEVOLVIDO
    ativar()
    return uid, cob, entregar("PAYMENT_RECEIVED", cob), entregar("PAYMENT_PARTIALLY_REFUNDED", cob)


def test_parcial_da_compra_retida_espera_a_retomada_e_abre_a_revisao(externo, varredura):
    """A conversa: compra e parcial chegam na pausa (o caminho rápido drena os dois); na retomada a varredura
    drena a compra e depois o parcial, e a comissão fica em revisão com o devolvido, não cheia e solta."""
    uid, cob = _venda()
    externo["refunds"] = DEVOLVIDO
    ativar()
    compra = entregar("PAYMENT_RECEIVED", cob)
    parcial = entregar("PAYMENT_PARTIALLY_REFUNDED", cob)
    varredura.extend([compra, parcial])
    drenar_pendentes()
    ev = evento(parcial)
    assert (ev["processed_at"], ev["attempts"], ev["last_error"]) == (None, 0, FALHA_PAUSA), ev
    assert ler(cob["id"])["status"] == "pending" and _comissoes(uid) == []

    retomar()
    drenar_pendentes()
    assert evento(compra)["processed_at"] and evento(parcial)["processed_at"]
    assert ler(cob["id"])["status"] == "refunded_partial"
    rev = _revisao(cob["external_reference"])
    assert rev is not None, "o estorno parcial se perdeu: comissão cheia, sem revisão"
    assert (rev["status"], rev["tipo"], rev["refunded_cents"]) == ("open", "partial", 5000)
    assert _comissoes(uid) == [(cob["external_reference"], "pending")]


def test_parcial_de_pagamento_ja_aplicado_drena_durante_a_pausa(externo):
    """POSITIVO: a compra foi aplicada antes da pausa; o parcial dela não depende de nada retido e segue na hora."""
    uid, cob = _venda()
    entregar("PAYMENT_RECEIVED", cob)
    externo["refunds"] = DEVOLVIDO
    ativar()
    parcial = entregar("PAYMENT_PARTIALLY_REFUNDED", cob)
    assert evento(parcial)["processed_at"] is not None
    assert ler(cob["id"])["status"] == "refunded_partial"
    assert _revisao(cob["external_reference"])["refunded_cents"] == 5000


def test_parcial_fora_de_ordem_sem_pausa_segue_como_antes(externo):
    """POSITIVO: sem pausa, o parcial que chega antes da compra fecha sem efeito (D1-a, decisão do dono);
    o conserto não muda isso."""
    _, cob = _venda()
    externo["refunds"] = DEVOLVIDO
    parcial = entregar("PAYMENT_PARTIALLY_REFUNDED", cob)
    assert evento(parcial)["processed_at"] is not None and ler(cob["id"])["status"] == "pending"


# ── a retomada: a compra retida ainda pode drenar depois do parcial ──────────

def test_retomada_no_meio_da_passada(externo, varredura, monkeypatch):
    """F1 do Tester: a passada lista com a pausa ativa (o lote só tem o parcial), o operador retoma logo depois
    do `select`, e a mesma passada drena o parcial com a cobrança ainda `pending`."""
    uid, cob, compra, parcial = _compra_e_parcial_na_pausa(externo)
    varredura.extend([compra, parcial])
    lista, vez = outbox.eventos_pendentes, {"n": 0}

    def lista_e_retoma(limite=100):
        ids = lista(limite)
        vez["n"] += 1
        if vez["n"] == 1:
            retomar()
        return ids

    monkeypatch.setattr(outbox, "eventos_pendentes", lista_e_retoma)
    drenar_pendentes()
    drenar_pendentes()
    assert evento(compra)["processed_at"] and evento(parcial)["processed_at"]
    _estorno_registrado(uid, cob)


def test_dois_drenadores_na_retomada(externo, varredura, monkeypatch):
    """F2 do Tester: na sobreposição do deploy, o drenador A reservou a compra e ainda não transicionou; o B pula
    a compra (`skip locked`) e pega o parcial."""
    uid, cob, compra, parcial = _compra_e_parcial_na_pausa(externo)
    retomar()
    chegou, solta = threading.Event(), threading.Event()
    real = pix_drain.conferir_valor
    monkeypatch.setattr(pix_drain, "conferir_valor", lambda *a, **k: (chegou.set(), solta.wait(20), real(*a, **k))[2])
    a = threading.Thread(target=drenar_evento, args=(compra,))
    a.start()
    assert chegou.wait(20)
    drenar_evento(parcial)  # o drenador B
    solta.set()
    a.join(30)
    drenar_evento(parcial)  # a passada seguinte
    assert evento(compra)["processed_at"] and evento(parcial)["processed_at"]
    _estorno_registrado(uid, cob)


def test_compra_falha_uma_vez_na_retomada(externo, varredura, monkeypatch):
    """F3 do Tester: na retomada a compra falha uma vez antes da transição; o parcial vem logo atrás na mesma passada."""
    uid, cob, compra, parcial = _compra_e_parcial_na_pausa(externo)
    varredura.extend([compra, parcial])
    retomar()
    real, vez = pix_drain.transicionar, {"n": 0}

    def falha_uma(*a, **k):
        vez["n"] += 1
        if vez["n"] == 1:
            raise psycopg.OperationalError("conexão caiu")
        return real(*a, **k)

    monkeypatch.setattr(pix_drain, "transicionar", falha_uma)
    drenar_pendentes()
    assert evento(compra)["attempts"] == 1 and evento(parcial)["processed_at"] is None
    drenar_pendentes()
    assert evento(compra)["processed_at"] and evento(parcial)["processed_at"]
    _estorno_registrado(uid, cob)


def test_depois_da_janela_o_parcial_fora_de_ordem_volta_ao_d1a(externo):
    """POSITIVO: passada a janela depois da retomada, o parcial que chega antes da compra fecha sem efeito, como
    sem pausa nenhuma (D1-a). Dentro da janela, o mesmo parcial espera."""
    _, cob = _venda()
    externo["refunds"] = DEVOLVIDO
    ativar()
    retomar()
    dentro = entregar("PAYMENT_PARTIALLY_REFUNDED", cob)
    assert evento(dentro)["last_error"] == FALHA_PAUSA and evento(dentro)["processed_at"] is None
    _sql("update pix_pausa_concessao set retomada_em = now() - make_interval(hours => %s)", (JANELA_DO_PARCIAL_H + 1,))
    fora = entregar("PAYMENT_PARTIALLY_REFUNDED", cob)
    drenar_evento(dentro)
    assert evento(fora)["processed_at"] and evento(dentro)["processed_at"]
    assert ler(cob["id"])["status"] == "pending" and _revisao(cob["external_reference"]) is None
