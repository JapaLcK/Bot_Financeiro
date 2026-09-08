"""
tests/test_billing_dunning_eventos.py — o relógio da inadimplência × o VEREDITO
do evento que o está escrevendo.

Uma classe só, três instâncias: **escrita no relógio (`past_due_since`) que
ignora o estado atual da conta**. Os writers da coluna de STATUS já são
condicionais desde o PR anterior (`db_support.set_payment_status_impl` e o SQL
cru de `admin_dashboard.set_account_plan`, cobertos por
`tests/test_billing_dunning.py`); os writers do próprio RELÓGIO eram os que
ficaram incondicionais:

  • R1 — `claim_past_due_since` carimbava sem olhar `last_payment_status`, e a
    corrida entre `invoice.payment_failed` e `invoice.paid` (duas requisições,
    com um `await` no meio do ramo falho) produzia o ÓRFÃO que a invariante
    declara impossível: relógio com status `active`.
  • R2 — o `clear_past_due_since` de `invoice.paid` rodava mesmo quando
    `_materializar_assinatura` devolvia False (evento VELHO, recusado pela
    guarda de versão de `upsert_grant`).
  • R3 — `checkout.session.completed` tinha o defeito IDÊNTICO ao R2 e não
    estava no apontamento; apareceu na varredura da classe (§2).

Fica FORA, de propósito: o clear de `customer.subscription.deleted`. Lá não há
veredito para ler — o `update_user_plan`/`set_payment_status` daquele ramo já
escrevem sem checar versão de evento na `main`, então gatear só o clear não
fecharia nada. Ressalva registrada no comentário do ramo.

**Fica fora também a REENTREGA, e ela tem arquivo próprio**
(`tests/test_billing_dunning_reentrega.py`): o discriminador de R1–R3 é o
VEREDITO do evento, e o dela é a IDADE do relógio. O gate `_decidiu_acesso` que
R2/R3 provam é NECESSÁRIO e não SUFICIENTE — a tabela de estados × eventos
(`docs/dunning_estados_eventos.md`) mostra as duas células lado a lado.

Helpers por IMPORT de `test_billing_webhook_lifecycle` (§0.7), como os dois
arquivos irmãos. Arquivo NOVO porque `test_billing_dunning_webhook.py` está em
350/350, o teto de `tests/test_max_lines_python.py`.

CONTROLES NEGATIVOS DECLARADOS, cada um injetado num caso que estava VERDE:

  • R1 — tire o `and lower(coalesce(last_payment_status, '')) = any(%s)` do
    `where` de `db/dunning.py::claim_past_due_since` (e o `%s` correspondente):
      VERMELHO: test_R1_corrida_com_invoice_paid_nao_deixa_relogio_orfao.
      VERDE:    R2, R3 e os dois arquivos irmãos do webhook.
  • R2 — no ramo `invoice.paid` do webhook, volte o clear a incondicional
    (apague o `if _decidiu_acesso:` e desidente as duas linhas):
      VERMELHO: test_R2_invoice_paid_velho_nao_zera_o_relogio.
      VERDE:    R1, R3 e o T3a do arquivo irmão (o `paid` NORMAL, que é o caso
                que a injeção NÃO pode reprovar).
  • R3 — o mesmo no ramo `checkout.session.completed`:
      VERMELHO: test_R3_checkout_velho_nao_zera_o_relogio.
      VERDE:    R1, R2 e o T3b do arquivo irmão.

CONTROLES POSITIVOS (os três consertos RESTRINGEM — sem eles o arquivo passaria
num código que nunca carimba e nunca limpa):
  • R1, 2ª metade: a falha LEGÍTIMA depois da corrida continua carimbando E
    continua abrindo ciclo — o e-mail sai DENTRO da janela de dedupe, que é o
    observável de `_abriu_ciclo` (o mecanismo tem teste próprio no T8 de
    `tests/test_billing_payment_failed.py`).
  • R2 e R3, 2ª metade: o evento ACEITO (versão mais nova) continua zerando o
    relógio de quem pagou, na MESMA conta.
"""
from __future__ import annotations

from datetime import datetime, timezone

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

_SUB = "sub_dev"

# `created` de um evento que a Stripe emitiu ANTES do carimbo do relógio (que é
# `now()` do banco). Relativo a `now()`, e não literal, pela mesma razão do
# `_T_LIFE`: literal no passado ou no futuro faz o teste medir o calendário.
_T_PASSADO = int(datetime.now(timezone.utc).timestamp()) - 30 * 86_400


