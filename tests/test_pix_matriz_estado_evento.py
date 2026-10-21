"""A matriz 11 estados x 13 eventos do dreno do Pix, escrita À MÃO (#819, G1).

`TABELA` abaixo é o §11 do plano lido célula a célula, NÃO derivado de
`ROTEAMENTO`: um teste que calculasse o esperado a partir do código seria verde
por construção. Cada célula monta a linha no estado (pelo caminho de produção,
inclusive o `paid` que vem de um `PAYMENT_RECEIVED` de verdade), entrega UM evento
pela outbox + `drenar_evento`, banco real, e afirma três coisas: o status final,
os efeitos NOVOS em `pix_payment_effects` e os grants `pix` ativos do dono.

A propriedade que fecha a CLASSE (e não só o caso): **nenhuma célula deixa o
evento retentando** (`processed_at` preenchido, `attempts == 0`). O #819 era
exatamente uma célula que não convergia.

CONTROLES, medidos no G1 (saídas no relato do PR):
  * NEGATIVO: volte `_ORIGENS_DE_PAGAMENTO` (core/services/pix_drain.py) a
    `("pending", "canceling", "canceled", "expired")` → ficam VERMELHAS as 4
    células `draft`/`creating` x `PAYMENT_RECEIVED`/`PAYMENT_CONFIRMED`
    (`NotNullViolation` no `last_error`), e nenhuma outra;
  * NEGATIVO 2: remova a guarda de `payment.id` ausente do `_decidir` → VERMELHO
    `test_evento_sem_id_nao_paga_a_linha_sem_id`;
  * NEGATIVO 3: tire o `destino == "paid" and` do `_orfao` →
    VERMELHO `test_orfao_so_alerta_no_pagamento`.
  * POSITIVO: as células `refunded` x RECEIVED/CONFIRMED (dinheiro devolvido não
    é reconcedido) e `pending`/`paid` x RECEIVED — um conserto que fizesse
    "qualquer estado vira paid" passaria no negativo e quebraria estas.
"""
from __future__ import annotations

import secrets
import uuid

import pytest

from _billing_grants_helpers import conta, garantir_system_event_logs
from _dreno_pix_helpers import efeitos, entregar, evento, ler, mundo_externo
from db.connection import get_conn
from db.pix_charges import criar_cobranca, transicionar
from db.pix_charges_saga import attach_pagamento
from db.plan_grants import list_grants

ESTADOS = ("draft", "creating", "pending", "canceling", "canceled", "expired",
           "paid", "paid_orphan", "refunded_partial", "refunded", "chargeback")
EVENTOS = ("PAYMENT_RECEIVED", "PAYMENT_CONFIRMED", "PAYMENT_CREATED",
           "PAYMENT_OVERDUE", "PAYMENT_DELETED", "PAYMENT_REFUNDED",
           "PAYMENT_PARTIALLY_REFUNDED", "PAYMENT_REFUND_IN_PROGRESS",
           "PAYMENT_REFUND_DENIED", "PAYMENT_CHARGEBACK_REQUESTED",
           "PAYMENT_CHARGEBACK_DISPUTE", "PAYMENT_AWAITING_CHARGEBACK_REVERSAL",
           "PAYMENT_UPDATED")                  # o último é desconhecido de propósito

COMPRA = frozenset({"stripe_cancel", "grant", "ebook", "ga4", "capi", "email"})
NADA = frozenset()
REVOKE = frozenset({"revoke"})
REC = ("PAYMENT_RECEIVED", "PAYMENT_CONFIRMED")


def _ninguem(status, grants):
    """Células em que o evento não muda nada (`—`/no-op do §11)."""
    return {e: (status, NADA, grants) for e in EVENTOS}


def _linha(status, grants, **mudancas):
    return _ninguem(status, grants) | mudancas


def _paga_tudo(grants_final=1):
    return {e: ("paid", COMPRA, grants_final) for e in REC}


