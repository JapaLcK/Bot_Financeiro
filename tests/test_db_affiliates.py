"""Testes do programa de afiliados (db/affiliates.py).

Cobrem: criação de afiliado, atribuição de referral (com bloqueios de
auto-indicação/dupla atribuição/afiliado desativado), comissão idempotente
por fatura, carência, saque mínimo, pagamento e rejeição de saque, estorno.
"""
import uuid

import pytest

import db
from db import ensure_user
from db.affiliates import (
    MIN_PAYOUT_CENTS,
    create_affiliate,
    get_affiliate_by_code,
    get_affiliate_by_user,
    get_affiliate_stats,
    list_affiliate_payouts,
    mark_payout_paid,
    record_commission_for_invoice,
    record_referral,
    reject_payout,
    request_payout,
    reverse_commission,
    set_affiliate_status,
)


@pytest.fixture()
def referred_user_id(user_id):
    """Segundo usuário (o indicado). O `user_id` do conftest vira o afiliado."""
    uid = int(uuid.uuid4().int % 10_000_000_000)
    ensure_user(uid)
    yield uid
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("delete from accounts where user_id = %s", (uid,))
            cur.execute("delete from users where id = %s", (uid,))
        conn.commit()


def _make_available(commission_id: int):
    """Antecipa a carência da comissão pra ontem (testa o bucket disponível)."""
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update affiliate_commissions set available_at = now() - interval '1 day' where id = %s",
                (commission_id,),
            )
        conn.commit()


def _inv(prefix: str = "in_test") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def test_create_affiliate_gera_codigo_e_eh_idempotente(user_id):
    a1 = create_affiliate(user_id)
    assert a1["code"] and len(a1["code"]) == 8
    assert a1["status"] == "active"
    assert a1["commission_bps"] == 1000

    a2 = create_affiliate(user_id)  # segunda chamada devolve o mesmo
    assert a2["id"] == a1["id"]
    assert get_affiliate_by_code(a1["code"].lower())["id"] == a1["id"]  # lookup case-insensitive


def test_referral_atribui_uma_vez_e_bloqueia_auto_indicacao(user_id, referred_user_id):
    aff = create_affiliate(user_id)

    assert record_referral(aff["code"], referred_user_id) is True
    # segunda atribuição do mesmo user não sobrescreve (primeiro ganha)
    assert record_referral(aff["code"], referred_user_id) is False
    # auto-indicação bloqueada
    assert record_referral(aff["code"], user_id) is False
    # código inexistente não explode
    assert record_referral("NAOEXISTE", referred_user_id) is False

    assert get_affiliate_stats(aff["id"])["referrals"] == 1


def test_referral_nao_atribui_com_afiliado_desativado(user_id, referred_user_id):
    aff = create_affiliate(user_id)
    set_affiliate_status(aff["id"], "disabled")
    assert record_referral(aff["code"], referred_user_id) is False
    set_affiliate_status(aff["id"], "active")
    assert record_referral(aff["code"], referred_user_id) is True


def test_comissao_10pct_idempotente_e_em_carencia(user_id, referred_user_id):
    aff = create_affiliate(user_id)
    record_referral(aff["code"], referred_user_id)

    invoice = _inv()
    c = record_commission_for_invoice(referred_user_id, invoice, 1990)  # R$ 19,90
    assert c is not None
    assert c["amount_cents"] == 199  # 10%

    # retry do webhook com a mesma fatura não duplica
    assert record_commission_for_invoice(referred_user_id, invoice, 1990) is None

    stats = get_affiliate_stats(aff["id"])
    assert stats["held_cents"] == 199       # recém-criada fica em carência
    assert stats["available_cents"] == 0

    _make_available(c["id"])
    stats = get_affiliate_stats(aff["id"])
    assert stats["held_cents"] == 0
    assert stats["available_cents"] == 199


