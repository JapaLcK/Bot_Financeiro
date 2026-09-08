"""
tests/test_billing_payment_failed.py — o ramo `invoice.payment_failed` do
webhook: o E-MAIL de falha e o erro de API no meio do caminho.

O relógio (`past_due_since`) tem arquivo próprio, `test_billing_dunning_webhook`.
Aqui está o que o relógio NÃO cobre e que já quebrou duas vezes: a
retentabilidade do e-mail e o `stripe.Subscription.retrieve` sem try. Helpers
por IMPORT de `test_billing_webhook_lifecycle` (§0.7), como no arquivo irmão.

CONTROLES NEGATIVOS DECLARADOS, cada um injetado num caso que estava VERDE:

  • T6 — em `_fire_email` (frontend/finance_bot_websocket_custom.py), volte a
    gravar a chave de dedupe sem olhar o retorno (`await asyncio.to_thread(fn,
    ...)` sozinho, sem o `if not ok: return`):
      VERMELHO: T6, na entrega 2 ("entregas 2 e 3 nao retentaram o e-mail").
      VERDE:    T7 e T8.
    O modo de falha do T6 é `return False`, e isso É o teste: `send_email`
    (`core/services/email_service.py:64`) documenta "nunca lança exceção", e um
    controle que injetasse `raise` mediria um caminho que a produção não toma —
    foi exatamente assim que a v1 passou verde com o defeito intacto.
  • T7 — envolva o `stripe.Subscription.retrieve` do ramo num `try/except: pass`:
      VERMELHO: T7 (a 1ª entrega passa a devolver 200 e a escrever).
      VERDE:    T6 e T8.
  • T8 — troque o `dedup_days=0.0 if _abriu_ciclo else ...` por
    `dedup_days=float(DUNNING_GRACE_DAYS)` fixo:
      VERMELHO: T8, e só ele.

CONTROLE POSITIVO do grupo: as três últimas asserções do T6 (sucesso na entrega
2 CONTINUA barrando a 3), a segunda metade do T7 (com o `retrieve` respondendo,
a reentrega carimba e manda) e a primeira metade do T8 (o 2º evento do MESMO
ciclo continua mudo). Sem eles o arquivo passaria num código que manda e-mail
em toda entrega — pior que o bug que ele conserta.
"""
from __future__ import annotations

import pytest

import db
from _billing_grants_helpers import garantir_system_event_logs
from db.connection import get_conn
from test_billing_webhook_lifecycle import (
    _T_LIFE,
    _cleanup_trial,
    _fake_sub,
    _post,
    _setup,
)

_INVOICE_SUB = "sub_pf"


@pytest.fixture(autouse=True)
def _event_logs():
    """A dedupe deste arquivo é `recent_event_exists`, e `system_event_logs` não
    nasce do `init_db` — sem isto ela devolve False por exceção e a dedupe vira
    no-op silencioso (medido no arquivo irmão: 2 e-mails onde devia haver 1)."""
    garantir_system_event_logs()


def _relogio(uid: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select past_due_since from auth_accounts where user_id=%s",
                        (uid,))
            return cur.fetchone()["past_due_since"]


def _failed(uid: int, evt_id: str, created: int, sub: str = _INVOICE_SUB) -> dict:
    return {"type": "invoice.payment_failed", "id": evt_id, "created": created,
            "data": {"object": {"metadata": {"finbot_user_id": str(uid)},
                                "subscription": sub, "attempt_count": 1}}}


def test_T6_email_de_falha_e_retentavel_no_modo_de_falha_real(user_id, monkeypatch):
    """T6: Resend fora do ar na 1ª entrega não pode calar a janela inteira.

    O MODO DE FALHA é `return False`, não `raise`. `send_payment_failed_email`
    termina em `return send_email(...)`, e `send_email` documenta "Retorna True
    em sucesso, nunca lança exceção" — os dois caminhos de falha dela saem por
    `return False` (sem RESEND_API_KEY e no `except`). Uma versão anterior deste
    teste injetava `raise` e ficava verde com o defeito intacto: o `_fire_email`
    gravava a chave de dedupe sem olhar o retorno, e a medição do Manager com
    `return False` deu 1 tentativa, 0 e-mails e chave gravada 1 vez, com a
    janela de 7 dias inteira suprimida.
    """
    from core.services import email_service

    tentativas = []

    def send_payment_failed_email(email, *a, **k):   # __name__ é a chave da dedupe
        tentativas.append(email)
        return len(tentativas) > 1                   # a 1ª "falha" como a real

    monkeypatch.setattr(email_service, "send_payment_failed_email",
                        send_payment_failed_email)

    uid, client, fake = _setup(monkeypatch, f"pf6-{user_id}")
    evento = _failed(uid, "evt_pf_6", _T_LIFE)
    subs = {_INVOICE_SUB: _fake_sub("past_due")}
    try:
        assert _post(client, fake, evento, subs=subs).status_code == 200
        assert len(tentativas) == 1 and _relogio(uid) is not None

        # Reentrega do MESMO evento: tem de TENTAR de novo, e desta vez vai.
        assert _post(client, fake, evento, subs=subs).status_code == 200
        assert len(tentativas) == 2, "entrega 2 não retentou o e-mail"

        # POSITIVO: o sucesso da 2 barra a 3 e a 4 — a dedupe continua valendo,
        # e o conserto não virou "manda em toda entrega".
        assert _post(client, fake, evento, subs=subs).status_code == 200
        assert _post(client, fake, evento, subs=subs).status_code == 200
        assert len(tentativas) == 2, "e-mail duplicado depois do sucesso"
    finally:
        _cleanup_trial(uid)


