"""
tests/test_billing_webhook_event_loop_db.py — TERCEIRA CLASSE de I/O síncrono no
event loop do `POST /billing/webhook`: as leituras/escritas `db.*`.

As duas primeiras classes (`stripe.Subscription.retrieve` e a dedupe
`recent_event_exists`) estão em `tests/test_billing_webhook_event_loop.py`, com
a explicação do observável (`espiao_no_loop`, em `_billing_grants_helpers`). Esta
é a mesma pergunta para as chamadas `db.*` que ainda rodavam direto no corpo da
corrotina (conexão psycopg síncrona, bloqueia o loop pelo connect/statement
timeout do Postgres):

| sítio                                           | quem                                   |
|-------------------------------------------------|----------------------------------------|
| `_resolve_user`, `get_user_by_stripe_customer`  | 5 ramos, sempre que `metadata.finbot_user_id` falta — caminho COMUM de `invoice.paid`/`invoice.payment_failed` |
| `_materializar_assinatura`, perna legada        | `update_user_plan` + `set_payment_status` (sub sem `current_period_end`) |
| ramo `payment_failed`                           | `set_payment_status(past_due)` |
| ramo `deleted`                                  | `update_user_plan(free)` + `set_payment_status(unpaid | canceled)` |

Os espiões entram em `db.<nome>` porque o handler faz `from db import ...` a
cada request — o atributo do pacote é o que vale na hora da chamada.

CONTROLES NEGATIVOS DECLARADOS — em `frontend/finance_bot_websocket_custom.py`:
    tirar o `await asyncio.to_thread(` de UM dos sítios de escrita
        VERMELHO: test_escrita_db_nao_roda_na_thread_do_event_loop[<o sítio>],
                  e SÓ ele
    tirar o `await` de um `await _resolve_user(...)` (coroutine nunca
    executada → `int(coroutine)` estoura → 500)
        VERMELHO: test_get_user_by_stripe_customer_nao_roda_na_thread_do_event_loop[<o ramo>]
                  (nos ramos com `retrieve`, o irmão
                  `test_retrieve_nao_roda_na_thread_do_event_loop[<o ramo>]`
                  cai junto, pelo mesmo 500)

Regra dos controles deste arquivo: citar o predicado a injetar, nomear só os
VERMELHOS — ver `docs/controles_declarados.md`.

CONTROLES POSITIVOS:
- `test_get_user_by_stripe_customer_que_estoura_continua_virando_5xx`: a
  exceção da leitura continua propagando (5xx → a Stripe reentrega); um
  `try/except` em volta do `to_thread` passaria na medição de thread.
- das escritas: `test_payment_failed_marca_past_due` e
  `test_subscription_deleted_volta_pra_free`
  (`tests/test_billing_webhook_lifecycle.py`) — o estado no banco depois do
  POST é o que um `to_thread` sem `await` (escrita nunca executada) quebra.
"""
from __future__ import annotations

import pytest

import db
from _billing_grants_helpers import (
    espiao_no_loop,
    evt_deleted,
    garantir_system_event_logs,
    set_customer,
)
from core.services.billing_dunning import STRIPE_CANCEL_REASON_INADIMPLENCIA
from test_billing_dunning_eventos import _SUB, _checkout, _failed, _paid
from test_billing_webhook_event_loop import _trial_will_end
from test_billing_webhook_lifecycle import (
    _T_LIFE,
    _cleanup_trial,
    _fake_sub,
    _post,
    _setup,
)


@pytest.fixture(autouse=True)
def _event_logs():
    garantir_system_event_logs()


def _espiar(monkeypatch, nomes: tuple[str, ...]) -> dict[str, list[bool]]:
    """Um `onde` por função espiada, em `db.<nome>`, chamando a original por baixo."""
    ondes: dict[str, list[bool]] = {}
    for nome in nomes:
        ondes[nome] = []
        monkeypatch.setattr(db, nome, espiao_no_loop(ondes[nome], getattr(db, nome)))
    return ondes


# ── `_resolve_user` sem metadata: cai em `get_user_by_stripe_customer` ────────

# Os cinco ramos que chamam `_resolve_user`, com o builder (dos irmãos) e o
# status da sub que o `retrieve` devolve (None = ramo sem `retrieve`).
_RAMOS = {
    "checkout.session.completed": (lambda uid: _checkout(uid, "evt_evldb_co", _T_LIFE), "active"),
    "invoice.paid": (lambda uid: _paid(uid, "evt_evldb_paid", _T_LIFE), "active"),
    "customer.subscription.trial_will_end": (_trial_will_end, None),
    "invoice.payment_failed": (lambda uid: _failed(uid, "evt_evldb_pf", _T_LIFE), "past_due"),
    "customer.subscription.deleted": (lambda uid: evt_deleted(uid, _SUB, _T_LIFE), None),
}