def test_comissao_nao_gera_sem_referral_nem_com_afiliado_desativado(user_id, referred_user_id):
    # sem referral → nada
    assert record_commission_for_invoice(referred_user_id, _inv(), 1990) is None

    aff = create_affiliate(user_id)
    record_referral(aff["code"], referred_user_id)
    set_affiliate_status(aff["id"], "disabled")
    # afiliado desativado → para de acumular ("vitalício até eu dizer chega")
    assert record_commission_for_invoice(referred_user_id, _inv(), 1990) is None

    set_affiliate_status(aff["id"], "active")
    assert record_commission_for_invoice(referred_user_id, _inv(), 1990) is not None

    # valor zero (fatura de trial) → nada
    assert record_commission_for_invoice(referred_user_id, _inv(), 0) is None


def test_saque_minimo_e_ciclo_pagamento(user_id, referred_user_id):
    aff = create_affiliate(user_id)
    record_referral(aff["code"], referred_user_id)

    # 1 comissão de R$ 19,90 → R$ 1,99 disponível: abaixo do mínimo
    c = record_commission_for_invoice(referred_user_id, _inv(), 1990)
    _make_available(c["id"])
    with pytest.raises(ValueError):
        request_payout(aff["id"])

    # fatura anual de R$ 199 → +R$ 19,90; mais 30 mensais → passa de R$ 50
    big = record_commission_for_invoice(referred_user_id, _inv(), 19900)
    _make_available(big["id"])
    for _ in range(15):
        ci = record_commission_for_invoice(referred_user_id, _inv(), 1990)
        _make_available(ci["id"])

    total_expected = 199 + 1990 + 15 * 199
    assert total_expected >= MIN_PAYOUT_CENTS

    payout = request_payout(aff["id"], pix_key_enc=None)
    assert payout["amount_cents"] == total_expected
    assert payout["status"] == "requested"

    stats = get_affiliate_stats(aff["id"])
    assert stats["available_cents"] == 0                      # tudo travado no saque
    assert stats["requested_cents"] == total_expected

    # segundo saque com um em aberto → erro
    with pytest.raises(ValueError):
        request_payout(aff["id"])

    assert mark_payout_paid(payout["id"], note="pix ok") is True
    assert mark_payout_paid(payout["id"]) is False            # já processado

    stats = get_affiliate_stats(aff["id"])
    assert stats["requested_cents"] == 0
    assert stats["paid_cents"] == total_expected
    assert list_affiliate_payouts(aff["id"])[0]["status"] == "paid"


def test_saque_rejeitado_devolve_saldo(user_id, referred_user_id):
    aff = create_affiliate(user_id)
    record_referral(aff["code"], referred_user_id)
    big = record_commission_for_invoice(referred_user_id, _inv(), 100000)  # R$ 1000 → R$ 100
    _make_available(big["id"])

    payout = request_payout(aff["id"])
    assert reject_payout(payout["id"], note="dados errados") is True

    stats = get_affiliate_stats(aff["id"])
    assert stats["available_cents"] == 10000   # voltou pro disponível
    assert stats["requested_cents"] == 0


def test_estorno_de_comissao(user_id, referred_user_id):
    aff = create_affiliate(user_id)
    record_referral(aff["code"], referred_user_id)
    c = record_commission_for_invoice(referred_user_id, _inv(), 1990)

    assert reverse_commission(c["id"]) is True
    assert reverse_commission(c["id"]) is False  # já estornada

    stats = get_affiliate_stats(aff["id"])
    assert stats["held_cents"] == 0
    assert stats["available_cents"] == 0

    # comissão dentro de saque em andamento não estorna
    big = record_commission_for_invoice(referred_user_id, _inv(), 100000)
    _make_available(big["id"])
    payout = request_payout(aff["id"])
    assert reverse_commission(big["id"]) is False
    reject_payout(payout["id"])


def test_get_affiliate_by_user(user_id):
    assert get_affiliate_by_user(user_id) is None
    created = create_affiliate(user_id)
    assert get_affiliate_by_user(user_id)["id"] == created["id"]