def test_T7_retrieve_que_estoura_devolve_5xx_sem_escrever_nada(user_id, monkeypatch):
    """T7: `stripe.Subscription.retrieve` é chamado SEM try neste ramo, e isso é
    decisão e não esquecimento — mas nenhum teste exercitava o caminho: o fake
    resolve `outer.subs[sub_id]` e um KeyError virava 500 sem ninguém afirmar
    nada.

    O contrato que este teste fixa: erro de API vira 5xx (a Stripe reentrega por
    até 3 dias) e NADA foi escrito antes disso — nem status, nem relógio, nem
    e-mail. Quem trocar por `try/except: pass` inverte a decisão, e o custo dela
    está no comentário do ramo: `retrieve` que falha de forma PERMANENTE esgota
    as reentregas e o ciclo fica sem `past_due` e sem e-mail.
    """
    from core.services import email_service

    emails = []
    monkeypatch.setattr(email_service, "send_payment_failed_email",
                        lambda *a, **k: emails.append(a) or True)

    uid, client, fake = _setup(monkeypatch, f"pf7-{user_id}")
    try:
        # `subs` vazio: o fake levanta KeyError, o mesmo formato de falha de um
        # `retrieve` que não resolve.
        r = _post(client, fake, _failed(uid, "evt_pf_7", _T_LIFE))
        assert r.status_code >= 500, r.status_code
        assert _relogio(uid) is None, "escreveu o relógio antes de saber o status"
        assert db.get_auth_user(uid)["last_payment_status"] != "past_due"
        assert emails == []

        # POSITIVO do par: com o `retrieve` respondendo, a MESMA reentrega passa
        # e carimba. Sem isto o teste acima passaria num ramo que 5xx sempre.
        assert _post(client, fake, _failed(uid, "evt_pf_7", _T_LIFE),
                     subs={_INVOICE_SUB: _fake_sub("past_due")}).status_code == 200
        assert _relogio(uid) is not None
        assert len(emails) == 1
    finally:
        _cleanup_trial(uid)


def test_T8_ciclo_novo_dentro_da_janela_de_dedupe_manda_email(user_id, monkeypatch):
    """T8: a supressão do e-mail é por CICLO, não por 7 dias de calendário.

    `dedup_days=DUNNING_GRACE_DAYS` sozinho tem um buraco: `clear_past_due_since`
    zera o relógio mas não apaga o evento `send_payment_failed_email_sent`, então
    dois ciclos distintos dentro da mesma semana (troca de plano gerando fatura
    nova, segunda assinatura, falha logo depois de um pagamento) deixavam o
    segundo MUDO. O `rowcount` de `claim_past_due_since` diz que este evento
    abriu ciclo novo, e aí o webhook manda com `dedup_days=0`.

    Ele só AMPLIA: nunca é o `rowcount` que cala o e-mail — quem cala é sempre a
    chave gravada DEPOIS do envio confirmar (T6).
    """
    from core.services import email_service

    emails = []
    monkeypatch.setattr(email_service, "send_payment_failed_email",
                        lambda *a, **k: emails.append(a) or True)

    uid, client, fake = _setup(monkeypatch, f"pf8-{user_id}")
    subs = {_INVOICE_SUB: _fake_sub("past_due")}
    try:
        # Ciclo 1: falha, carimba, manda.
        assert _post(client, fake, _failed(uid, "evt_pf_8a", _T_LIFE),
                     subs=subs).status_code == 200
        assert len(emails) == 1

        # POSITIVO, e vem primeiro de propósito: OUTRO evento do MESMO ciclo
        # (smart retry) continua mudo — o relógio já está carimbado, `rowcount`
        # é 0, e a janela de 7 dias suprime.
        assert _post(client, fake, _failed(uid, "evt_pf_8b", _T_LIFE + 60),
                     subs=subs).status_code == 200
        assert len(emails) == 1, "smart retry do mesmo ciclo mandou e-mail a mais"

        # Pagou: o ciclo fecha e o relógio zera. O evento de dedupe do e-mail
        # continua lá — é ele que fazia o ciclo seguinte ficar mudo.
        assert _post(client, fake,
                     {"type": "invoice.paid", "id": "evt_pf_8_paid",
                      "created": _T_LIFE + 120,
                      "data": {"object": {"metadata": {"finbot_user_id": str(uid)},
                                          "subscription": _INVOICE_SUB,
                                          "amount_paid": 0, "id": "in_pf8"}}},
                     subs={_INVOICE_SUB: _fake_sub("active")}).status_code == 200
        assert _relogio(uid) is None

        # Ciclo 2, DENTRO dos 7 dias de calendário do primeiro e-mail.
        assert _post(client, fake, _failed(uid, "evt_pf_8c", _T_LIFE + 180),
                     subs=subs).status_code == 200
        assert _relogio(uid) is not None
        assert len(emails) == 2, "ciclo novo ficou mudo dentro da janela de dedupe"
    finally:
        _cleanup_trial(uid)