# (status final, efeitos NOVOS, grants pix ativos). Escrita à mão, linha a linha.
TABELA = {
    # sem id remoto: RECEIVED paga mesmo assim (o dinheiro entrou, §8.2); o QR nunca saiu.
    "draft": _linha("draft", 0, **_paga_tudo(), PAYMENT_REFUNDED=("draft", REVOKE, 0)),
    "creating": _linha("creating", 0, **_paga_tudo(),
                       PAYMENT_REFUNDED=("creating", REVOKE, 0)),
    "pending": _linha("pending", 0, **_paga_tudo(),
                      PAYMENT_OVERDUE=("expired", NADA, 0),
                      PAYMENT_DELETED=("canceled", NADA, 0),
                      PAYMENT_REFUNDED=("pending", REVOKE, 0)),
    "canceling": _linha("canceling", 0, **_paga_tudo(),
                        PAYMENT_OVERDUE=("expired", NADA, 0),
                        PAYMENT_DELETED=("canceled", NADA, 0),
                        PAYMENT_REFUNDED=("canceling", REVOKE, 0)),
    "canceled": _linha("canceled", 0, **_paga_tudo(),
                       PAYMENT_REFUNDED=("canceled", REVOKE, 0)),
    "expired": _linha("expired", 0, **_paga_tudo(),
                      PAYMENT_REFUNDED=("expired", REVOKE, 0)),
    # `paid` vem de um RECEIVED de verdade: os 6 efeitos já estão registrados.
    "paid": _linha("paid", 1,
                   PAYMENT_REFUNDED=("refunded", REVOKE, 0),
                   PAYMENT_PARTIALLY_REFUNDED=("refunded_partial", NADA, 1)),
    # titular excluído: a transição acontece, o efeito de compra e o `revoke` NÃO.
    "paid_orphan": _linha("paid_orphan", 0,
                          PAYMENT_REFUNDED=("refunded", NADA, 0),
                          PAYMENT_PARTIALLY_REFUNDED=("refunded_partial", NADA, 0)),
    "refunded_partial": _linha("refunded_partial", 1,
                               PAYMENT_REFUNDED=("refunded", REVOKE, 0)),
    # `revoke` já registrado: o RECEIVED atrasado NÃO reconcede (a célula positiva).
    "refunded": _linha("refunded", 0),
    # só se chega a `chargeback` por reconciliação manual (a linha do §11).
    "chargeback": _linha("chargeback", 1, PAYMENT_REFUNDED=("chargeback", REVOKE, 0)),
}


def _sql(q, a=()):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(q, a)
        conn.commit()


def montar(estado: str, uid: int) -> dict:
    """A linha no `estado`, pelo caminho de produção. Devolve o necessário para
    `entregar`: o id remoto fica FORA da linha em draft/creating (é só do evento)."""
    conta(uid, "free", None)
    linha = criar_cobranca(uid, public_token=secrets.token_urlsafe(16), plan="pro_max",
                           plan_stored="pro_max", price_cents=49900, credit_cents=0,
                           amount_cents=49900, duration_days=365)
    cid, pid = linha["id"], f"pay_{uuid.uuid4().hex[:12]}"
    if estado != "draft":
        transicionar(cid, de="draft", para="creating")
    if estado not in ("draft", "creating"):
        assert attach_pagamento(cid, pid)                       # -> pending
    if estado == "canceling" or estado == "canceled":
        transicionar(cid, de="pending", para="canceling")
    if estado == "canceled":
        transicionar(cid, de="canceling", para="canceled", apagar_qr=True)
    if estado == "expired":
        transicionar(cid, de="pending", para="expired", apagar_qr=True)
    cob = dict(linha, asaas_payment_id=pid, status=estado)
    if estado == "paid_orphan":
        _sql("update pix_charges set user_id = null where id = %s", (cid,))
    if estado in ("paid", "paid_orphan", "refunded_partial", "refunded", "chargeback"):
        entregar("PAYMENT_RECEIVED", cob)
    if estado == "refunded_partial":
        entregar("PAYMENT_PARTIALLY_REFUNDED", cob)
    if estado == "refunded":
        entregar("PAYMENT_REFUNDED", cob)
    if estado == "chargeback":
        _sql("update pix_charges set status = 'chargeback' where id = %s", (cid,))
    assert ler(cid)["status"] == estado, f"fixture não chegou em {estado}"
    return cob


def _grants(uid):
    return len([g for g in list_grants(uid) if g["source"] == "pix" and g["status"] == "active"])