def _sem_metadata(evento: dict, cid: str) -> dict:
    """Mesmo evento dos irmãos, mas resolvido pelo `customer` — é o que a
    Stripe manda em `invoice.*`, onde `metadata` é o da fatura, não o nosso."""
    obj = evento["data"]["object"]
    obj.pop("metadata", None)
    obj["customer"] = cid
    return evento


@pytest.mark.parametrize("tipo", list(_RAMOS))
def test_get_user_by_stripe_customer_nao_roda_na_thread_do_event_loop(user_id, monkeypatch, tipo):
    """Um caso por ramo: a leitura tem de sair da thread do event loop, e o POST
    tem de continuar 200 — é o que pega o `await` esquecido (500)."""
    construir, status = _RAMOS[tipo]
    uid, client, fake = _setup(monkeypatch, f"evldb-{tipo[:6]}-{user_id}")
    cid = f"cus_evl_{tipo[:6]}_{uid}"
    set_customer(uid, cid)
    ondes = _espiar(monkeypatch, ("get_user_by_stripe_customer",))
    try:
        r = _post(client, fake, _sem_metadata(construir(uid), cid),
                  subs={_SUB: _fake_sub(status)} if status else None)
        assert r.status_code == 200, r.text
        onde = ondes["get_user_by_stripe_customer"]
        assert onde, f"ramo {tipo}: get_user_by_stripe_customer não foi chamado"
        assert not any(onde), (
            f"ramo {tipo}: get_user_by_stripe_customer rodou na thread do event "
            f"loop — bloqueia request e outros webhooks pelo timeout do Postgres")
    finally:
        _cleanup_trial(uid)


def test_get_user_by_stripe_customer_que_estoura_continua_virando_5xx(user_id, monkeypatch):
    """POSITIVO: `to_thread` NÃO pode ter mudado a propagação — erro de banco
    na resolução do usuário vira 5xx e a Stripe reentrega."""
    uid, client, fake = _setup(monkeypatch, f"evldbx-{user_id}")
    cid = f"cus_evlx_{uid}"
    set_customer(uid, cid)

    def _explode(stripe_customer_id):
        raise RuntimeError("postgres indisponivel")

    monkeypatch.setattr(db, "get_user_by_stripe_customer", _explode)
    try:
        r = _post(client, fake, _sem_metadata(_paid(uid, "evt_evldbx", _T_LIFE), cid),
                  subs={_SUB: _fake_sub("active")})
        assert r.status_code >= 500, (
            "erro do get_user_by_stripe_customer deixou de virar 5xx — a Stripe "
            "para de reentregar e o evento se perde")
    finally:
        _cleanup_trial(uid)


# ── escritas `update_user_plan` / `set_payment_status` ───────────────────────

def _deleted(uid: int, motivo: str | None) -> dict:
    evento = evt_deleted(uid, _SUB, _T_LIFE)
    if motivo:
        evento["data"]["object"]["cancellation_details"] = {"reason": motivo}
    return evento


def _sub_sem_periodo() -> dict:
    """Sub sem `current_period_end` em lugar nenhum: `_materializar_assinatura`
    cai na perna legada (`update_user_plan` + `set_payment_status` diretos)."""
    return {"status": "active", "items": {"data": [{"price": {"id": "price_legado"}}]}}


# (evento, sub do retrieve ou None, funções espiadas)
_SITIOS = {
    "payment_failed": (
        lambda uid: _failed(uid, "evt_evldb_w_pf", _T_LIFE), _fake_sub("past_due"),
        ("set_payment_status",)),
    "deleted": (
        lambda uid: _deleted(uid, None), None,
        ("update_user_plan", "set_payment_status")),
    "deleted_inadimplencia": (
        lambda uid: _deleted(uid, STRIPE_CANCEL_REASON_INADIMPLENCIA), None,
        ("set_payment_status",)),
    "checkout_legado": (
        lambda uid: _checkout(uid, "evt_evldb_w_co", _T_LIFE), _sub_sem_periodo(),
        ("update_user_plan", "set_payment_status")),
}


@pytest.mark.parametrize("sitio", list(_SITIOS))
def test_escrita_db_nao_roda_na_thread_do_event_loop(user_id, monkeypatch, sitio):
    """Um caso por sítio de escrita, para a medição dizer QUAL regrediu."""
    construir, sub, nomes = _SITIOS[sitio]
    uid, client, fake = _setup(monkeypatch, f"evldbw-{sitio[:8]}-{user_id}")
    ondes = _espiar(monkeypatch, nomes)
    try:
        r = _post(client, fake, construir(uid), subs={_SUB: sub} if sub else None)
        assert r.status_code == 200, r.text
        for nome, onde in ondes.items():
            assert onde, f"sítio {sitio}: {nome} não foi chamado"
            assert not any(onde), (
                f"sítio {sitio}: {nome} rodou na thread do event loop — "
                f"bloqueia request e outros webhooks pelo timeout do Postgres")
    finally:
        _cleanup_trial(uid)
