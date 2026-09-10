"""A janela de acesso e o `CheckViolation` retentável (§7, §17.1 pendência 3).

`pix_charges_pago_tem_janela` existe no banco desde o 1b-A e é **condicional**:
com titular vivo levanta; com `user_id is null` aplica em silêncio (a exceção é
por titular ausente, não por status). Duas obrigações caem no dreno:

  1. **sempre** passar `access_starts_at`/`access_expires_at` na transição para
     `paid`;
  2. tratar `CheckViolation` como falha RETENTÁVEL — `attempts`, `last_error`, e
     sair **sem** carimbar `processed_at`. Engolir e fechar o evento é dinheiro
     dentro, acesso nenhum, para sempre.

A janela é derivada do próprio snapshot, sem rechamar `plano_da_cobranca`:
chamá-lo aqui poderia levantar `CoberturaJaPaga` sobre dinheiro que JÁ entrou,
que é o erro irreversível.

CONTROLES NEGATIVOS MEDIDOS:

  * engula a `CheckViolation` e chame `marcar_processado` →
    `test_janela_ausente_e_falha_retentavel` VERMELHO (evento fechado, dinheiro
    dentro, acesso nenhum);
  * tire a exceção do órfão (`user_id is null`) do CHECK, ou faça o dreno passar
    janela ao órfão → `test_orfao_vai_a_paid_orphan_sem_janela` VERMELHO.

POSITIVO do grupo: `test_pagamento_normal_carimba_os_dois_e_fecha_o_evento`. Sem
ele, um dreno que **recusasse toda transição para `paid`** passaria nos dois
negativos acima.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from _billing_grants_helpers import conta, garantir_system_event_logs
from _dreno_pix_helpers import efeitos, entregar, evento, ler, mundo_externo, nova_cobranca
from db.connection import get_conn
from db.plan_grants import upsert_grant


@pytest.fixture()
def externo(monkeypatch):
    garantir_system_event_logs()
    return mundo_externo(monkeypatch)


def test_pagamento_normal_carimba_os_dois_e_fecha_o_evento(user_id, externo):
    """D4-b, POSITIVO do grupo."""
    conta(user_id, "free", None)
    cobranca = nova_cobranca(user_id)
    event_id = entregar("PAYMENT_RECEIVED", cobranca)

    linha = ler(cobranca["id"])
    assert linha["status"] == "paid"
    assert linha["paid_at"] and linha["access_starts_at"] and linha["access_expires_at"]
    assert (linha["access_expires_at"] - linha["access_starts_at"]).days == 365
    assert evento(event_id)["processed_at"] is not None
    assert "grant" in efeitos(cobranca["asaas_payment_id"])


def test_janela_ausente_e_falha_retentavel(user_id, externo, monkeypatch):
    """D4-a, o caso que DISCRIMINA.

    Com o cálculo da janela sabotado (é assim que a ausência é encenada sem
    reescrever `transicionar`), o `CHECK` do banco levanta. O evento tem de
    ficar ABERTO: `processed_at` nulo, `attempts` 1, `last_error` com o nome da
    classe, cobrança ainda `pending`, nenhum efeito.
    """
    import core.services.pix_drain as drain

    conta(user_id, "free", None)
    cobranca = nova_cobranca(user_id)
    monkeypatch.setattr(drain, "janela_de_acesso", lambda _c: (None, None))
    event_id = entregar("PAYMENT_RECEIVED", cobranca)

    linha, ev = ler(cobranca["id"]), evento(event_id)
    assert linha["status"] == "pending", "avançou sem janela — o CHECK não pegou"
    assert ev["processed_at"] is None, "evento FECHADO com dinheiro dentro"
    assert ev["attempts"] == 1
    assert ev["last_error"] == "CheckViolation"
    assert efeitos(cobranca["asaas_payment_id"]) == set()


def test_orfao_vai_a_paid_orphan_sem_janela(user_id, externo):
    """D4-c, o limite MEDIDO: titular já excluído (§13.4).

    A cobrança sobrevive com `user_id is null` (FK `on delete set null`), o
    destino vira `paid_orphan`, **sem janela e sem exceção**, nenhum efeito de
    compra roda e o `orphan_notified` fica registrado.
    """
    conta(user_id, "free", None)
    cobranca = nova_cobranca(user_id)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("update pix_charges set user_id = null where id = %s",
                    (cobranca["id"],))
        conn.commit()

    event_id = entregar("PAYMENT_RECEIVED", cobranca)
    linha = ler(cobranca["id"])
    assert linha["status"] == "paid_orphan"
    assert linha["paid_at"] and linha["access_starts_at"] is None
    assert efeitos(cobranca["asaas_payment_id"]) == {"orphan_notified"}
    assert externo["ga4"] == 0 and externo["email"] == 0
    assert len(externo["alerta"]) == 1
    assert evento(event_id)["processed_at"] is not None


# ── D4-d: as relações do §7, uma fixture por relação ─────────────────────────

def test_compra_nova_comeca_agora(user_id, externo):
    """Sem crédito, sem Stripe e sem grant vivo: começa AGORA."""
    conta(user_id, "free", None)
    cobranca = nova_cobranca(user_id)
    entregar("PAYMENT_RECEIVED", cobranca)
    inicio = ler(cobranca["id"])["access_starts_at"]
    assert abs((datetime.now(timezone.utc) - inicio).total_seconds()) < 120


def test_renovacao_emenda_no_fim_do_grant_vivo(user_id, externo):
    """Renovação/compra agendada: a cobertura é CONTÍGUA — começa quando o
    grant ativo termina, e não hoje. Começar hoje queimaria os dias que o
    cliente já pagou."""
    conta(user_id, "pro_max", None)
    fim = datetime.now(timezone.utc) + timedelta(days=40)
    upsert_grant(user_id, "stripe", "sub_x", "pro_max",
                 datetime.now(timezone.utc) - timedelta(days=1), fim, 1)
    cobranca = nova_cobranca(user_id)
    entregar("PAYMENT_RECEIVED", cobranca)
    assert abs((ler(cobranca["id"])["access_starts_at"] - fim).total_seconds()) < 5


def test_upgrade_com_credito_comeca_agora(user_id, externo):
    """Upgrade Pix→Pix: há crédito da cobertura restante embutido no preço, então
    o acesso novo vale JÁ — emendar no fim cobraria o upgrade e entregaria depois."""
    conta(user_id, "pro", None)
    fim = datetime.now(timezone.utc) + timedelta(days=90)
    upsert_grant(user_id, "pix", "antigo", "pro",
                 datetime.now(timezone.utc) - timedelta(days=1), fim, 1)
    cobranca = nova_cobranca(user_id, credit_cents=12000, amount_cents=37900)
    entregar("PAYMENT_RECEIVED", cobranca)
    inicio = ler(cobranca["id"])["access_starts_at"]
    assert abs((datetime.now(timezone.utc) - inicio).total_seconds()) < 120


def test_migracao_do_stripe_comeca_no_fim_do_periodo_pago(user_id, externo):
    """Migração: o ano Pix começa quando o período de cartão JÁ PAGO termina —
    senão o cliente paga duas vezes o mesmo período."""
    conta(user_id, "pro_max", None)
    fim = datetime.now(timezone.utc) + timedelta(days=20)
    cobranca = nova_cobranca(user_id, stripe_subscription_id="sub_mig",
                             stripe_period_end_at=fim)
    # O efeito `stripe_cancel` da migração é do checkout (C6) e falha alto aqui;
    # o que este caso mede é a JANELA, e ela é decidida na transição, antes.
    entregar("PAYMENT_RECEIVED", cobranca)
    assert abs((ler(cobranca["id"])["access_starts_at"] - fim).total_seconds()) < 5