@pytest.fixture(autouse=True)
def _event_logs():
    """`system_event_logs` não nasce do `init_db`, e sem ela `recent_event_exists`
    devolve False por exceção — a dedupe do e-mail viraria no-op silencioso e a
    2ª metade do R1 mediria nada."""
    garantir_system_event_logs()


def _relogio(uid: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select past_due_since from auth_accounts where user_id=%s",
                        (uid,))
            return cur.fetchone()["past_due_since"]


def _conta_emails(monkeypatch) -> list:
    from core.services import email_service
    chamadas = []
    monkeypatch.setattr(email_service, "send_payment_failed_email",
                        lambda *a, **k: chamadas.append(a) or True)
    return chamadas


def _failed(uid: int, evt_id: str, created: int) -> dict:
    return {"type": "invoice.payment_failed", "id": evt_id, "created": created,
            "data": {"object": {"metadata": {"finbot_user_id": str(uid)},
                                "subscription": _SUB, "attempt_count": 1}}}


def _paid(uid: int, evt_id: str, created: int) -> dict:
    return {"type": "invoice.paid", "id": evt_id, "created": created,
            "data": {"object": {"metadata": {"finbot_user_id": str(uid)},
                                "subscription": _SUB,
                                "amount_paid": 0, "id": f"in_{evt_id}"}}}


def _checkout(uid: int, evt_id: str, created: int) -> dict:
    return {"type": "checkout.session.completed", "id": evt_id, "created": created,
            "data": {"object": {"metadata": {"finbot_user_id": str(uid)},
                                "subscription": _SUB, "id": f"cs_{evt_id}"}}}


# ──────────────────────────────────────────────────────────────────────────────
# R1 — a corrida entre os NOSSOS dois handlers.
# ──────────────────────────────────────────────────────────────────────────────

def test_R1_corrida_com_invoice_paid_nao_deixa_relogio_orfao(user_id, monkeypatch):
    """R1: o `invoice.paid` que cai ENTRE o `set_payment_status(past_due)` e o
    `claim_past_due_since` não vira relógio órfão.

    O intercalamento é injetado no ponto EXATO onde ele cabe em produção — o
    `await asyncio.to_thread(claim_past_due_since, ...)` do ramo falho, que é
    uma transação separada da anterior — e o competidor é o código REAL
    (`set_payment_status(active)` + `clear_past_due_since`), não um estado
    escrito à mão. O `Subscription.retrieve` do ramo falho NÃO fecha isso: ele
    roda ANTES do `set_payment_status`, então a corrida sobrevive a ele (é o que
    distingue este caso do T5 do arquivo irmão, que é o evento fora de ORDEM).
    """
    from db.dunning import clear_past_due_since

    uid, client, fake = _setup(monkeypatch, f"dev1-{user_id}")
    emails = _conta_emails(monkeypatch)
    real_sps = db.set_payment_status
    ja_correu: list = []

    def _com_corrida(u, status):
        real_sps(u, status)
        if status == "past_due" and int(u) == uid and not ja_correu:
            ja_correu.append(status)
            # A outra requisição, no intervalo: pagou. O `paid` competidor é
            # MAIS NOVO que o relógio que este ramo acabou de tentar carimbar,
            # então ele passa pelo predicado da escrita (é o que o
            # `nao_mais_novo_que` mede — ver R4/R5 abaixo).
            real_sps(u, "active")
            clear_past_due_since(int(u), nao_mais_novo_que=_T_LIFE + 1)

    # O webhook resolve o nome em `db` (o `from db import ...` do :4687 roda
    # DENTRO do endpoint), então o ponto de injeção é o módulo `db`.
    monkeypatch.setattr(db, "set_payment_status", _com_corrida)
    try:
        assert _post(client, fake, _failed(uid, "evt_dev_1a", _T_LIFE),
                     subs={_SUB: _fake_sub("past_due")}).status_code == 200
        assert ja_correu, "a corrida não foi injetada — o teste mediria nada"
        assert db.get_auth_user(uid)["last_payment_status"] == "active"
        assert _relogio(uid) is None, "órfão: relógio carimbado com status 'active'"
        assert len(emails) == 1

        # POSITIVO: a falha LEGÍTIMA (sem corrida) continua carimbando E
        # continua abrindo ciclo. O 2º e-mail dentro da janela de dedupe é o
        # observável de `_abriu_ciclo` (rowcount 1): com o predicado errado o
        # `claim` nunca carimbaria e este e-mail não sairia.
        assert _post(client, fake, _failed(uid, "evt_dev_1b", _T_LIFE + 60),
                     subs={_SUB: _fake_sub("past_due")}).status_code == 200
        assert db.get_auth_user(uid)["last_payment_status"] == "past_due"
        assert _relogio(uid) is not None, "a falha legítima deixou de carimbar"
        assert len(emails) == 2, "ciclo novo ficou mudo — `_abriu_ciclo` não amplia"
    finally:
        _cleanup_trial(uid)


