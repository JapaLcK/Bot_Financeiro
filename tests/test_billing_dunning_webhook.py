"""
tests/test_billing_dunning_webhook.py — o RELÓGIO da inadimplência no webhook.

`auth_accounts.past_due_since` (`core/services/billing_dunning`): o
`invoice.payment_failed` o carimba, a reentrega do MESMO evento não reinicia a
contagem, e os três ramos de "voltou a valer" o limpam. Nada aqui bloqueia
acesso — o relógio alimenta só o lembrete do 6º dia e a dedupe do e-mail.

T4/T5/T9 são os defeitos achados nas revisões; os controles de cada um estão no
cabeçalho da seção, abaixo.

**Por que arquivo separado**: o portão de `tests/test_max_lines_python.py`
reprova arquivo novo acima de 350 linhas, e o `test_billing_webhook_lifecycle.py`
já estava em 335. Os helpers vêm por IMPORT dele — uma fonte só (§0.7).
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
    """T1: a falha de cobrança grava past_due_since — o relógio dos 7 dias.

    O payload nomeia a `subscription` porque é o que a Stripe REALMENTE manda
    num `invoice.payment_failed` de assinatura (§3 regra 3). Fatura sem
    assinatura é outro caso e tem teste próprio, o T9."""
    uid, client, fake = _setup(monkeypatch, f"pds1-{user_id}")
    _conta_emails(monkeypatch)
    try:
        assert _relogio(uid) is None
        r = _post(client, fake,
                  {"type": "invoice.payment_failed", "id": "evt_pds_1",
                   "created": _T_LIFE,
                   "data": {"object": {"metadata": {"finbot_user_id": str(uid)},
                                       "subscription": "sub_pds1",
                                       "attempt_count": 1}}},
                  subs={"sub_pds1": _fake_sub("past_due")})
        assert r.status_code == 200, r.text
        assert db.get_auth_user(uid)["last_payment_status"] == "past_due"
        assert _relogio(uid) is not None
        # A coluna tem de chegar a quem lê pelo `get_auth_user` (a lista
        # EXPLÍCITA de colunas do db_support). Sem isso ninguém a veria.
        assert db.get_auth_user(uid)["past_due_since"] == _relogio(uid)
    finally:
        _cleanup_trial(uid)


def test_T2_reentrega_nao_reinicia_o_relogio_nem_duplica_email(user_id, monkeypatch):
    """T2: o MESMO evento entregue 2x não move o relógio, e o e-mail sai 1x.

    A Stripe manda um `payment_failed` por smart retry E reentrega o mesmo
    evento em cima de 5xx. Antes deste PR o e-mail saía em TODOS.

    Controle negativo declarado: tire o `and past_due_since is null` do
    `claim_past_due_since` (db/dunning.py) → VERMELHO na metade do RELÓGIO (ele
    avança); T1 e T3 continuam verdes. A metade do E-MAIL não depende desse
    `where`: quem a protege é a dedupe do `_fire_email`, cujos controles estão
    em tests/test_billing_payment_failed_email.py.
    """
    uid, client, fake = _setup(monkeypatch, f"pds2-{user_id}")
    emails = _conta_emails(monkeypatch)
    evento = {"type": "invoice.payment_failed", "id": "evt_pds_2",
              "created": _T_LIFE,
              "data": {"object": {"metadata": {"finbot_user_id": str(uid)},
                                  "subscription": "sub_pds2",
                                  "attempt_count": 1}}}
    subs = {"sub_pds2": _fake_sub("past_due")}
    try:
        assert _post(client, fake, evento, subs=subs).status_code == 200
        primeiro = _relogio(uid)
        assert primeiro is not None
        assert len(emails) == 1

        assert _post(client, fake, evento, subs=subs).status_code == 200
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
                                          "subscription": "sub_pds3a",
                                          "attempt_count": 2}}},
                     subs={"sub_pds3a": _fake_sub("past_due")}).status_code == 200
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
                                          "subscription": "sub_pds3b",
                                          "attempt_count": 1}}},
                     subs={"sub_pds3b": _fake_sub("past_due")}).status_code == 200
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
                                          "subscription": "sub_pds3c",
                                          "attempt_count": 3}}},
                     subs={"sub_pds3c": _fake_sub("past_due")}).status_code == 200
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
# T4/T5/T9 — os defeitos achados nas revisões, no RELÓGIO.
# (T6/T7/T8 são do RAMO `invoice.payment_failed` — e-mail e erro de API — e
#  moram em tests/test_billing_payment_failed.py: assunto diferente, e este
#  arquivo bateria no teto de 350 linhas de tests/test_max_lines_python.py.)
#
# CONTROLES NEGATIVOS DECLARADOS (um por conserto, num caso que estava VERDE):
#   • T4: devolva o `cur.execute` de `set_payment_status_impl` (db_support.py)
#     ao UPDATE de uma coluna só (sem o `case when ... then past_due_since end`)
#     → T4 VERMELHO, T1/T2/T3/T5/T9 e o arquivo do e-mail VERDES (medido: 1
#     failed, 10 passed).
#   • T5: não atribua o resultado do `retrieve` a `_status_agora` no ramo
#     `invoice.payment_failed` (frontend/finance_bot_websocket_custom.py) → T5
#     VERMELHO, o resto VERDE (medido: 1 failed, 10 passed).
#   • T9: tire o `bool(_sub_id) and` do `claim_past_due_since` do mesmo ramo →
#     T9 VERMELHO, o resto VERDE (medido: 1 failed, 10 passed). T1..T5 nomeiam
#     a subscription no payload, como a Stripe faz.
#
# CONTROLE POSITIVO: T1 e T2 — conta LIMPA, `payment_failed` continua carimbando
# o relógio. Sem eles os consertos "passariam" num relógio que nunca carimba.
# T5 e T9 trazem o deles dentro do próprio teste.
# ──────────────────────────────────────────────────────────────────────────────

_INVOICE_SUB = "sub_dun"


def _failed(uid: int, evt_id: str, created: int) -> dict:
    return {"type": "invoice.payment_failed", "id": evt_id, "created": created,
            "data": {"object": {"metadata": {"finbot_user_id": str(uid)},
                                "subscription": _INVOICE_SUB,
                                "attempt_count": 1}}}


def test_T4_grant_pix_nao_deixa_relogio_orfao(user_id, monkeypatch):
    """T4: o produtor de relógio órfão está fechado, e o órfão não detona.

    Sequência REAL (não estado injetado): cartão falha pelo webhook → a pessoa
    passa a ter grant Pix vigente e a passada de 60 s roda
    `recompute_entitlement`, que escreve `active` (billing_access.py:459) e não
    chama `clear_past_due_since`. Antes, o relógio sobrevivia ali; o
    `payment_failed` do ciclo seguinte via `rowcount 0` e o relógio do ciclo
    novo nascia com a data velha — fora da janela do lembrete de
    pagamento, que então nunca saía.
    """
    from core.services.billing_access import recompute_entitlement
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

        # E o ciclo seguinte nasce com os 7 dias inteiros, não com a data
        # velha. A consequência MEDÍVEL disso é o lembrete: recém-carimbada, a
        # conta não está na janela do lembrete — vai estar no 6º dia.
        assert _post(client, fake, _failed(uid, "evt_pds_4b", _T_LIFE + 60),
                     subs={_INVOICE_SUB: _fake_sub("past_due")}).status_code == 200
        novo = _relogio(uid)
        assert novo is not None and novo > primeiro, (primeiro, novo)
        from db.dunning import list_payment_reminder_candidates
        assert uid not in [r["user_id"] for r in list_payment_reminder_candidates(7)]
    finally:
        _cleanup_trial(uid)


def test_T5_payment_failed_atrasado_nao_carimba_quem_pagou(user_id, monkeypatch):
    """T5: `payment_failed` fora de ordem não carimba o relógio de quem pagou.

    A Stripe reentrega o falho DEPOIS do `invoice.paid` do mesmo ciclo, com
    `created` ANTERIOR ao dele. Antes, isso repunha `past_due` e recarimbava:
    ~3 semanas rotulado "em atraso" e recebendo lembrete de pagamento, para
    quem pagou em dia, até a fatura do mês seguinte.
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


