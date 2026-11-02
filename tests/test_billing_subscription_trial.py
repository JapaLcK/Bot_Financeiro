"""GET /billing/subscription devolve `trial_disponivel` (rótulo dos CTAs da /).

A regra é a do checkout (`core.services.trial_offer.dias_de_trial` > 0: no v2, um
trial por telefone na vida), e o valor depende SÓ da conta do `user_id` da sessão — o caso de dois
usuários prova que um telefone queimado de A não vaza para B (CLAUDE.md §0).
Falha de banco = campo ausente: o front trata como desconhecido e não troca nada.

CONTROLES (CLAUDE.md §3), medidos:
  * negativo — trocar o `dias_de_trial` por `7` deixa
    `test_telefone_que_ja_usou_o_trial_e_false` vermelho; não capturar o
    `TrialEligibilityError` deixa `test_falha_de_banco_omite_o_campo` vermelho;
    voltar a ler só o telefone deixa `test_v2_desligado_segue_o_checkout…` vermelho;
    não capturar o `ValueError` deixa `test_pro_trial_days_invalido…` vermelho.
  * positivo — `test_telefone_livre_sem_assinatura_e_true`: sem ele, um handler
    que devolvesse sempre False passaria em todos os outros.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

import frontend.finance_bot_websocket_custom as dashboard
from _billing_grants_helpers import conta
from db import ensure_user
from db.connection import get_conn
from db.pix_charges import criar_cobranca
from db_support import invalidate_auth_user_cache


def _subscription(uid: int) -> dict:
    return asyncio.run(dashboard.billing_subscription(user_id=uid))


def _telefone(uid: int, usado: bool = False) -> str:
    ph = f"ph_trial_{uid}_{uuid.uuid4().hex[:8]}"
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("update auth_accounts set phone_hash=%s where user_id=%s", (ph, uid))
        if usado:
            cur.execute("insert into plan_trials (phone_hash) values (%s) on conflict do nothing", (ph,))
        conn.commit()
    invalidate_auth_user_cache(uid)
    return ph


@pytest.fixture()
def limpa_trials():
    hashes: list[str] = []
    yield hashes
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("delete from plan_trials where phone_hash = any(%s)", (hashes,))
        conn.commit()


def test_telefone_livre_sem_assinatura_e_true(user_id):
    conta(user_id, "free", None)
    _telefone(user_id)
    assert _subscription(user_id) == {"active": False, "trial_disponivel": True}


def test_telefone_que_ja_usou_o_trial_e_false(user_id, limpa_trials):
    conta(user_id, "free", None)
    limpa_trials.append(_telefone(user_id, usado=True))
    assert _subscription(user_id) == {"active": False, "trial_disponivel": False}


def test_sem_telefone_e_false(user_id):
    conta(user_id, "free", None)
    assert _subscription(user_id) == {"active": False, "trial_disponivel": False}


def test_isolamento_o_trial_usado_de_a_nao_vaza_para_b(user_id, limpa_trials):
    outro = int(uuid.uuid4().int % 10_000_000_000)
    ensure_user(outro)
    conta(user_id, "free", None)
    conta(outro, "free", None)
    limpa_trials.append(_telefone(user_id, usado=True))
    _telefone(outro)
    assert _subscription(user_id)["trial_disponivel"] is False
    assert _subscription(outro)["trial_disponivel"] is True


def test_falha_de_banco_omite_o_campo(user_id, monkeypatch):
    import db.plans

    conta(user_id, "free", None)
    _telefone(user_id)

    def quebra(uid):
        raise db.plans.TrialEligibilityError("db down")

    monkeypatch.setattr(db.plans, "motivo_trial_indisponivel", quebra)
    assert _subscription(user_id) == {"active": False}


def test_assinatura_ativa_pix_tambem_leva_o_campo(user_id):
    from db.plan_grants import upsert_grant

    conta(user_id, "pro_max", None)
    _telefone(user_id)
    cobranca = criar_cobranca(user_id, public_token=uuid.uuid4().hex, plan="pro_max",
                              plan_stored="pro_max", price_cents=49900,
                              credit_cents=0, amount_cents=49900, duration_days=365)
    inicio = datetime.now(timezone.utc) - timedelta(days=5)
    upsert_grant(user_id, "pix", str(cobranca["id"]), "pro_max",
                 inicio, inicio + timedelta(days=365), 1, last_event_id="e1")
    corpo = _subscription(user_id)
    assert corpo["active"] is True and corpo["gateway"] == "pix", corpo
    assert corpo["trial_disponivel"] is True, corpo


def test_v2_desligado_segue_o_checkout_e_nao_o_telefone(user_id, limpa_trials, monkeypatch):
    """Freio de emergência (PLANS_V2_ENABLED=0): o checkout dá `PRO_TRIAL_DAYS` sem
    olhar telefone, então o rótulo também não pode olhar (§0.7, `dias_de_trial`)."""
    monkeypatch.setenv("PLANS_V2_ENABLED", "0")
    monkeypatch.setenv("PRO_TRIAL_DAYS", "7")
    conta(user_id, "free", None)
    limpa_trials.append(_telefone(user_id, usado=True))
    assert _subscription(user_id) == {"active": False, "trial_disponivel": True}
    monkeypatch.setenv("PRO_TRIAL_DAYS", "0")
    assert _subscription(user_id) == {"active": False, "trial_disponivel": False}


def test_pro_trial_days_invalido_omite_o_campo_sem_derrubar_a_rota(user_id, monkeypatch):
    """A /precos, o pix-checkout e o /painel leem esta rota: env torta não pode virar 500."""
    monkeypatch.setenv("PLANS_V2_ENABLED", "0")
    monkeypatch.setenv("PRO_TRIAL_DAYS", "sete")
    conta(user_id, "free", None)
    assert _subscription(user_id) == {"active": False}


def _stripe_falso(monkeypatch, uid: int, assinatura) -> None:
    """Customer no Stripe e o `_find_active_subscription` falso: `Exception` = Stripe fora."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("update auth_accounts set stripe_customer_id = 'cus_teste' where user_id = %s", (uid,))
        conn.commit()
    invalidate_auth_user_cache(uid)
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_MENSAL", "price_plus_m")

    def achar(_stripe, _cust):
        if assinatura is Exception:
            raise dashboard.StripeLookupError("fora")
        return assinatura

    monkeypatch.setattr(dashboard, "_find_active_subscription", achar)


_ATIVA = {"items": {"data": [{"price": {"id": "price_plus_m"}, "current_period_end": 1900000000}]}}


@pytest.mark.parametrize("ramo, assinatura, esperado", [
    ("vitalicio", None, {"active": True, "lifetime": True}),
    ("degraded", Exception, {"active": False, "degraded": True}),
    ("sem_assinatura_viva", None, {"active": False}),
    ("stripe_ativo", _ATIVA, {"active": True, "gateway": "stripe", "plan": "plus"}),
])
def test_todo_ramo_leva_o_campo(user_id, monkeypatch, ramo, assinatura, esperado):
    """Os ramos do Stripe e o vitalício (o Pix e o "sem chave" estão nos casos acima).
    *Negativo: tirar o `**trial` de qualquer `return` deixa o caso dele vermelho.*"""
    conta(user_id, "pro", None, status="grandfathered" if ramo == "vitalicio" else "active")
    _telefone(user_id)
    if ramo != "vitalicio":
        _stripe_falso(monkeypatch, user_id, assinatura)
    corpo = _subscription(user_id)
    assert {k: corpo.get(k) for k in esperado} == esperado, corpo
    assert corpo["trial_disponivel"] is True, corpo