@pytest.fixture()
def externo(monkeypatch):
    garantir_system_event_logs()
    return mundo_externo(monkeypatch)


def test_a_tabela_tem_as_143_celulas():
    assert set(TABELA) == set(ESTADOS)
    assert all(set(t) == set(EVENTOS) for t in TABELA.values())


@pytest.mark.parametrize("estado", ESTADOS)
@pytest.mark.parametrize("tipo", EVENTOS)
def test_celula(user_id, externo, estado, tipo):
    cob = montar(estado, user_id)
    antes = efeitos(cob["asaas_payment_id"])
    eid = entregar(tipo, cob)
    esperado_status, esperado_efeitos, esperado_grants = TABELA[estado][tipo]
    ev = evento(eid)
    assert (ev["processed_at"] is not None and ev["attempts"] == 0), (
        f"{estado} x {tipo} não converge: attempts={ev['attempts']} last_error={ev['last_error']}")
    assert ler(cob["id"])["status"] == esperado_status
    assert efeitos(cob["asaas_payment_id"]) - antes == esperado_efeitos
    assert _grants(user_id) == esperado_grants


# ── as quebras de classe que a tabela sozinha não vê ─────────────────────────

@pytest.mark.parametrize("sem_id", ["", 12345], ids=["vazio", "inteiro"])
def test_evento_sem_id_nao_paga_a_linha_sem_id(user_id, externo, sem_id):
    """Linha `creating` sem id + evento sem `payment.id` utilizável: virar `paid`
    com id nulo faria `registrar_efeito(None)` estourar NotNull e reprocessar os
    efeitos (e-mail, GA4) a cada 60 s. A guarda devolve falha NOMEADA antes de tudo."""
    cob = montar("creating", user_id)
    eid = entregar("PAYMENT_RECEIVED", cob, payment_id=sem_id)
    ev = evento(eid)
    assert ev["processed_at"] is None and ev["attempts"] == 1
    assert ev["last_error"] == "PagamentoSemId"
    assert ler(cob["id"])["status"] == "creating"
    assert _grants(user_id) == 0 and externo["email"] == 0 and externo["ga4"] == 0


def test_evento_sem_id_de_so_transicao_converge(user_id, externo):
    """POSITIVO da guarda: ela é só para quem tem efeito. OVERDUE sem id numa
    `creating` não pode virar evento travado."""
    cob = montar("creating", user_id)
    ev = evento(entregar("PAYMENT_OVERDUE", cob, payment_id=""))
    assert ev["processed_at"] is not None and ev["attempts"] == 0


def test_estorno_antes_do_pagamento_em_creating_nao_concede(user_id, externo):
    """Fora de ordem: REFUNDED chega primeiro (registra `revoke`) e o RECEIVED
    depois, na linha `creating` que o #819 passou a pagar. Alerta D3, sem grant."""
    cob = montar("creating", user_id)
    entregar("PAYMENT_REFUNDED", cob)
    externo["alerta"].clear()
    ev = evento(entregar("PAYMENT_RECEIVED", cob))
    assert ev["processed_at"] is not None and ev["attempts"] == 0
    assert ler(cob["id"])["status"] == "creating", "a linha não pode virar paid sobre dinheiro devolvido"
    assert _grants(user_id) == 0 and efeitos(cob["asaas_payment_id"]) == {"revoke"}
    assert any("depois do estorno" in a for a in externo["alerta"])


def test_orfao_so_alerta_no_pagamento(user_id, externo):
    """A1/D9: o alerta de órfão era consumido pelo primeiro evento de QUALQUER
    tipo. Um OVERDUE gastava o alerta e o RECEIVED real não avisava ninguém."""
    cob = montar("pending", user_id)
    _sql("update pix_charges set user_id = null where id = %s", (cob["id"],))
    entregar("PAYMENT_OVERDUE", cob)
    assert externo["alerta"] == [] and "orphan_notified" not in efeitos(cob["asaas_payment_id"])
    entregar("PAYMENT_RECEIVED", cob)
    assert ler(cob["id"])["status"] == "paid_orphan"
    assert len(externo["alerta"]) == 1 and "órfão" in externo["alerta"][0]
    entregar("PAYMENT_CONFIRMED", cob)                      # POSITIVO: o alerta é único
    assert len(externo["alerta"]) == 1
