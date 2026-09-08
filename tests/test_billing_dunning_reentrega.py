"""
tests/test_billing_dunning_reentrega.py — o relógio da inadimplência × a
REENTREGA do mesmo evento de cobrança.

O irmão de `test_billing_dunning_eventos.py`, e a distinção entre os dois é o
DISCRIMINADOR, não o tamanho:

  • lá  — o VEREDITO do evento (`_decidiu_acesso`): evento estritamente VELHO
    não pode escrever no relógio;
  • aqui — a IDADE do relógio: evento cuja versão é IGUAL à do grant **passa**
    pelo veredito de propósito (`upsert_grant` devolve o `id` para versão igual,
    é o que faz o 5xx da Stripe ser retryable), e ainda assim não pode apagar um
    ciclo aberto DEPOIS dele.

A causa da assimetria: o `invoice.payment_failed` que abre o ciclo novo **não
escreve grant**, então não avança a marca d'água de `event_version`. O gate da
rodada anterior era necessário e não suficiente — a tabela de estados × eventos
(`docs/dunning_estados_eventos.md`) põe as duas células lado a lado: nº 7 é o
evento velho (fechada pelo gate) e nº 5 é a reentrega (fechada pelo predicado
`nao_mais_novo_que` de `db.dunning.clear_past_due_since`).

Arquivo NOVO porque `test_billing_dunning_eventos.py` bate no teto de 350
linhas de `tests/test_max_lines_python.py` com estes dois casos dentro. Helpers
por IMPORT do irmão (§0.7), que por sua vez os importa de
`test_billing_webhook_lifecycle`.

CONTROLE NEGATIVO DECLARADO — em `db/dunning.py::clear_past_due_since`, tire o
predicado do `where` (volte para `where user_id = %s`, sem o
`and past_due_since <= to_timestamp(%s)` e sem o `%s` correspondente):
    VERMELHO: test_R4_reentrega_de_invoice_paid_nao_apaga_o_ciclo_novo
              test_R5_reentrega_de_checkout_nao_apaga_o_ciclo_novo
    VERDE:    R1, R2, R3 do irmão, e T3a/T3b/T5 de
              `test_billing_dunning_webhook.py` — os `paid`/`checkout` NORMAIS,
              que a injeção NÃO pode reprovar.

**A injeção é a REENTREGA, e não o evento velho, de propósito** (§3, "injete
onde discrimina"): com evento velho estes dois casos ficam VERDES no head
anterior — o gate `_decidiu_acesso` já os cobria — e mediriam o conserto da
rodada 2, não este.

CONTROLES POSITIVOS (o conserto RESTRINGE; sem eles o arquivo passaria num
código que nunca limpa o relógio):
  • R4 fase 4 e R5 fase 3 — o evento NOVO continua fechando o ciclo de quem
    pagou (célula 3), na MESMA conta;
  • R4 fase 5 — a reentrega do paid DO PRÓPRIO ciclo continua limpando (célula
    6, o 5xx que cai entre a escrita do grant e o clear). É o caso que um gate
    por "o upsert APLICOU?" quebraria, e a razão de o limite ser a idade do
    relógio.
"""
from __future__ import annotations

import pytest

import db
from _billing_grants_helpers import garantir_system_event_logs
from test_billing_dunning_eventos import (
    _SUB,
    _T_PASSADO,
    _checkout,
    _conta_emails,
    _failed,
    _paid,
    _relogio,
)
from test_billing_webhook_lifecycle import _T_LIFE, _cleanup_trial, _fake_sub, _post, _setup


@pytest.fixture(autouse=True)
def _event_logs():
    """A dedupe do e-mail de falha é `recent_event_exists`, e `system_event_logs`
    não nasce do `init_db` — sem a tabela ela devolve False por exceção."""
    garantir_system_event_logs()


# Nos dois casos o `Subscription.retrieve` devolve `past_due` de propósito. Com
# `active`, o `set_payment_status` de `_materializar_assinatura` já zeraria o
# relógio no MESMO UPDATE (a invariante, mantida na escrita) e o teste não
# mediria o clear nenhuma — passaria com e sem o conserto.

