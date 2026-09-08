"""
tests/test_billing_dunning_webhook.py — o RELÓGIO da inadimplência no webhook.

T1/T2/T3 do corte por cartão em atraso (`core/services/billing_dunning`):
o `invoice.payment_failed` carimba `auth_accounts.past_due_since`, a reentrega
do MESMO evento não reinicia a contagem nem duplica o e-mail, e os três ramos
de "voltou a valer" limpam o relógio.

T4/T5/T6 são os três defeitos que o Tester achou depois: relógio órfão,
`payment_failed` fora de ordem e e-mail que podia nunca sair. Os controles
declarados de cada um estão no cabeçalho da própria seção, mais abaixo.

**Por que arquivo separado**: o plano pedia estes testes dentro do
`test_billing_webhook_lifecycle.py` (que já tem os helpers), mas o portão de
tamanho de `tests/test_max_lines_python.py` reprova arquivo novo acima de 350
linhas e aquele já estava em 335. Os helpers vêm por IMPORT do módulo vizinho —
uma fonte só (§0.7), sem uma segunda cópia de `_FakeStripe`/`_setup`/`_post`
para divergir. O diretório `tests/` está no `sys.path` do pytest (é assim que
`_billing_grants_helpers` já é importado).
"""
from __future__ import annotations

import pytest

import db
from _billing_grants_helpers import garantir_system_event_logs
from db.connection import get_conn
from db.plan_grants import upsert_grant
from test_billing_webhook_lifecycle import (
    _T_LIFE,
    _cleanup_trial,
    _fake_sub,
    _post,
    _setup,
)


@pytest.fixture(autouse=True)
def _event_logs():
    """A dedupe de e-mail deste arquivo é `recent_event_exists`, e
    `system_event_logs` não nasce do `init_db` — sem isto ela devolve False por
    exceção e a dedupe vira no-op silencioso (medido: T2 acusava 2 e-mails onde
    devia haver 1, e a causa era a tabela ausente, não o dedup)."""
    garantir_system_event_logs()


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
    `claim_past_due_since` (db/plans.py) → VERMELHO na metade do RELÓGIO (ele
    avança); T1 e T3 continuam verdes. A metade do E-MAIL deixou de depender
    desse `where`: quem a protege agora é a dedupe do `_fire_email`, e o
    controle dela é o do T6 (voltar o gate para `primeira_falha`).
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


# ──────────────────────────────────────────────────────────────────────────────
# T4/T5/T6 — os três defeitos que o Tester achou no relógio.
#
# CONTROLES NEGATIVOS DECLARADOS (um por conserto, cada um injetado num caso que
# estava VERDE):
#   • T4: devolva `set_payment_status_impl` (db_support.py) ao UPDATE de uma
#     coluna só (sem o `case when ... then past_due_since end`) → T4 VERMELHO,
#     T1/T2/T3/T5/T6 VERDES.
#   • T5: apague a consulta `_status_agora` do ramo `invoice.payment_failed`
#     (frontend/finance_bot_websocket_custom.py) → T5 VERMELHO, o resto VERDE.
#   • T6: volte o e-mail para `if email and primeira_falha:` com
#     `claim_past_due_since` → T6 VERMELHO, o resto VERDE.
#
# CONTROLE POSITIVO do grupo: T1 e T2 — conta LIMPA, `payment_failed` continua
# carimbando o relógio E mandando o e-mail. Sem eles os consertos "passariam"
# num relógio que nunca carimba e num e-mail que nunca sai. O T5 traz o dele
# dentro do próprio teste (a falha LEGÍTIMA depois da atrasada ainda carimba).
# ──────────────────────────────────────────────────────────────────────────────

_INVOICE_SUB = "sub_dun"


def _failed(uid: int, evt_id: str, created: int) -> dict:
    return {"type": "invoice.payment_failed", "id": evt_id, "created": created,
            "data": {"object": {"metadata": {"finbot_user_id": str(uid)},
                                "subscription": _INVOICE_SUB,
                                "attempt_count": 1}}}


def test_T4_grant_pix_nao_deixa_relogio_orfao(user_id, monkeypatch):
    """T4: o produtor de relógio órfão está fechado, e o órfão não detona.

    Sequência REAL (não estado injetado à mão): cartão falha pelo webhook →
    a pessoa passa a ter grant Pix vigente e a passada de 60 s roda
    `recompute_entitlement`, que escreve `active` (billing_access.py:459) e
    NÃO chama `clear_past_due_since`. Antes, o relógio sobrevivia ali; o
    `payment_failed` do ciclo seguinte via `rowcount 0`, a carência virava ZERO
    e o e-mail não saía.
    """
    from core.services.billing_access import recompute_entitlement
    from core.services.billing_dunning import bloqueado_por_inadimplencia
    from datetime import datetime, timedelta, timezone

    uid, client, fake = _setup(monkeypatch, f"pds4-{user_id}")
    emails = _conta_emails(monkeypatch)
    try:
        assert _post(client, fake, _failed(uid, "evt_pds_4a", _T_LIFE),
                     subs={_INVOICE_SUB: _fake_sub("past_due")}).status_code == 200
        # Positivo local: conta limpa carimba E avisa.
        primeiro = _relogio(uid)
        assert primeiro is not None
        assert len(emails) == 1

        # A passada de 60 s com Pix vigente — o produtor do órfão.
        agora = datetime.now(timezone.utc)
        upsert_grant(uid, "pix", f"pix:{uid}", "pro", agora - timedelta(days=1),
                     agora + timedelta(days=300), 1, f"evt_pix_{uid}")
        assert recompute_entitlement(uid) is not None
        assert db.get_auth_user(uid)["last_payment_status"] == "active"
        assert _relogio(uid) is None, "relógio órfão: status saiu da lista e ele ficou"

        # E o ciclo seguinte volta a ter os 7 dias inteiros, não zero.
        assert _post(client, fake, _failed(uid, "evt_pds_4b", _T_LIFE + 60),
                     subs={_INVOICE_SUB: _fake_sub("past_due")}).status_code == 200
        novo = _relogio(uid)
        assert novo is not None and novo > primeiro, (primeiro, novo)
        assert bloqueado_por_inadimplencia(uid) is False
    finally:
        _cleanup_trial(uid)


