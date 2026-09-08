"""
tests/test_billing_dunning_webhook.py — o RELÓGIO da inadimplência no webhook.

T1/T2/T3 do corte por cartão em atraso (`core/services/billing_dunning`):
o `invoice.payment_failed` carimba `auth_accounts.past_due_since`, a reentrega
do MESMO evento não reinicia a contagem nem duplica o e-mail, e os três ramos
de "voltou a valer" limpam o relógio.

**Por que arquivo separado**: o plano pedia estes testes dentro do
`test_billing_webhook_lifecycle.py` (que já tem os helpers), mas o portão de
tamanho de `tests/test_max_lines_python.py` reprova arquivo novo acima de 350
linhas e aquele já estava em 335. Os helpers vêm por IMPORT do módulo vizinho —
uma fonte só (§0.7), sem uma segunda cópia de `_FakeStripe`/`_setup`/`_post`
para divergir. O diretório `tests/` está no `sys.path` do pytest (é assim que
`_billing_grants_helpers` já é importado).
"""
from __future__ import annotations

import db
from db.connection import get_conn
from test_billing_webhook_lifecycle import (
    _T_LIFE,
    _cleanup_trial,
    _fake_sub,
    _post,
    _setup,
)


def _relogio(uid: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select past_due_since from auth_accounts where user_id=%s", (uid,))
            return cur.fetchone()["past_due_since"]


def _conta_emails(monkeypatch) -> list:
    """Conta as chamadas de send_payment_failed_email. O webhook a importa
    DENTRO do ramo, então trocar o atributo do módulo basta."""
    from core.services import email_service
    chamadas = []
    monkeypatch.setattr(email_service, "send_payment_failed_email",
                        lambda *a, **k: chamadas.append(a) or True)
    return chamadas


def test_T1_payment_failed_carimba_o_relogio(user_id, monkeypatch):
    """T1: a falha de cobrança grava past_due_since — o relógio dos 7 dias."""
    uid, client, fake = _setup(monkeypatch, f"pds1-{user_id}")
    _conta_emails(monkeypatch)
    try:
        assert _relogio(uid) is None
        r = _post(client, fake,
                  {"type": "invoice.payment_failed", "id": "evt_pds_1",
                   "created": _T_LIFE,
                   "data": {"object": {"metadata": {"finbot_user_id": str(uid)},
                                       "attempt_count": 1}}})
        assert r.status_code == 200, r.text
        assert db.get_auth_user(uid)["last_payment_status"] == "past_due"
        assert _relogio(uid) is not None
        # A coluna tem de chegar ao gate pelo get_auth_user (a lista explícita
        # de colunas do db_support). Sem isso o gate nunca a veria.
        assert db.get_auth_user(uid)["past_due_since"] == _relogio(uid)
    finally:
        _cleanup_trial(uid)


def test_T2_reentrega_nao_reinicia_o_relogio_nem_duplica_email(user_id, monkeypatch):
    """T2: o MESMO evento entregue 2x não move o relógio, e o e-mail sai 1x.

    A Stripe manda um `payment_failed` por smart retry E reentrega o mesmo
    evento em cima de 5xx. Antes deste PR o e-mail saía em TODOS.

    Controle negativo declarado: tire o `and past_due_since is null` do
    `claim_past_due_since` (db/plans.py) e este teste fica VERMELHO nas duas
    metades (relógio avança, e-mail sai 2x); T1 e T3 continuam verdes.
    """
    uid, client, fake = _setup(monkeypatch, f"pds2-{user_id}")
    emails = _conta_emails(monkeypatch)
    evento = {"type": "invoice.payment_failed", "id": "evt_pds_2",
              "created": _T_LIFE,
              "data": {"object": {"metadata": {"finbot_user_id": str(uid)},
                                  "attempt_count": 1}}}
    try:
        assert _post(client, fake, evento).status_code == 200
        primeiro = _relogio(uid)
        assert primeiro is not None
        assert len(emails) == 1

        assert _post(client, fake, evento).status_code == 200
        assert _relogio(uid) == primeiro          # NÃO reiniciou
        assert len(emails) == 1                   # e não duplicou
    finally:
        _cleanup_trial(uid)


def test_T3_invoice_paid_limpa_o_relogio(user_id, monkeypatch):
    """T3a: pagou → desbloqueio automático (relógio zerado)."""
    uid, client, fake = _setup(monkeypatch, f"pds3a-{user_id}")
    _conta_emails(monkeypatch)
    try:
        assert _post(client, fake,
                     {"type": "invoice.payment_failed", "id": "evt_pds_3a",
                      "created": _T_LIFE,
                      "data": {"object": {"metadata": {"finbot_user_id": str(uid)},
                                          "attempt_count": 2}}}).status_code == 200
        assert _relogio(uid) is not None
        r = _post(client, fake,
                  {"type": "invoice.paid", "id": "evt_pds_3a_paid",
                   "created": _T_LIFE + 1,
                   "data": {"object": {"metadata": {"finbot_user_id": str(uid)},
                                       "subscription": "sub_pds3a",
                                       "amount_paid": 0, "id": "in_pds3a"}}},
                  subs={"sub_pds3a": _fake_sub("active")})
        assert r.status_code == 200, r.text
        assert _relogio(uid) is None
    finally:
        _cleanup_trial(uid)


def test_T3_checkout_completed_limpa_o_relogio(user_id, monkeypatch):
    """T3b: assinatura nova por cima de um ciclo de inadimplência limpa."""
    uid, client, fake = _setup(monkeypatch, f"pds3b-{user_id}")
    _conta_emails(monkeypatch)
    try:
        assert _post(client, fake,
                     {"type": "invoice.payment_failed", "id": "evt_pds_3b",
                      "created": _T_LIFE,
                      "data": {"object": {"metadata": {"finbot_user_id": str(uid)},
                                          "attempt_count": 1}}}).status_code == 200
        assert _relogio(uid) is not None
        r = _post(client, fake,
                  {"type": "checkout.session.completed", "id": "evt_pds_3b_co",
                   "created": _T_LIFE + 1,
                   "data": {"object": {"metadata": {"finbot_user_id": str(uid)},
                                       "subscription": "sub_pds3b",
                                       "id": "cs_pds3b"}}},
                  subs={"sub_pds3b": _fake_sub("active")})
        assert r.status_code == 200, r.text
        assert _relogio(uid) is None
    finally:
        _cleanup_trial(uid)


def test_T3_subscription_deleted_limpa_o_relogio(user_id, monkeypatch):
    """T3c: a assinatura morreu — o relógio não tem mais o que medir."""
    uid, client, fake = _setup(monkeypatch, f"pds3c-{user_id}")
    _conta_emails(monkeypatch)
    try:
        assert _post(client, fake,
                     {"type": "invoice.payment_failed", "id": "evt_pds_3c",
                      "created": _T_LIFE,
                      "data": {"object": {"metadata": {"finbot_user_id": str(uid)},
                                          "attempt_count": 3}}}).status_code == 200
        assert _relogio(uid) is not None
        r = _post(client, fake,
                  {"type": "customer.subscription.deleted", "id": "evt_pds_3c_del",
                   "created": _T_LIFE + 1,
                   "data": {"object": {"id": "sub_pds3c",
                                       "metadata": {"finbot_user_id": str(uid)}}}})
        assert r.status_code == 200, r.text
        assert _relogio(uid) is None
        assert db.get_auth_user(uid)["last_payment_status"] == "canceled"
    finally:
        _cleanup_trial(uid)
