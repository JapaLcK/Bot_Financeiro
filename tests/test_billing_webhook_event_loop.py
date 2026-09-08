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
    VERMELHO: test_retrieve_nao_roda_na_thread_do_event_loop[<o ramo tocado>]
    VERDE:    os outros dois ramos, e o resto da suíte de billing — é o que
              prova que a medição é POR RAMO e discrimina qual deles regrediu.

CONTROLE POSITIVO: `test_retrieve_que_estoura_continua_virando_5xx`. O guard de
evento fora de ordem DEPENDE de a exceção propagar (falha do `retrieve` tem de
virar 5xx e não "status vazio", senão evento obsoleto passa pela guarda), então
um "conserto" que engolisse a exceção — `try/except` em volta do `to_thread` —
passaria na medição de thread e quebraria o contrato. Ele também é o par do
`test_T7_retrieve_que_estoura_devolve_5xx_sem_escrever_nada`
(`tests/test_billing_payment_failed.py`), que cobre o ramo `payment_failed` e
tem de continuar verde.
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

    O observável é `asyncio.get_running_loop()`, que levanta `RuntimeError`
    quando NÃO há loop rodando na thread atual. Chamado direto do corpo da
    corrotina, ele devolve o loop (a chamada bloqueia o loop); despachado por
    `asyncio.to_thread`, roda numa worker do `ThreadPoolExecutor`, onde não há
    loop nenhum e ele levanta.

    **A primeira versão disto comparava com `threading.main_thread()` e não
    media NADA**: o `TestClient` do Starlette roda o ASGI num portal, com o
    event loop em thread SEPARADA da do teste, então a comparação dava
    "não é a main" com e sem `to_thread` — o controle negativo ficou verde e
    denunciou o teste (§3, 1ª pergunta: "quanto isso daria se a correção não
    fizesse nada?"). `get_running_loop` não depende da topologia de threads do
    cliente de teste, que é o que torna a medição válida sob `TestClient` e sob
    `asyncio.run`.
    """
    onde: list[bool] = []

    def _retrieve(sub_id):
        import asyncio
        try:
            asyncio.get_running_loop()
            onde.append(True)      # há loop NESTA thread → a chamada o bloqueia
        except RuntimeError:
            onde.append(False)     # thread sem loop → executor
        return _fake_sub(status)

    fake.Subscription = SimpleNamespace(retrieve=_retrieve)
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