def test_T5_payment_failed_atrasado_nao_bloqueia_quem_pagou(user_id, monkeypatch):
    """T5: `payment_failed` fora de ordem não carimba o relógio de quem pagou.

    A Stripe reentrega o falho DEPOIS do `invoice.paid` do mesmo ciclo, com
    `created` ANTERIOR ao dele. Antes, isso repunha `past_due` e recarimbava:
    ~3 semanas sem bot para um pagante, até a fatura do mês seguinte.
    """
    from core.observability import recent_event_exists

    uid, client, fake = _setup(monkeypatch, f"pds5-{user_id}")
    _conta_emails(monkeypatch)
    try:
        assert _post(client, fake, _failed(uid, "evt_pds_5a", _T_LIFE),
                     subs={_INVOICE_SUB: _fake_sub("past_due")}).status_code == 200
        assert _relogio(uid) is not None

        # Pagou: relógio zerado, assinatura ATIVA na Stripe.
        assert _post(client, fake,
                     {"type": "invoice.paid", "id": "evt_pds_5_paid",
                      "created": _T_LIFE + 10,
                      "data": {"object": {"metadata": {"finbot_user_id": str(uid)},
                                          "subscription": _INVOICE_SUB,
                                          "amount_paid": 0, "id": "in_pds5"}}},
                     subs={_INVOICE_SUB: _fake_sub("active")}).status_code == 200
        assert _relogio(uid) is None
        assert db.get_auth_user(uid)["last_payment_status"] == "active"

        # O falho ATRASADO (created anterior ao do paid) chega agora.
        assert _post(client, fake, _failed(uid, "evt_pds_5b", _T_LIFE)).status_code == 200
        assert _relogio(uid) is None, "carimbou o relógio de quem pagou"
        assert db.get_auth_user(uid)["last_payment_status"] == "active"
        assert recent_event_exists("billing_payment_failed_obsoleto", uid, 1.0)

        # POSITIVO: a falha LEGÍTIMA do ciclo seguinte (assinatura de novo em
        # atraso na Stripe) continua carimbando — a guarda não recusa tudo.
        assert _post(client, fake, _failed(uid, "evt_pds_5c", _T_LIFE + 20),
                     subs={_INVOICE_SUB: _fake_sub("past_due")}).status_code == 200
        assert _relogio(uid) is not None
        assert db.get_auth_user(uid)["last_payment_status"] == "past_due"
    finally:
        _cleanup_trial(uid)


def test_T6_email_de_falha_e_retentavel(user_id, monkeypatch):
    """T6: SMTP fora do ar na 1ª entrega não pode calar o ciclo inteiro.

    Antes, a dedupe era o `rowcount` de `claim_past_due_since`, COMMITADO antes
    do envio: 1ª entrega com o e-mail estourando + as reentregas da Stripe = 0
    e-mails no ciclo, e no fim dele a pessoa perde o bot sem nunca ter sido
    avisada. Agora a chave é gravada DEPOIS do envio (`_fire_email`).
    """
    from core.services import email_service

    tentativas = []

    def send_payment_failed_email(email, *a, **k):   # __name__ é a chave da dedupe
        tentativas.append(email)
        if len(tentativas) == 1:
            raise RuntimeError("SMTP fora do ar")
        return True

    monkeypatch.setattr(email_service, "send_payment_failed_email",
                        send_payment_failed_email)

    uid, client, fake = _setup(monkeypatch, f"pds6-{user_id}")
    evento = _failed(uid, "evt_pds_6", _T_LIFE)
    subs = {_INVOICE_SUB: _fake_sub("past_due")}
    try:
        assert _post(client, fake, evento, subs=subs).status_code == 200
        assert len(tentativas) == 1 and _relogio(uid) is not None

        # Reentrega do MESMO evento: tem de TENTAR de novo, e desta vez vai.
        assert _post(client, fake, evento, subs=subs).status_code == 200
        assert len(tentativas) == 2, "entrega 2 não retentou o e-mail"

        # E o sucesso da 2 barra a 3 — a dedupe continua valendo.
        assert _post(client, fake, evento, subs=subs).status_code == 200
        assert len(tentativas) == 2, "e-mail duplicado depois do sucesso"
    finally:
        _cleanup_trial(uid)
