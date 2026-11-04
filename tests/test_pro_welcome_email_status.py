"""E-mail pós-checkout do cartão: o texto segue o status da assinatura.

Pelo handler (`POST /billing/webhook`, harness de `test_billing_webhook_lifecycle`),
com o `send_pro_welcome_email` de verdade e só o `send_email` capturado: a ordem
dos argumentos do `_fire_email` é posicional e erra calada, então chamar a função
direto não provaria o webhook.

Antes, todo checkout dizia "período grátis até X — só tem cobrança depois",
inclusive o `active` (telefone que já usou o teste: cobrado na hora).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from _billing_grants_helpers import garantir_system_event_logs
from core.services import admin_notify
from core.services import email_service as es
from test_billing_webhook_lifecycle import _cleanup_trial, _fake_sub, _post, _setup
from test_ebook_webhook import _SUB, _checkout

_CADERNO = "Se você levou um caderno junto"


@pytest.fixture(autouse=True)
def _event_logs():
    garantir_system_event_logs()


def _boas_vindas(monkeypatch, uid, status, *, ebook):
    uid, client, fake = _setup(monkeypatch, f"pwst-{status}-{int(ebook)}-{uid}")
    caixa: list[dict] = []
    monkeypatch.setattr(es, "send_email", lambda **k: caixa.append(k) or True)
    monkeypatch.setattr(es, "send_founder_email_once", lambda *a, **k: True)
    monkeypatch.setattr(admin_notify, "notify_new_pro", lambda *a, **k: None)
    sub = _fake_sub(status)
    try:
        r = _post(client, fake, _checkout(uid, ebook=ebook), subs={_SUB: sub})
        assert r.status_code == 200, r.text
    finally:
        _cleanup_trial(uid)
    (msg,) = [m for m in caixa if m["subject"].startswith("🐷 Tá dentro")]
    fim = es._fmt_brl_date(datetime.fromtimestamp(sub["current_period_end"], timezone.utc))
    return (msg["html_body"], msg["text_body"]), fim


def test_t1_trialing_sem_caderno_promete_o_periodo_gratis(user_id, monkeypatch):
    """POSITIVO: o caminho do trial continua prometendo o trial."""
    corpos, fim = _boas_vindas(monkeypatch, user_id, "trialing", ebook=False)
    for c in corpos:
        assert "período grátis" in c.lower() and fim in c, c
        assert "a cobrança do plano só vem depois" in c, c
        assert "foi feita hoje" not in c and "caderno" not in c, c


def test_t2_active_diz_que_cobrou_hoje_e_nao_promete_trial(user_id, monkeypatch):
    corpos, fim = _boas_vindas(monkeypatch, user_id, "active", ebook=False)
    for c in corpos:
        assert "primeira cobrança do plano foi feita hoje" in c and fim in c, c
        assert "reembolso integral" in c and es.SUPPORT_EMAIL in c, c
        assert "período grátis" not in c.lower() and "só tem cobrança depois" not in c, c
        assert "caderno" not in c, c


def test_t3_trialing_com_caderno_avisa_que_o_caderno_ja_foi_cobrado(user_id, monkeypatch):
    corpos, _ = _boas_vindas(monkeypatch, user_id, "trialing", ebook=True)
    for c in corpos:
        assert f"{_CADERNO}, ele já foi cobrado hoje, à parte do plano." in c, c
        assert "a cobrança do plano só vem depois" in c, c


def test_t4_active_com_caderno(user_id, monkeypatch):
    corpos, _ = _boas_vindas(monkeypatch, user_id, "active", ebook=True)
    for c in corpos:
        assert f"{_CADERNO}, ele também foi cobrado hoje, à parte do plano." in c, c
        assert "primeira cobrança do plano foi feita hoje" in c, c


@pytest.mark.parametrize("status", ["past_due", "incomplete"])
def test_t5_outro_status_nao_promete_trial_nem_cobranca(user_id, monkeypatch, status):
    corpos, _ = _boas_vindas(monkeypatch, user_id, status, ebook=False)
    for c in corpos:
        assert "foi registrada" in c and "/conta" in c, c
        assert "período grátis" not in c.lower() and "foi feita hoje" not in c, c