def test_R4_reentrega_de_invoice_paid_nao_apaga_o_ciclo_novo(user_id, monkeypatch):
    """R4: a reentrega de um `invoice.paid` anterior ao ciclo não zera o relógio.

    Cinco fases na MESMA conta: o grant nasce numa versão do passado, a falha
    abre o ciclo, a reentrega do paid antigo é recusada (célula 5), e os dois
    positivos provam que o predicado não recusa tudo.
    """
    uid, client, fake = _setup(monkeypatch, f"dev4-{user_id}")
    _conta_emails(monkeypatch)
    atrasado = {_SUB: _fake_sub("past_due")}
    try:
        # 1) o grant nasce na versão `_T_PASSADO`.
        assert _post(client, fake, _paid(uid, "evt_dev4_p1", _T_PASSADO),
                     subs={_SUB: _fake_sub("active")}).status_code == 200
        assert _relogio(uid) is None

        # 2) o cartão falha: relógio carimbado com `now()`, que é DEPOIS de
        #    `_T_PASSADO` — é essa ordem que a fase 3 mede.
        assert _post(client, fake, _failed(uid, "evt_dev4_f1", _T_PASSADO + 10),
                     subs=atrasado).status_code == 200
        antes = _relogio(uid)
        assert antes is not None

        # 3) NEGATIVO (célula 5): a REENTREGA do mesmo paid antigo. `upsert_grant`
        #    devolve o `id` (versão IGUAL) e `_decidiu_acesso` é True, então o
        #    gate da rodada anterior deixa passar; quem recusa é o predicado da
        #    escrita.
        assert _post(client, fake, _paid(uid, "evt_dev4_p1", _T_PASSADO),
                     subs=atrasado).status_code == 200
        assert _relogio(uid) == antes, \
            "reentrega de paid anterior ao ciclo apagou o relógio (célula 5)"
        assert db.get_auth_user(uid)["last_payment_status"] == "past_due"

        # 4) POSITIVO (célula 3): o paid NOVO, com `created` depois do carimbo,
        #    continua fechando o ciclo de quem pagou de verdade.
        assert _post(client, fake, _paid(uid, "evt_dev4_p2", _T_LIFE + 500),
                     subs=atrasado).status_code == 200
        assert _relogio(uid) is None, "quem pagou de verdade não foi destravado"

        # 5) POSITIVO (célula 6): 5xx entre a escrita do grant e o clear. O
        #    ciclo é recarimbado e a REENTREGA do MESMO paid — versão IGUAL,
        #    `created` depois do carimbo — tem de limpar.
        assert _post(client, fake, _failed(uid, "evt_dev4_f2", _T_LIFE + 450),
                     subs=atrasado).status_code == 200
        assert _relogio(uid) is not None
        assert _post(client, fake, _paid(uid, "evt_dev4_p2", _T_LIFE + 500),
                     subs=atrasado).status_code == 200
        assert _relogio(uid) is None, \
            "reentrega do paid do PRÓPRIO ciclo deixou de limpar (célula 6)"
    finally:
        _cleanup_trial(uid)


def test_R5_reentrega_de_checkout_nao_apaga_o_ciclo_novo(user_id, monkeypatch):
    """R5, o IRMÃO que o apontamento não citava: `checkout.session.completed`
    tem a reentrega idêntica ao R4 e o mesmo gate insuficiente. Achado pela
    varredura da classe, não pelo revisor (§2)."""
    uid, client, fake = _setup(monkeypatch, f"dev5-{user_id}")
    _conta_emails(monkeypatch)
    atrasado = {_SUB: _fake_sub("past_due")}
    try:
        assert _post(client, fake, _checkout(uid, "evt_dev5_c1", _T_PASSADO),
                     subs={_SUB: _fake_sub("active")}).status_code == 200
        assert _post(client, fake, _failed(uid, "evt_dev5_f1", _T_PASSADO + 10),
                     subs=atrasado).status_code == 200
        antes = _relogio(uid)
        assert antes is not None

        # NEGATIVO (célula 5, no ramo do checkout).
        assert _post(client, fake, _checkout(uid, "evt_dev5_c1", _T_PASSADO),
                     subs=atrasado).status_code == 200
        assert _relogio(uid) == antes, \
            "reentrega de checkout anterior ao ciclo apagou o relógio"
        assert db.get_auth_user(uid)["last_payment_status"] == "past_due"

        # POSITIVO (célula 3): a assinatura NOVA continua fechando o ciclo.
        assert _post(client, fake, _checkout(uid, "evt_dev5_c2", _T_LIFE + 500),
                     subs=atrasado).status_code == 200
        assert _relogio(uid) is None, "assinatura nova não fechou o ciclo"
    finally:
        _cleanup_trial(uid)
