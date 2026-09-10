"""Estorno no dreno: o parcial não revoga e não concede; o total não é
reconcedido (§12, §17.1 pendências 1, 3 e 4).

As duas decisões do dono que estão medidas aqui, e que este arquivo existe para
não deixar voltar:

  * **D1 — o parcial só transiciona de `paid`/`paid_orphan`.** De qualquer outro
    estado é ZERO transição e ZERO efeito. A consequência é o cenário que o dono
    mediu em 2026-09-08: o `PARTIALLY_REFUNDED` fora de ordem encontra a cobrança
    em `pending`, **não a move**, e o `RECEIVED` que chega depois casa
    `pending → paid` pelo caminho normal e concede. A "correção óbvia"
    (acrescentar `refunded_partial` às origens do `RECEIVED`) nunca chega a ser
    necessária — e ela é pior, porque torna a transição RE-APLICÁVEL numa
    reentrega, recarimbando `paid_at` e reescrevendo a janela.
  * **D3 — dinheiro devolvido não é reconcedido.** Com `(payment_id, 'revoke')`
    já registrado, os efeitos de COMPRA não rodam. É a regra "quem concede é o
    pagamento, nunca o estorno" lida do outro lado, para o estorno TOTAL que
    chega ANTES do `RECEIVED`. Usa só `pix_payment_effects`, que nunca é purgada.

CONTROLES NEGATIVOS MEDIDOS (um a um, com o resto do grupo verde):

  * dê a `PAYMENT_PARTIALLY_REFUNDED` origem irrestrita (rotear por tipo de
    evento só, como o §8.2 escrevia) → `test_parcial_fora_de_ordem_nao_move_a_cobranca`
    VERMELHO na primeira asserção;
  * faça o parcial disparar `[revoke]` → `test_parcial_em_cobranca_paga_nao_revoga`
    VERMELHO no grant;
  * apague a condição de D3 em `pix_drain._decidir` →
    `test_received_depois_de_estorno_total_nao_concede` VERMELHO (grant concedido
    sobre dinheiro devolvido).

POSITIVOS do grupo: `test_estorno_total_revoga_o_acesso` (D1-c) e
`test_received_sem_estorno_anterior_concede` (D3-b). Sem eles o grupo passaria
num dreno que **nunca concede nada**, que é pior que o bug.
"""
from __future__ import annotations

import pytest

from _billing_grants_helpers import conta, garantir_system_event_logs
from _dreno_pix_helpers import efeitos, entregar, ler, mundo_externo, nova_cobranca
from db.plan_grants import list_grants


@pytest.fixture()
def externo(monkeypatch):
    garantir_system_event_logs()
    return mundo_externo(monkeypatch)


def _grants_ativos(uid: int) -> list[dict]:
    return [g for g in list_grants(uid) if g["source"] == "pix" and g["status"] == "active"]


# ── D1: o parcial ────────────────────────────────────────────────────────────

def test_parcial_fora_de_ordem_nao_move_a_cobranca(user_id, externo):
    """D1-a, o caso que DISCRIMINA.

    O parcial chega antes do `RECEIVED` numa cobrança `pending`: nada acontece.
    O `RECEIVED` seguinte casa a origem normal e concede — uma única vez.
    """
    conta(user_id, "free", None)
    cobranca = nova_cobranca(user_id)

    entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    assert ler(cobranca["id"])["status"] == "pending"
    assert efeitos(cobranca["asaas_payment_id"]) == set()

    entregar("PAYMENT_RECEIVED", cobranca)
    depois = ler(cobranca["id"])
    assert depois["status"] == "paid"
    assert depois["access_starts_at"] and depois["access_expires_at"]
    assert len(_grants_ativos(user_id)) == 1
    assert externo["ga4"] == 1 and externo["email"] == 1


def test_parcial_em_cobranca_paga_nao_revoga(user_id, externo):
    """D1-b. De `paid` o parcial transiciona e ALERTA — e o acesso continua."""
    conta(user_id, "free", None)
    cobranca = nova_cobranca(user_id)
    entregar("PAYMENT_RECEIVED", cobranca)
    externo["alerta"].clear()

    entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    assert ler(cobranca["id"])["status"] == "refunded_partial"
    assert len(_grants_ativos(user_id)) == 1, "o parcial revogou — não podia"
    assert "revoke" not in efeitos(cobranca["asaas_payment_id"])
    assert len(externo["alerta"]) == 1
    assert "499.00" in externo["alerta"][0], "o alerta tem de trazer o valor"


def test_estorno_total_revoga_o_acesso(user_id, externo):
    """D1-c, POSITIVO do grupo: o caminho que DEVE revogar continua revogando."""
    conta(user_id, "free", None)
    cobranca = nova_cobranca(user_id)
    entregar("PAYMENT_RECEIVED", cobranca)
    assert len(_grants_ativos(user_id)) == 1

    entregar("PAYMENT_REFUNDED", cobranca)
    assert ler(cobranca["id"])["status"] == "refunded"
    assert "revoke" in efeitos(cobranca["asaas_payment_id"])
    assert _grants_ativos(user_id) == []