# ──────────────────────────────────────────────────────────────────────────────
# R2 e R3 — o clear que ignorava o veredito do evento.
#
# O evento VELHO é construído como a Stripe o entrega: `created` ANTERIOR ao do
# evento que criou o grant. A guarda de versão de `upsert_grant` o recusa
# (retorno None), `_materializar_assinatura` devolve False, e o clear não pode
# rodar por ordem de quem não decidiu o acesso.
# ──────────────────────────────────────────────────────────────────────────────

def _cenario_evento_velho(monkeypatch, user_id, suffix):
    """Conta com grant nascido em `_T_LIFE + 200` e relógio de inadimplência
    carimbado em `+300`. Devolve `(uid, client, fake)`."""
    uid, client, fake = _setup(monkeypatch, f"{suffix}-{user_id}")
    _conta_emails(monkeypatch)
    subs = {_SUB: _fake_sub("active")}
    # 1) assinatura viva: cria o grant na versão +200.
    assert _post(client, fake, _paid(uid, f"evt_{suffix}_novo", _T_LIFE + 200),
                 subs=subs).status_code == 200
    # 2) o cartão falha: o relógio do ciclo é carimbado.
    assert _post(client, fake, _failed(uid, f"evt_{suffix}_fail", _T_LIFE + 300),
                 subs={_SUB: _fake_sub("past_due")}).status_code == 200
    assert _relogio(uid) is not None
    return uid, client, fake


def test_R2_invoice_paid_velho_nao_zera_o_relogio(user_id, monkeypatch):
    """R2: `invoice.paid` recusado por velho não fecha o ciclo de inadimplência.

    `_materializar_assinatura` devolve False e não escreve status nenhum; o
    retorno era DESCARTADO no call site e o clear rodava do mesmo jeito, dando a
    um evento já classificado como obsoleto o poder de apagar o relógio.
    """
    uid, client, fake = _cenario_evento_velho(monkeypatch, user_id, "dev2")
    try:
        antes = _relogio(uid)
        # Velho: created ANTERIOR ao +200 que criou o grant.
        assert _post(client, fake, _paid(uid, "evt_dev2_velho", _T_LIFE + 100),
                     subs={_SUB: _fake_sub("active")}).status_code == 200
        assert _relogio(uid) == antes, "evento velho zerou o relógio"
        assert db.get_auth_user(uid)["last_payment_status"] == "past_due"

        # POSITIVO, mesma conta: o `paid` ACEITO (versão mais nova) zera.
        assert _post(client, fake, _paid(uid, "evt_dev2_ok", _T_LIFE + 400),
                     subs={_SUB: _fake_sub("active")}).status_code == 200
        assert _relogio(uid) is None, "quem pagou de verdade não foi destravado"
    finally:
        _cleanup_trial(uid)


def test_R3_checkout_velho_nao_zera_o_relogio(user_id, monkeypatch):
    """R3, o IRMÃO que o apontamento não cobria: `checkout.session.completed`
    tinha o mesmo descarte de retorno e o mesmo clear incondicional."""
    uid, client, fake = _cenario_evento_velho(monkeypatch, user_id, "dev3")
    try:
        antes = _relogio(uid)
        assert _post(client, fake, _checkout(uid, "evt_dev3_velho", _T_LIFE + 100),
                     subs={_SUB: _fake_sub("active")}).status_code == 200
        assert _relogio(uid) == antes, "checkout velho zerou o relógio"
        assert db.get_auth_user(uid)["last_payment_status"] == "past_due"

        # POSITIVO, mesma conta: o checkout ACEITO zera.
        assert _post(client, fake, _checkout(uid, "evt_dev3_ok", _T_LIFE + 400),
                     subs={_SUB: _fake_sub("active")}).status_code == 200
        assert _relogio(uid) is None, "assinatura nova não fechou o ciclo"
    finally:
        _cleanup_trial(uid)