def test_T9_fatura_sem_assinatura_nao_carimba_o_relogio(user_id, monkeypatch):
    """T9: `past_due_since` é o relógio do ciclo DA ASSINATURA. Fatura avulsa
    (sem `subscription` em nenhuma das duas formas da API — ver
    `_invoice_subscription_id`) não tem ciclo para medir e não pode carimbar o
    relógio de quem tem assinatura viva.

    Antes, este era o único ponto fail-CLOSED da feature: sem `_sub_id` o
    `_status_agora` ficava vazio, o `elif user_id` carimbava, e a pessoa
    recebia o lembrete do 6º dia sem nada em atraso.

    O `set_payment_status(user_id, 'past_due')` da linha de cima é
    comportamento PRÉ-EXISTENTE da main e continua acontecendo — está afirmado
    aqui de propósito, para que mudá-lo seja uma decisão e não um efeito
    colateral.
    """
    uid, client, fake = _setup(monkeypatch, f"pds9-{user_id}")
    _conta_emails(monkeypatch)
    avulsa = {"type": "invoice.payment_failed", "id": "evt_pds_9",
              "created": _T_LIFE,
              "data": {"object": {"metadata": {"finbot_user_id": str(uid)},
                                  "attempt_count": 1}}}
    try:
        assert _post(client, fake, avulsa).status_code == 200
        assert _relogio(uid) is None, "carimbou o relógio de uma fatura avulsa"
        assert db.get_auth_user(uid)["last_payment_status"] == "past_due"

        # POSITIVO do par, mesma conta: a fatura DA ASSINATURA continua
        # carimbando. Sem ele a guarda poderia recusar tudo.
        assert _post(client, fake, _failed(uid, "evt_pds_9b", _T_LIFE + 10),
                     subs={_INVOICE_SUB: _fake_sub("past_due")}).status_code == 200
        assert _relogio(uid) is not None
    finally:
        _cleanup_trial(uid)
