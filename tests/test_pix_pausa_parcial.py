"""Pausa da concessão do Pix (#1574) × estorno parcial (thread do Codex no #1600).

A pausa reordena eventos que o Asaas mandou em ordem: retém a compra e deixa passar o resto. O estorno
parcial só age sobre cobrança já paga (`paid`/`paid_orphan`); drenado com a compra retida, ele fechava sem
efeito, e na retomada a compra gerava comissão cheia, sem revisão nem bloqueio de saque. Agora, com a pausa
ativa e a cobrança ainda antes do pagamento, o parcial espera a retomada como a compra (`FALHA_PAUSA`: sem
tentativa, guardado da purga) e é drenado depois dela, na ordem de chegada.

Os irmãos (a mesma classe: evento cujo efeito depende de a compra do mesmo pagamento já ter sido aplicada)
estão no relato do PR. `PAYMENT_REFUNDED` fica de fora de propósito: drenado antes da compra, ele registra o
estorno (#1437) e a compra não concede nada, que é melhor do que concede-e-revoga.
"""
from __future__ import annotations

from _dreno_pix_helpers import entregar, evento, ler
from core.services.pix_sweeps import drenar_pendentes
from db.pix_pausa import FALHA_PAUSA
from test_afiliado_comissao_pix import _revisao
from test_pix_pausa import _comissoes, _venda, ativar, externo, retomar, varredura  # noqa: F401

DEVOLVIDO = [{"value": 50.0, "status": "DONE"}]


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
