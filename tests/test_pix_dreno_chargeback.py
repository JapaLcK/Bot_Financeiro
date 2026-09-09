"""Chargeback no dreno: o curinga saiu, e nenhum dos três eventos revoga na
abertura (§17.1, pendência 2 — decisão do dono).

A linha `PAYMENT_CHARGEBACK_* → chargeback + [revoke]` do §8.2 casava TRÊS
eventos de significados diferentes, e o pior deles é o
`PAYMENT_AWAITING_CHARGEBACK_REVERSAL`: pela doc oficial do Asaas ele significa
que **NÓS GANHAMOS** a disputa. Sob o curinga, o evento que anuncia a vitória era
o que revogava o acesso do cliente.

Agora são três linhas nomeadas, nenhuma transiciona e nenhuma dispara efeito.
Quem revoga quando PERDEMOS é o `PAYMENT_REFUNDED` que chega depois — caminho que
já existe e é testado aqui pelo positivo.

**Consequência a registrar:** o estado `chargeback` deixa de ser produzido por
webhook. Ele continua no `check pix_charges_status_valido` e continua alcançável
por reparo manual — a linha `chargeback` da matriz do §11 passa a descrever só
esse caminho.

CONTROLES NEGATIVOS MEDIDOS:

  * reponha o curinga — e ele tem de ser `"CHARGEBACK" in event_type`, não
    `startswith("PAYMENT_CHARGEBACK")`: `PAYMENT_AWAITING_CHARGEBACK_REVERSAL`
    **não** começa com esse prefixo, e a primeira tentativa de mutação passou
    verde por isso, medindo nada. Com o curinga certo →
    `test_vitoria_na_disputa_nao_revoga` e
    `test_nome_novo_de_chargeback_cai_em_desconhecido` VERMELHOS.
    `test_perdemos_a_disputa_o_refunded_revoga` continua VERDE nas duas versões,
    e é por isso que ele é o POSITIVO do grupo e não o discriminante.

CEGUEIRA DECLARADA: os três nomes vieram da doc oficial do Asaas e **não foram
verificados contra o Sandbox** (§18). Se a plataforma usar outro nome, ele cai em
`asaas_evento_desconhecido` — que é o lado seguro, e é o que
`test_nome_novo_de_chargeback_cai_em_desconhecido` fixa.
"""
from __future__ import annotations

import pytest

from _billing_grants_helpers import conta, garantir_system_event_logs
from _dreno_pix_helpers import efeitos, entregar, evento, ler, mundo_externo, nova_cobranca
from db.plan_grants import list_grants


@pytest.fixture()
def externo(monkeypatch):
    garantir_system_event_logs()
    return mundo_externo(monkeypatch)


def _ativos(uid: int) -> list[dict]:
    return [g for g in list_grants(uid) if g["source"] == "pix" and g["status"] == "active"]


def _paga(user_id) -> dict:
    conta(user_id, "free", None)
    cobranca = nova_cobranca(user_id)
    entregar("PAYMENT_RECEIVED", cobranca)
    return cobranca


def test_vitoria_na_disputa_nao_revoga(user_id, externo):
    """D2-a — o caso que DISCRIMINA. `AWAITING_CHARGEBACK_REVERSAL` é a nossa
    vitória: nada muda, e o evento fecha normalmente."""
    cobranca = _paga(user_id)
    event_id = entregar("PAYMENT_AWAITING_CHARGEBACK_REVERSAL", cobranca)

    assert ler(cobranca["id"])["status"] == "paid"
    assert len(_ativos(user_id)) == 1, "o evento da VITÓRIA revogou o acesso"
    assert "revoke" not in efeitos(cobranca["asaas_payment_id"])
    assert evento(event_id)["processed_at"] is not None


def test_chargeback_aberto_alerta_e_nao_mexe(user_id, externo):
    """D2-b. A abertura da disputa não corta acesso — desfazer revogação é mais
    caro que esperar, e o estorno pode ser negado."""
    cobranca = _paga(user_id)
    externo["alerta"].clear()
    entregar("PAYMENT_CHARGEBACK_REQUESTED", cobranca)

    assert ler(cobranca["id"])["status"] == "paid"
    assert len(_ativos(user_id)) == 1
    assert len(externo["alerta"]) == 1


def test_nome_novo_de_chargeback_cai_em_desconhecido(user_id, externo):
    """D2-c. Um nome que a plataforma invente amanhã **não** pode ser tratado
    como chargeback por prefixo: ele cai em desconhecido, sem transição e sem
    efeito. É o oposto exato do curinga."""
    cobranca = _paga(user_id)
    event_id = entregar("PAYMENT_CHARGEBACK_INVENTADO_2027", cobranca)

    assert ler(cobranca["id"])["status"] == "paid"
    assert "revoke" not in efeitos(cobranca["asaas_payment_id"])
    assert evento(event_id)["processed_at"] is not None


def test_perdemos_a_disputa_o_refunded_revoga(user_id, externo):
    """D2-d, POSITIVO do grupo: abrimos a disputa e PERDEMOS — o
    `PAYMENT_REFUNDED` que chega depois revoga, como sempre revogou.

    Ele fica verde com e sem o curinga, **de propósito e medido**: é o controle
    que impede o grupo de passar num dreno que nunca revoga nada. Por isso ele
    afirma o RESULTADO (acesso revogado, `revoke` registrado) e não o status —
    sob o curinga a revogação acontece cedo demais e o status final é
    `chargeback`, e um controle que ficasse vermelho aí deixaria de ser controle.
    O status correto (`refunded`) é fixado por `test_estorno_total_revoga_o_acesso`
    em `tests/test_pix_dreno_estorno.py`."""
    cobranca = _paga(user_id)
    entregar("PAYMENT_CHARGEBACK_REQUESTED", cobranca)
    entregar("PAYMENT_REFUNDED", cobranca)

    assert "revoke" in efeitos(cobranca["asaas_payment_id"])
    assert _ativos(user_id) == []


def test_estorno_em_andamento_e_negado_nao_mexem(user_id, externo):
    """Os dois irmãos do §8.2: `REFUND_IN_PROGRESS` não revoga (o estorno ainda
    pode ser negado) e `REFUND_DENIED` não tem o que reverter. Os dois são
    CONHECIDOS-e-no-op — encher o log de desconhecidos com evento esperado é
    como esse log deixa de ser lido."""
    cobranca = _paga(user_id)
    for tipo in ("PAYMENT_REFUND_IN_PROGRESS", "PAYMENT_REFUND_DENIED"):
        event_id = entregar(tipo, cobranca)
        assert evento(event_id)["processed_at"] is not None, tipo
    assert ler(cobranca["id"])["status"] == "paid"
    assert len(_ativos(user_id)) == 1
