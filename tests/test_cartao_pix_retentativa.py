"""Cartão × Pix quando o dreno FALHA entre `stripe_cancel` e `grant` e retenta.

O par de `tests/test_cartao_depois_do_pix.py` para a janela que só existe com
falha: o `stripe_cancel` já está registrado, o `grant` não, e o webhook do
cartão chega no meio. Mesma cena (banco real, os dois webhooks de verdade).

Controles negativos (rodados, ver o relato do PR):
  · guarda só por grant vigente (sem `paga_cobrindo_agora`) → R1 e RC vermelhos;
  · `gravar_stripe_period_end` com `and stripe_subscription_id is not null` de
    volta no `where` (a linha descoberta não grava a janela) → R2, RB, RC vermelhos;
  · alerta da 6ª falha com o texto de antes                  → R3 vermelho;
  · `paga_cobrindo_agora` sem o `not exists` do grant revogado → RA vermelho;
  · `_stripe_cancel` sem tratar assinatura morta, ou tratando sem voltar a
    janela, ou sem o `coalesce` que grava a assinatura achada → RB vermelho.
Positivos: R2 — janela FUTURA (migração) não recusa o cartão que a sustenta;
RA — grant Pix revogado não recusa nem o checkout nem o webhook; RC (2º) —
a migração com janela futura abre o checkout.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from fastapi import HTTPException

import db
import frontend.finance_bot_websocket_custom as dashboard
from _dreno_pix_helpers import entregar, evento, ler, nova_cobranca
from core.admin_dashboard import _gravar_grant_do_admin
from core.services import pix_drain, pix_drain_effects
from db.connection import get_conn
from test_billing_webhook_lifecycle import _post
from test_cartao_depois_do_pix import (  # noqa: F401 — `cena` é fixture
    _checkout, _fatura, _fim, _grants, _sub, cena)


def _grant_falha_uma_vez(monkeypatch):
    original = pix_drain_effects.EXECUTORES["grant"]
    vezes = []

    def _grant(cobranca, evt):
        vezes.append(1)
        if len(vezes) == 1:
            raise RuntimeError("blip de banco")
        return original(cobranca, evt)
    monkeypatch.setitem(pix_drain_effects.EXECUTORES, "grant", _grant)


def _cartao_conclui(c, sub_id):
    assert _post(c.client, c.fake, _fatura(c.uid, sub_id, 4990)).status_code == 200
    assert _post(c.client, c.fake, _checkout(c.uid, sub_id, amount=4990)).status_code == 200


def test_r1_cartao_que_conclui_antes_do_grant_retentado_e_recusado(cena, monkeypatch):
    """`stripe_cancel` não viu cartão (no-op registrado), o `grant` falhou, e a
    sessão aberta antes do Pix conclui no intervalo: sem a cobrança paga na
    guarda, o cartão materializava e renovava em 30 dias por cima do Pix."""
    c = cena
    db.set_stripe_customer(c.uid, "cus_r1")
    _grant_falha_uma_vez(monkeypatch)
    eid = entregar("PAYMENT_RECEIVED", nova_cobranca(c.uid))
    assert evento(eid)["processed_at"] is None

    _sub(c.fake, "sub_r1", "active", customer="cus_r1")
    _cartao_conclui(c, "sub_r1")
    pix_drain.drenar_evento(eid)

    assert [s for s, _ in c.fake.cancelados] == ["sub_r1"]
    assert _grants(c.uid, "stripe") == []
    assert len(_grants(c.uid, "pix")) == 1


def test_r2_janela_adiada_sobrevive_a_retentativa(cena, monkeypatch):
    """Caminho B com o webhook do cartão atrasado: o `stripe_cancel` acha a
    assinatura viva, agenda e ADIA a janela; o `grant` falha; o cartão
    materializa; a retentativa tem de ler a janela adiada, e não a de agora."""
    c = cena
    db.set_stripe_customer(c.uid, "cus_r2")
    sub = _sub(c.fake, "sub_r2", "active", customer="cus_r2")
    _grant_falha_uma_vez(monkeypatch)
    eid = entregar("PAYMENT_RECEIVED", nova_cobranca(c.uid))
    assert c.fake.modificados == [("sub_r2", {"cancel_at_period_end": True})]

    _cartao_conclui(c, "sub_r2")
    pix_drain.drenar_evento(eid)

    assert c.fake.cancelados == []
    assert len(_grants(c.uid, "stripe")) == 1
    (pix,) = _grants(c.uid, "pix")
    assert pix["starts_at"] >= _fim(sub) - timedelta(minutes=1), (pix, _fim(sub))


def test_r3_stripe_fora_sem_assinatura_conhecida_o_alerta_nao_mente(cena):
    c = cena
    db.set_stripe_customer(c.uid, "cus_r3")

    def _fora(**kw):
        raise RuntimeError("stripe fora")
    c.fake.Subscription.list = staticmethod(_fora)
    entregar("PAYMENT_RECEIVED", nova_cobranca(c.uid), tentativas=5)

    assert len(_grants(c.uid, "pix")) == 1
    (alerta,) = c.externo["alerta"]
    assert "não consegui consultar o Stripe" in alerta, alerta
    assert "cancelamento no Stripe não entrou" not in alerta, alerta
    assert f"`{c.uid}` tem assinatura viva" in alerta, alerta


def _checkout_recusa_por_pix(uid) -> bool:
    """O `create-checkout` de verdade, com um Stripe que não acha assinatura.
    Quem não é recusado precisa de `stripe_customer_id`: o `MagicMock` não cria."""
    stripe_mod = MagicMock()
    stripe_mod.Subscription.list.return_value = {"data": []}
    try:
        asyncio.run(dashboard._billing_checkout_for_user(
            stripe_mod, uid, "pro_max", "monthly", "price_x"))
    except HTTPException as exc:
        return isinstance(exc.detail, dict) and exc.detail.get("error") == "pix_active"
    return False


def test_ra_grant_pix_revogado_pelo_admin_nao_recusa_o_cartao(cena):
    """O admin revoga TODOS os grants e deixa a cobrança `paid` com a janela
    vigente: ela não pode continuar dizendo "o Pix cobre"."""
    c = cena
    db.set_stripe_customer(c.uid, "cus_ra")   # o checkout que abre não cria customer
    entregar("PAYMENT_RECEIVED", nova_cobranca(c.uid))
    with get_conn() as conn, conn.cursor() as cur:
        _gravar_grant_do_admin(cur, c.uid, "free", 0)
        conn.commit()
    assert _grants(c.uid, "pix") == []

    assert not _checkout_recusa_por_pix(c.uid)
    _sub(c.fake, "sub_ra", "trialing")
    assert _post(c.client, c.fake, _checkout(c.uid, "sub_ra")).status_code == 200
    assert c.fake.cancelados == []
    assert len(_grants(c.uid, "stripe")) == 1


def test_rc_create_checkout_recusa_pix_pago_com_o_grant_por_nascer(cena, monkeypatch):
    c = cena
    monkeypatch.setitem(pix_drain_effects.EXECUTORES, "grant", _sempre_falha)
    eid = entregar("PAYMENT_RECEIVED", nova_cobranca(c.uid))
    assert evento(eid)["processed_at"] is None and _grants(c.uid, "pix") == []
    assert _checkout_recusa_por_pix(c.uid)


def test_rc_create_checkout_abre_na_migracao_com_janela_futura(cena):
    c = cena
    db.set_stripe_customer(c.uid, "cus_rc")
    _sub(c.fake, "sub_rc", "active", customer="cus_rc")
    entregar("PAYMENT_RECEIVED", nova_cobranca(c.uid))
    assert _grants(c.uid, "pix")[0]["starts_at"] > datetime.now(timezone.utc)
    assert not _checkout_recusa_por_pix(c.uid)


def _sempre_falha(cobranca, evt):
    raise RuntimeError("blip de banco")


def test_rb_assinatura_cancelada_entre_a_consulta_e_o_modify(cena):
    """O dreno acha o cartão vivo, o webhook dele o recusa (caminho A) antes do
    `modify`, que falha; a janela já foi ADIADA. A retentativa vê a assinatura
    morta: sem `modify`, e o Pix volta a valer agora, não no fim de um cartão
    que não existe mais."""
    c = cena
    db.set_stripe_customer(c.uid, "cus_rb")
    _sub(c.fake, "sub_rb", "active", customer="cus_rb")
    retrieve, modify = c.fake.Subscription.retrieve, c.fake.Subscription.modify

    def _retrieve_e_o_webhook_cancela(sub_id):
        foto = dict(retrieve(sub_id))
        c.fake.subs[sub_id]["status"] = "canceled"
        return foto

    def _modify(sub_id, **kw):
        if c.fake.subs[sub_id]["status"] == "canceled":
            raise RuntimeError("A canceled subscription can only update cancellation_details")
        return modify(sub_id, **kw)
    c.fake.Subscription.retrieve = staticmethod(_retrieve_e_o_webhook_cancela)
    c.fake.Subscription.modify = staticmethod(_modify)
    cob = nova_cobranca(c.uid)
    eid = entregar("PAYMENT_RECEIVED", cob)
    assert evento(eid)["processed_at"] is None
    assert ler(cob["id"])["access_starts_at"] > datetime.now(timezone.utc) + timedelta(days=20)

    pix_drain.drenar_evento(eid)
    assert evento(eid)["processed_at"] is not None
    assert c.fake.modificados == []
    (pix,) = _grants(c.uid, "pix")
    assert dashboard._grant_pix_vigente(c.uid) is not None, pix
    assert pix["ends_at"] - pix["starts_at"] == timedelta(days=365)
