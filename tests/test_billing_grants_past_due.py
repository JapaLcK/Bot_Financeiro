"""
tests/test_billing_grants_past_due.py — células 5 e 6 da matriz do §4.1.1 D
(docs/plano_pix_anual_asaas.md): a carência do `past_due` e o teto que a fecha.

Casos 9i, 9j e 9k do §16.

Arquivo próprio porque é a única situação em que a varredura tira acesso de
alguém cuja assinatura o gateway diz estar VIVA — e porque as duas células
decidem sem depender de haver grant alvo, o que as separa do resto da matriz.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from _billing_grants_helpers import (
    FakeStripeSubs, conta as _conta, ler as _ler, set_customer,
)
from core.services.billing_access import recompute_entitlement
from db.plan_grants import upsert_grant


def _cenario_past_due(user_id: int, monkeypatch, dias_apos_o_fim: int,
                      com_grant: bool = True):
    """Cobertura vencida há `dias_apos_o_fim` e assinatura `past_due` no Stripe.

    **O `current_period_end` do `past_due` é FUTURO**, e a fixture reproduz
    isso: a doc do Stripe diz que o período acompanha a CRIAÇÃO da fatura, não o
    pagamento — a fatura do período novo nasce, a cobrança falha, e o
    `current_period_end` já aponta para o fim desse período novo. (Uma versão
    anterior desta fixture usava data passada, "porque é assim que o Stripe
    representa atraso"; era falso.)

    A fixture com data futura é a que discrimina o erro caro: se alguém fizer o
    ramo `past_due` cair no reparo, o grant será esticado para um período que
    NINGUÉM PAGOU, e o teste do dia 16 fica vermelho.
    """
    agora = datetime.now(timezone.utc)
    fim_da_cobertura = agora - timedelta(days=dias_apos_o_fim)
    # `plan_expires_at` = fim da cobertura, que é o que a projeção teria escrito
    # a partir do grant antes de ele ficar para trás. Importa porque é a `base`
    # do teto quando não há grant ativo (célula 5/6 do §4.1.1 D) — pôr uma data
    # futura aqui daria carência eterna e o teste mediria o cenário errado.
    _conta(user_id, "pro", fim_da_cobertura, "past_due")
    set_customer(user_id, f"cus_{user_id}")
    if com_grant:
        upsert_grant(user_id, "stripe", f"sub_pd_{user_id}", "pro",
                     agora - timedelta(days=395), fim_da_cobertura, 1_000)
    else:
        # Sem grant CASÁVEL: um `legacy` revogado pela supersessão, que é como
        # a base migrada fica depois do primeiro `invoice.paid`.
        upsert_grant(user_id, "legacy", f"legacy:{user_id}", "pro",
                     agora - timedelta(days=395), fim_da_cobertura, 0)
        from db.plan_grants import revoke_grant
        revoke_grant(user_id, "legacy", f"legacy:{user_id}",
                     "superseded_by_stripe", 1_000)

    # Período FUTURO: a fatura do novo ciclo foi criada e não foi paga.
    periodo_novo = int((fim_da_cobertura + timedelta(days=30)).timestamp())
    atrasada = {"id": f"sub_pd_{user_id}", "status": "past_due",
                "current_period_end": periodo_novo,
                "items": {"data": [{"current_period_end": periodo_novo}]}}
    monkeypatch.setitem(__import__("sys").modules, "stripe",
                        FakeStripeSubs(ativa=atrasada, status_viva="past_due"))


def test_9i_past_due_dentro_do_teto_SEGURA_o_acesso(user_id, monkeypatch):
    """Dentro da carência de 15 dias o acesso é mantido: durante o dunning a
    assinatura está viva e o cliente ainda pode pagar. Nada é escrito."""
    _cenario_past_due(user_id, monkeypatch, dias_apos_o_fim=14)

    alertas: list[str] = []
    import core.services.admin_notify as admin_notify
    monkeypatch.setattr(admin_notify, "_send", lambda m: alertas.append(m) or True)

    assert recompute_entitlement(user_id, origem="varredura") is None
    assert _ler(user_id)["plan"] == "pro", "cortou acesso dentro da carência"
    assert alertas == [], "dunning é estado esperado; alerta diário vira ruído"


def test_9j_past_due_alem_do_teto_REDUZ_com_assinatura_viva(user_id, monkeypatch):
    """Passado o teto, reduz MESMO com a assinatura viva no Stripe. Sem isso a
    cauda é infinita: `past_due` conta como viva e o `current_period_end` dela
    nunca avança, então nunca haveria o que reparar."""
    _cenario_past_due(user_id, monkeypatch, dias_apos_o_fim=16)

    assert recompute_entitlement(user_id, origem="varredura") == {
        "plan": "free", "plan_expires_at": None}
    assert _ler(user_id)["plan"] == "free"


def test_9k_past_due_alem_do_teto_SEM_grant_ativo_ainda_reduz(user_id, monkeypatch):
    """A célula que o teto não alcançava: `past_due` há 400 dias e nenhum grant
    ATIVO a esticar.

    Enquanto o teto dependia do alvo, este caso caía em `alvo is None →
    nao_reduz` e a conta ficava `pro` para sempre. E ele deixaria de ser exótico
    exatamente por causa do conserto do B-4: filtrar `status='active'` faz toda
    a base migrada cair aqui. O teto é propriedade do ATRASO, não do casamento.
    """
    _cenario_past_due(user_id, monkeypatch, dias_apos_o_fim=400, com_grant=False)

    assert recompute_entitlement(user_id, origem="varredura") == {
        "plan": "free", "plan_expires_at": None}
    assert _ler(user_id)["plan"] == "free"


def test_9k_positivo_past_due_dentro_do_teto_SEM_grant_ativo_segura(user_id, monkeypatch):
    """Mesma célula, do lado de dentro do teto: sem alvo, mas em carência, o
    acesso é segurado. Prova que a linha 5 também deixou de depender do alvo."""
    _cenario_past_due(user_id, monkeypatch, dias_apos_o_fim=10, com_grant=False)

    assert recompute_entitlement(user_id, origem="varredura") is None
    assert _ler(user_id)["plan"] == "pro"
