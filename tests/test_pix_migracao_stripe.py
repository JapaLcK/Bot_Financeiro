"""A migração Stripe → Pix no PAGAMENTO (§9 passo 4).

Este arquivo existe porque o efeito `stripe_cancel` era um `RuntimeError`
declarado: no 1b-A ele falhava ALTO na migração, porque dependia de duas coisas
que só nasciam no checkout — a releitura do `current_period_end` e um escritor
de `stripe_period_end_at`. As duas existem agora, e a marca `ponytail:` saiu.

**A ordem dos dois passos é o conteúdo**, e por isso a lista `feito` é a
asserção principal:

  1. lê o `current_period_end` de verdade e GRAVA;
  2. só então agenda `cancel_at_period_end=True`.

E é `modify(cancel_at_period_end=True)`, **nunca `Subscription.delete`**: deletar
cortaria hoje o acesso que o cliente já pagou até o fim do mês, e o grant Pix só
começa depois disso — ele ficaria sem produto nenhum no intervalo.

CONTROLES NEGATIVOS MEDIDOS:

  * troque o `modify` por `delete` → `test_migracao_agenda_e_nao_deleta` vermelho;
  * engula a exceção do Stripe → `test_falha_no_stripe_vira_attempts_e_nao_fecha`
    vermelho, e é dinheiro dentro com o cartão ainda cobrando, calado;
  * faça o efeito rodar na venda comum →
    `test_venda_comum_nao_fala_com_o_stripe` vermelho.

POSITIVO do grupo: `test_venda_comum_nao_fala_com_o_stripe` — ele reprova a
versão que trata TODA venda como migração, que é a esmagadora maioria delas.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from _billing_grants_helpers import conta, garantir_system_event_logs
from _dreno_pix_helpers import efeitos, entregar, ler, mundo_externo, nova_cobranca
from db.plan_grants import list_grants


@pytest.fixture()
def externo(monkeypatch):
    garantir_system_event_logs()
    return mundo_externo(monkeypatch)


@pytest.fixture()
def stripe_falso(monkeypatch):
    """Stripe por CONTADOR, com a ordem das chamadas registrada."""
    import sys
    import types

    estado = {"feito": [], "erro": None,
              "fim": datetime.now(timezone.utc) + timedelta(days=17)}

    class _Sub:
        @staticmethod
        def retrieve(sub_id):
            estado["feito"].append(f"retrieve:{sub_id}")
            if estado["erro"]:
                raise estado["erro"]
            return {"id": sub_id,
                    "current_period_end": int(estado["fim"].timestamp())}

        @staticmethod
        def modify(sub_id, **kw):
            estado["feito"].append(f"modify:{sub_id}:{sorted(kw.items())}")
            return {"id": sub_id}

        @staticmethod
        def delete(sub_id, **kw):  # pragma: no cover - existe para o negativo
            estado["feito"].append(f"delete:{sub_id}")
            return {"id": sub_id}

    falso = types.ModuleType("stripe")
    falso.Subscription = _Sub
    monkeypatch.setitem(sys.modules, "stripe", falso)
    return estado


def test_migracao_agenda_e_nao_deleta(user_id, externo, stripe_falso):
    """DISCRIMINA. Os dois passos do §9, na ordem, e o `stripe_period_end_at`
    RECONFIRMADO na linha.

    A janela de acesso NÃO é recalculada aqui: ela foi decidida na transição
    para `paid`, a partir da estimativa que o checkout gravou. O grant nasce no
    efeito SEGUINTE e leria uma linha movida debaixo dele.
    """
    conta(user_id, "free", None)
    estimativa = datetime.now(timezone.utc) + timedelta(days=15)
    cobranca = nova_cobranca(user_id, stripe_subscription_id="sub_mig",
                             stripe_period_end_at=estimativa)
    entregar("PAYMENT_RECEIVED", cobranca)

    assert stripe_falso["feito"] == [
        "retrieve:sub_mig",
        "modify:sub_mig:[('cancel_at_period_end', True)]",
    ], stripe_falso["feito"]

    linha = ler(cobranca["id"])
    assert linha["status"] == "paid"
    # Comparação por EPOCH inteiro, e não por objeto: o `current_period_end` do
    # Stripe tem precisão de segundos, e o banco devolve o instante no fuso da
    # conexão. Comparar `datetime` cru mediria fuso e microssegundo — o quinto
    # referencial de data deste app, que já custou um PR (§CLAUDE.md, memória).
    assert int(linha["stripe_period_end_at"].timestamp()) == int(
        stripe_falso["fim"].timestamp()), (
        "o período não foi reconfirmado — ficou a estimativa do checkout"
    )
    assert linha["stripe_cancel_scheduled_at"] is not None
    assert "stripe_cancel" in efeitos(cobranca["asaas_payment_id"])

    # O acesso comprado começa no fim do período JÁ PAGO no cartão, e não hoje:
    # é o que impede o cliente de pagar duas vezes o mesmo mês.
    grant = [g for g in list_grants(user_id) if g["source"] == "pix"][0]
    assert grant["starts_at"] >= estimativa - timedelta(minutes=1)


def test_venda_comum_nao_fala_com_o_stripe(user_id, externo, stripe_falso):
    """POSITIVO do grupo, e é a esmagadora maioria das vendas.

    `stripe_subscription_id is null` → no-op REGISTRADO: o par entra em
    `pix_payment_effects` e o laço segue para o `grant`. Sem este caso, a versão
    que trata toda venda como migração passaria no teste acima.
    """
    conta(user_id, "free", None)
    cobranca = nova_cobranca(user_id)
    entregar("PAYMENT_RECEIVED", cobranca)

    assert stripe_falso["feito"] == []
    assert "stripe_cancel" in efeitos(cobranca["asaas_payment_id"])
    assert "grant" in efeitos(cobranca["asaas_payment_id"])
    assert ler(cobranca["id"])["status"] == "paid"


def test_falha_no_stripe_vira_attempts_e_nao_fecha(user_id, externo, stripe_falso):
    """DISCRIMINA. O Stripe recusando é falha RETENTÁVEL, nunca silêncio.

    `stripe_cancel` é o PRIMEIRO efeito da lista de propósito: falhando, o
    `grant` não roda, o evento fica aberto na outbox e a próxima passada retoma.
    Engolir aqui deixaria o cartão cobrando o período que o Pix já pagou, sem
    uma linha de log que alguém leia.
    """
    from _dreno_pix_helpers import evento

    conta(user_id, "free", None)
    stripe_falso["erro"] = RuntimeError("stripe fora do ar")
    cobranca = nova_cobranca(user_id, stripe_subscription_id="sub_mig2",
                             stripe_period_end_at=datetime.now(timezone.utc))
    event_id = entregar("PAYMENT_RECEIVED", cobranca)

    linha_evt = evento(event_id)
    assert linha_evt["processed_at"] is None, "fechou o evento com o efeito falhando"
    assert linha_evt["attempts"] == 1
    assert efeitos(cobranca["asaas_payment_id"]) == set(), (
        "registrou efeito que não rodou — o grant sairia na próxima passada "
        "sem o cancelamento do cartão"
    )
    assert [g for g in list_grants(user_id) if g["source"] == "pix"] == []