def test_dois_parciais_somando_tudo_alertam_com_o_acumulado(user_id, externo,
                                                            monkeypatch):
    """D1-d. Sem limiar percentual em código: dois parciais de 50% mantêm o
    acesso e produzem DOIS alertas, o segundo com o acumulado = valor da cobrança.

    O acumulado vem do Asaas (`buscar_pagamento`), porque ele não é derivável do
    que sobrevive à minimização e à purga da outbox.
    """
    import core.services.asaas as asaas

    conta(user_id, "free", None)
    cobranca = nova_cobranca(user_id)
    entregar("PAYMENT_RECEIVED", cobranca)
    externo["alerta"].clear()

    devolvido = {"n": 0}

    def _pagamento(_pid):
        devolvido["n"] += 1
        return {"refunds": [{"value": 249.5}] * devolvido["n"]}

    monkeypatch.setattr(asaas, "buscar_pagamento", _pagamento)
    entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)

    assert ler(cobranca["id"])["status"] == "refunded_partial"
    assert len(_grants_ativos(user_id)) == 1, "dinheiro todo de volta e acesso vivo"
    assert len(externo["alerta"]) == 2
    assert "R$ 249.50" in externo["alerta"][0]
    assert "R$ 499.00" in externo["alerta"][1]


def test_asaas_fora_do_ar_nao_inventa_numero(user_id, externo, monkeypatch):
    """O alerta do parcial sai com `acumulado indisponível` quando o provedor
    não responde — nunca com um número derivado do que temos, que seria errado."""
    import core.services.asaas as asaas

    conta(user_id, "free", None)
    cobranca = nova_cobranca(user_id)
    entregar("PAYMENT_RECEIVED", cobranca)
    externo["alerta"].clear()

    def _explode(_pid):
        raise asaas.AsaasApiError("provedor fora", status_code=None)

    monkeypatch.setattr(asaas, "buscar_pagamento", _explode)
    entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    assert "acumulado indisponível" in externo["alerta"][0]


# ── D3: o estorno total fora de ordem ────────────────────────────────────────

def test_received_depois_de_estorno_total_nao_concede(user_id, externo):
    """D3-a, o caso que DISCRIMINA.

    `PAYMENT_REFUNDED` chega antes do `RECEIVED` numa cobrança `pending`: ele não
    transiciona (a origem não casa), mas o efeito `revoke` roda assim mesmo — os
    efeitos NUNCA são zerados pela transição (§8.2). O `revoke` registrado é o
    que impede o `RECEIVED` atrasado de conceder acesso sobre dinheiro devolvido.
    """
    conta(user_id, "free", None)
    cobranca = nova_cobranca(user_id)

    entregar("PAYMENT_REFUNDED", cobranca)
    assert "revoke" in efeitos(cobranca["asaas_payment_id"])
    assert _grants_ativos(user_id) == []
    externo["alerta"].clear()

    entregar("PAYMENT_RECEIVED", cobranca)
    assert _grants_ativos(user_id) == [], "concedeu acesso sobre dinheiro devolvido"
    assert efeitos(cobranca["asaas_payment_id"]) == {"revoke"}
    assert externo["ga4"] == 0 and externo["capi"] == 0 and externo["email"] == 0
    assert len(externo["alerta"]) == 1


def test_received_sem_estorno_anterior_concede(user_id, externo):
    """D3-b, POSITIVO do par: sem `revoke` registrado o caminho legítimo roda
    inteiro. Sem ele, a condição de D3 poderia virar "nunca concede nada" e o
    grupo continuaria verde."""
    conta(user_id, "free", None)
    cobranca = nova_cobranca(user_id)

    entregar("PAYMENT_RECEIVED", cobranca)
    assert len(_grants_ativos(user_id)) == 1
    assert efeitos(cobranca["asaas_payment_id"]) == {
        "stripe_cancel", "grant", "ga4", "capi", "email"}
    assert externo["ga4"] == 1 and externo["capi"] == 1 and externo["email"] == 1


def test_reentrega_do_received_nao_reexecuta_nada(user_id, externo):
    """A reentrega com `event_id` NOVO não dobra e-mail nem receita: quem barra é
    o registro do efeito por PAGAMENTO, não a transição."""
    conta(user_id, "free", None)
    cobranca = nova_cobranca(user_id)
    entregar("PAYMENT_RECEIVED", cobranca)
    entregar("PAYMENT_RECEIVED", cobranca)
    assert externo["ga4"] == 1 and externo["capi"] == 1 and externo["email"] == 1
    assert len(_grants_ativos(user_id)) == 1
