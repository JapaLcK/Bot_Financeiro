"""
tests/test_billing_webhook_event_loop.py — o handler do webhook não faz I/O
SÍNCRONO no event loop.

`POST /billing/webhook` é `async` e roda no event loop ÚNICO do Uvicorn. Uma
chamada de API bloqueante feita direto no corpo dele congela o processo
INTEIRO enquanto espera o Stripe — e não por milissegundos, mas pelo timeout do
cliente HTTP, com request de usuário e OUTROS webhooks de pagamento parados
atrás. É a mesma classe do lembrete (`tests/test_payment_reminder_lote.py`,
audit de PII fora do loop), agora no caminho do dinheiro.

A CLASSE são as chamadas `stripe.Subscription.retrieve` do handler, e elas são
TRÊS — uma por ramo:

| ramo                          | linha | origem |
|-------------------------------|-------|--------|
| `checkout.session.completed`  | :5001 | pré-existente na `main` |
| `invoice.paid`                | :5190 | pré-existente na `main` |
| `invoice.payment_failed`      | :5425 | acrescentada por este PR (guard da rodada 2) |

As três foram embrulhadas em `asyncio.to_thread` no mesmo commit, e a razão de
não consertar só a do PR está no relato da rodada: mesma função, mesma classe,
mesmo conserto de UMA linha, e as duas pré-existentes são as mais FREQUENTES
das três (todo checkout e toda renovação passam por elas; `payment_failed`, só
quem falha). Fechar duas e deixar a terceira é o erro de instância que este PR
já pagou cinco vezes (§2). `to_thread` é o padrão JÁ estabelecido no arquivo
para chamada Stripe em handler async (as cinco de `SubscriptionSchedule`,
`:4503`-`:4660`), então embrulhar é CONVERGIR.

O OBSERVÁVEL é se o `retrieve` roda numa thread que tem event loop ATIVO
(`asyncio.get_running_loop`): é a mesma PERGUNTA do teste de audit de PII da
rodada anterior, mas não o mesmo mecanismo — ver `_espiar_retrieve`, que
registra por que comparar com `threading.main_thread()` aqui não media nada.

CONTROLE NEGATIVO DECLARADO — em `frontend/finance_bot_websocket_custom.py`,
tire o `await asyncio.to_thread(...)` de QUALQUER um dos três ramos (voltando
para `stripe.Subscription.retrieve(sub_id)`):
    VERMELHO: test_retrieve_nao_roda_na_thread_do_event_loop[<o ramo tocado>],
              e SÓ o ramo tocado — a parametrização é o que faz a medição
              discriminar QUAL dos três regrediu, e essa é uma afirmação sobre o
              caminho injetado, não sobre o conjunto de arquivos.

Regra dos controles deste arquivo: citar o PREDICADO a injetar, nomear só os
VERMELHOS, nunca escrever `N passed` — ver `docs/controles_declarados.md`.


CONTROLE POSITIVO: `test_retrieve_que_estoura_continua_virando_5xx`. O guard de
evento fora de ordem DEPENDE de a exceção propagar (falha do `retrieve` tem de
virar 5xx e não "status vazio", senão evento obsoleto passa pela guarda), então
um "conserto" que engolisse a exceção — `try/except` em volta do `to_thread` —
passaria na medição de thread e quebraria o contrato. Ele também é o par do
`test_T7_retrieve_que_estoura_devolve_5xx_sem_escrever_nada`
(`tests/test_billing_payment_failed.py`), que cobre o ramo `payment_failed` e
tem de continuar verde.


SEGUNDA CLASSE (#440) — a dedupe `recent_event_exists` (abre conexão psycopg
síncrona, `core/system_event_log.py:299`), chamada DUAS vezes no ramo
`customer.subscription.trial_will_end`:

| chamada                                  | linha | estado na `main` |
|------------------------------------------|-------|------------------|
| ramo `trial_will_end`, dedupe de fora    | :5798 | direta no loop — consertada por este PR |
| `_fire_email`, dedupe de dentro          | :5399 | já em `to_thread` (o padrão) |

CONTROLE NEGATIVO DECLARADO — em `frontend/finance_bot_websocket_custom.py`:
    tirar o `await asyncio.to_thread(` de `:5798`
        VERMELHO: test_dedupe_do_trial_will_end_nao_roda_na_thread_do_event_loop
                  (`onde[0] is True`)
    tirar o de `:5399`
        VERMELHO: o mesmo teste, com `onde[1] is True`
    tirar SÓ o `await` de `:5798` (coroutine truthy, o ramo nunca entra)
        VERMELHO: test_trial_will_end_reentregue_nao_manda_segundo_email
                  (0 chamadas no 1º POST) e o negativo (`len(onde) == 0`)
    trocar o `if not await asyncio.to_thread(...)` de `:5798` por `if True:`
    (dedupe de fora removida por inteiro)
        VERMELHO: test_marcador_do_scheduler_cala_o_webhook (1 e-mail em vez
                  de 0) e o negativo (`len(onde) == 1`, só a checagem interna
                  restou). `test_trial_will_end_reentregue_nao_manda_segundo_email`
                  fica VERDE aqui de propósito: a reentrega do mesmo evento
                  continua barrada pela dedupe INTERNA do `_fire_email`.

CONTROLE POSITIVO, em dois casos porque duas dedupes se sobrepõem:
- `test_trial_will_end_reentregue_nao_manda_segundo_email` — o ramo ENTRA e
  envia na 1ª entrega e grava o marcador de fora (`:5807`). Pega o `to_thread`
  sem `await`. A supressão da 2ª entrega que ele também confere vem do
  `_fire_email` (chave `send_trial_ending_email_sent`, `:5412`), não do `:5798`.
- `test_marcador_do_scheduler_cala_o_webhook` — o cenário que SÓ o `:5798`
  protege: o `engagement_scheduler` grava `trial_ending_email_sent`
  (`core/services/engagement_scheduler.py:290`), chave que o `_fire_email` não
  consulta. Scheduler já mandou → webhook chega depois → 0 e-mails.


TERCEIRA CLASSE (escritas/leituras `db.*`): `tests/test_billing_webhook_event_loop_db.py`.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from test_billing_dunning_eventos import _checkout, _failed, _paid
from test_billing_webhook_lifecycle import (
    _T_LIFE,
    _cleanup_trial,
    _fake_sub,
    _post,
    _setup,
)

# Os três ramos que chamam `Subscription.retrieve`, com o builder de evento de
# cada um. `payment_failed` fica com `past_due` para o guard não descartar o
# ramo antes de o resto rodar.
_RAMOS = {
    "checkout.session.completed": (_checkout, "active"),
    "invoice.paid": (_paid, "active"),
    "invoice.payment_failed": (_failed, "past_due"),
}


@pytest.fixture(autouse=True)
def _event_logs():
    from _billing_grants_helpers import garantir_system_event_logs
    garantir_system_event_logs()


def _espiar_retrieve(fake, status: str) -> list[bool]:
    """Troca o `Subscription.retrieve` do fake por um espião que registra se
    rodou NA THREAD DE UM EVENT LOOP. Devolve a lista de `na_thread_do_loop`.
    O observável e por que não é `threading.main_thread()`: `espiao_no_loop`.
    """
    from _billing_grants_helpers import espiao_no_loop
    onde: list[bool] = []
    fake.Subscription = SimpleNamespace(
        retrieve=espiao_no_loop(onde, lambda sub_id: _fake_sub(status)))
    return onde


@pytest.mark.parametrize("tipo", list(_RAMOS))
def test_retrieve_nao_roda_na_thread_do_event_loop(user_id, monkeypatch, tipo):
    """Um caso por ramo: o `retrieve` tem de sair da thread do event loop.

    Parametrizado de propósito — com um teste só, tirar o `to_thread` de UM
    ramo poderia ficar verde pelos outros dois, e a medição não diria qual
    regrediu.
    """
    from core.services import email_service
    monkeypatch.setattr(email_service, "send_payment_failed_email",
                        lambda *a, **k: True)

    construir, status = _RAMOS[tipo]
    uid, client, fake = _setup(monkeypatch, f"evl-{tipo[:6]}-{user_id}")
    onde = _espiar_retrieve(fake, status)
    try:
        r = _post(client, fake, construir(uid, f"evt_evl_{tipo[:6]}", _T_LIFE))
        assert r.status_code == 200, r.text
        assert onde, f"o retrieve do ramo {tipo} não foi chamado"
        assert not any(onde), (
            f"o retrieve do ramo {tipo} rodou na thread do event loop — "
            f"bloqueia request e outros webhooks pelo timeout do Stripe")
    finally:
        _cleanup_trial(uid)


@pytest.mark.parametrize("tipo", list(_RAMOS))
def test_retrieve_que_estoura_continua_virando_5xx(user_id, monkeypatch, tipo):
    """POSITIVO do par: `to_thread` NÃO pode ter mudado a propagação.

    A exceção do `retrieve` tem de continuar chegando ao FastAPI e virando 5xx
    — a Stripe reentrega por até 3 dias, e no `payment_failed` é isso que
    impede um evento obsoleto de passar pela guarda com "status vazio". Sem
    este caso, um `try/except` em volta do `to_thread` passaria na medição de
    thread acima e quebraria o contrato em silêncio.
    """
    construir, _ = _RAMOS[tipo]
    uid, client, fake = _setup(monkeypatch, f"evx-{tipo[:6]}-{user_id}")

    def _explode(sub_id):
        raise RuntimeError("stripe indisponivel")

    fake.Subscription = SimpleNamespace(retrieve=_explode)
    try:
        r = _post(client, fake, construir(uid, f"evt_evx_{tipo[:6]}", _T_LIFE))
        assert r.status_code >= 500, (
            f"ramo {tipo}: erro do retrieve deixou de virar 5xx — a Stripe "
            f"para de reentregar e o evento se perde")
    finally:
        _cleanup_trial(uid)


# ── segunda classe: a dedupe `recent_event_exists` do ramo trial_will_end ────

def _trial_will_end(uid: int) -> dict:
    """Evento no formato de `test_billing_email_nome_do_plano.py`: sub em
    `trialing` a 3 dias do fim, resolvida por `metadata.finbot_user_id`."""
    from _billing_grants_helpers import sub_stripe
    sub = sub_stripe("trialing", "price_evl", 3)
    sub["id"] = "sub_evl_twe"
    sub["metadata"] = {"finbot_user_id": str(uid)}
    return {"type": "customer.subscription.trial_will_end", "id": "evt_evl_twe",
            "created": _T_LIFE, "data": {"object": sub}}


def test_dedupe_do_trial_will_end_nao_roda_na_thread_do_event_loop(user_id, monkeypatch):
    """As DUAS checagens de dedupe do ramo (`:5798` de fora, `:5399` dentro do
    `_fire_email`) têm de sair da thread do event loop.

    O espião entra em `core.observability` porque os dois call sites fazem
    `from core.observability import recent_event_exists` DENTRO da função —
    o atributo do módulo é lido na hora da chamada. Mesmo observável de
    `_espiar_retrieve` (`get_running_loop`), pela mesma razão de lá.
    Exige `len(onde) == 2`: se só uma checagem aparecer, o POST não percorreu o
    ramo inteiro e a medição da outra é vácuo.
    """
    from _billing_grants_helpers import espiao_email, espiao_no_loop
    from core.services import email_service

    onde: list[bool] = []
    _espiao = espiao_no_loop(onde, lambda event_type, user_id, within_days=7.0: False)
    monkeypatch.setattr("core.observability.recent_event_exists", _espiao)
    monkeypatch.setattr(email_service, "send_trial_ending_email",
                        espiao_email({}, "send_trial_ending_email"))

    uid, client, fake = _setup(monkeypatch, f"evl-twe-{user_id}")
    try:
        r = _post(client, fake, _trial_will_end(uid))
        assert r.status_code == 200, r.text
        assert len(onde) == 2, f"esperava as 2 checagens do ramo, vi {onde}"
        assert not any(onde), (
            f"dedupe rodou na thread do event loop (fora={onde[0]}, "
            f"_fire_email={onde[1]}) — bloqueia request e outros webhooks "
            f"pelo connect/statement timeout do Postgres")
    finally:
        _cleanup_trial(uid)


def _espiar_remetente(monkeypatch) -> list[tuple]:
    """Troca `send_trial_ending_email` por um contador de chamadas. `__name__`
    preservado porque é a chave da dedupe interna do `_fire_email`."""
    from core.services import email_service

    chamadas: list[tuple] = []

    def _send(*a, **kw):
        chamadas.append(a)
        return True
    _send.__name__ = "send_trial_ending_email"
    monkeypatch.setattr(email_service, "send_trial_ending_email", _send)
    return chamadas


def test_trial_will_end_reentregue_nao_manda_segundo_email(user_id, monkeypatch):
    """POSITIVO 1: o ramo ENTRA e envia na 1ª entrega, e grava o marcador de
    fora (`trial_ending_email_sent`, o que o `engagement_scheduler` consulta).

    É o que pega um `to_thread(...)` sem `await` (coroutine é truthy → `not`
    dá False → o ramo nunca entra → zero e-mails para sempre), que passaria
    verde no negativo.

    A 2ª entrega sem e-mail NÃO prova o `:5798`: com ele removido por inteiro
    ela continua muda, porque a dedupe INTERNA do `_fire_email` (chave
    `send_trial_ending_email_sent`) barra a reentrega sozinha. O caso que só o
    `:5798` cobre é `test_marcador_do_scheduler_cala_o_webhook`.
    """
    from core.observability import recent_event_exists

    chamadas = _espiar_remetente(monkeypatch)
    uid, client, fake = _setup(monkeypatch, f"evp-twe-{user_id}")
    try:
        r = _post(client, fake, _trial_will_end(uid))
        assert r.status_code == 200, r.text
        assert len(chamadas) == 1, f"1ª entrega: esperava 1 e-mail, vi {len(chamadas)}"
        assert recent_event_exists("trial_ending_email_sent", uid, 6), (
            "marcador de fora não gravado — o scheduler mandaria de novo")

        r = _post(client, fake, _trial_will_end(uid))
        assert r.status_code == 200, r.text
        assert len(chamadas) == 1, f"reentrega: esperava ainda 1 e-mail, vi {len(chamadas)}"
    finally:
        _cleanup_trial(uid)


def test_marcador_do_scheduler_cala_o_webhook(user_id, monkeypatch):
    """POSITIVO 2, o que discrimina o `:5798`: scheduler já mandou, webhook
    chega depois, e-mail NÃO sai de novo.

    O marcador é gravado exatamente como o `engagement_scheduler` grava
    (`core/services/engagement_scheduler.py:290`: `log_system_event_sync` com
    `trial_ending_email_sent`, `source="engagement_scheduler"`). Essa chave só
    a dedupe de fora consulta — o `_fire_email` olha `send_trial_ending_email_sent`
    — então com o `:5798` removido este teste vê 1 e-mail e fica vermelho.
    """
    from core.observability import log_system_event_sync

    chamadas = _espiar_remetente(monkeypatch)
    uid, client, fake = _setup(monkeypatch, f"evs-twe-{user_id}")
    try:
        log_system_event_sync(
            "info",
            "trial_ending_email_sent",
            "Email de trial ending enviado (3 dias antes).",
            source="engagement_scheduler",
            user_id=uid,
        )
        r = _post(client, fake, _trial_will_end(uid))
        assert r.status_code == 200, r.text
        assert len(chamadas) == 0, (
            f"scheduler já mandou; webhook mandou de novo: {len(chamadas)}")
    finally:
        _cleanup_trial(uid)
