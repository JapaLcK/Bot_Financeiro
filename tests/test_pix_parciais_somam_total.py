"""#1571: estornos parciais DONE que somam o devido são estorno TOTAL.

Dreno real (`entregar`) e banco real; só o `refunds[]` do Asaas é simulado (`externo["refunds"]`).
Anual de R$ 499,00.

CONTROLES NEGATIVOS MEDIDOS (um a um, com o resto do grupo verde):

  * `>=` -> `>` em `pix_valor.parcial_vira_total` (499,00 exato não vira total) ->
    `test_dois_parciais_somando_tudo_revogam`, `test_comissao_do_afiliado_e_estornada_como_total`,
    `test_reentregas_depois_do_total_...` e `test_fora_de_ordem_...` VERMELHOS (+ o D1-d de
    `test_pix_dreno_estorno.py`); `test_parcial_unico_que_cobre_tudo_revoga` (499,01) segue verde;
  * `parcial_vira_total` devolvendo sempre `evt` -> os mesmos + `test_parcial_unico_...` VERMELHOS
    (6 no total, com o D1-d); `test_asaas_fora_...` segue verde por desenho (fecha parcial igual);
  * `if promovido` desligado (marcador #1437 gravado por este evento não vale como promoção) ->
    `test_revoke_que_falha_na_1a_passada_e_refeito_na_retentativa` VERMELHO;
  * `gravar_no_dreno` tirado da guarda de órfão em `_decidir` ->
    `test_orfao_que_falha_no_rotulo_e_refeito_como_total` VERMELHO.

POSITIVOS do grupo: `test_abaixo_do_devido_ou_pendente_segue_parcial` (498,99 e DONE+PENDING mantêm o
acesso) e o `assert` do `_paga` (RECEIVED sem estorno concede, em todo teste); sem eles o grupo passaria num dreno que revoga tudo.
"""
from __future__ import annotations

import pytest

from _billing_grants_helpers import conta, garantir_system_event_logs
from _dreno_pix_helpers import efeitos, entregar, evento, ler, mundo_externo, nova_cobranca
from db.connection import get_conn
from db.pix_estornos import estorno_registrado
from db.plan_grants import list_grants
from test_afiliado_pagantes import _indicado  # noqa: F401
from test_afiliado_comissao_pix import _afiliado  # noqa: F401

PARCIAL = "PAYMENT_PARTIALLY_REFUNDED"
DOIS = [{"value": 249.5, "status": "DONE"}, {"value": 249.5, "status": "DONE"}]


@pytest.fixture()
def externo(monkeypatch):
    garantir_system_event_logs()
    return mundo_externo(monkeypatch)


def _ativos(uid):
    return [g for g in list_grants(uid) if g["source"] == "pix" and g["status"] == "active"]


def _paga(uid):
    conta(uid, "free", None)
    cobranca = nova_cobranca(uid)
    entregar("PAYMENT_RECEIVED", cobranca)
    assert len(_ativos(uid)) == 1
    return cobranca


def test_dois_parciais_somando_tudo_revogam(user_id, externo):
    cobranca = _paga(user_id)
    pid = cobranca["asaas_payment_id"]
    externo["refunds"] = DOIS[:1]
    entregar(PARCIAL, cobranca)
    assert (ler(cobranca["id"])["status"], len(_ativos(user_id))) == ("refunded_partial", 1)
    externo["refunds"] = DOIS
    entregar(PARCIAL, cobranca)
    assert ler(cobranca["id"])["status"] == "refunded"
    assert {"revoke", "afiliado_estorno"} <= efeitos(pid)
    assert _ativos(user_id) == [] and estorno_registrado(pid)


def test_comissao_do_afiliado_e_estornada_como_total(user_id, externo):
    ind = _indicado(_afiliado())
    conta(ind.uid, "free", None)
    cobranca = nova_cobranca(ind.uid, plan="pro", plan_stored="pro", price_cents=49900,
                             credit_cents=0, amount_cents=49900)
    entregar("PAYMENT_RECEIVED", cobranca)
    externo["refunds"] = DOIS
    entregar(PARCIAL, cobranca)
    with get_conn() as conn:
        (linha,) = conn.execute("select status from affiliate_commissions"
                                " where referred_user_id = %s", (ind.uid,)).fetchall()
        rev = conn.execute("select tipo from affiliate_refund_reviews where stripe_invoice_id = %s",
                           (cobranca["external_reference"],)).fetchone()
    assert (linha["status"], rev["tipo"]) == ("reversed", "total")


def test_parcial_unico_que_cobre_tudo_revoga(user_id, externo):
    cobranca = _paga(user_id)
    externo["refunds"] = [{"value": 499.00}, {"value": 0.01}]  # passou do devido
    entregar(PARCIAL, cobranca)
    assert ler(cobranca["id"])["status"] == "refunded" and _ativos(user_id) == []


@pytest.mark.parametrize("refunds", [
    [{"value": 249.5, "status": "DONE"}, {"value": 249.49, "status": "DONE"}],   # 498,99
    [{"value": 249.5, "status": "DONE"}, {"value": 249.5, "status": "PENDING"}],  # 2º ainda não saiu
], ids=["498_99", "done_mais_pendente"])
def test_abaixo_do_devido_ou_pendente_segue_parcial(user_id, externo, refunds):
    cobranca = _paga(user_id)
    externo["refunds"] = refunds
    entregar(PARCIAL, cobranca)
    assert ler(cobranca["id"])["status"] == "refunded_partial"
    assert len(_ativos(user_id)) == 1 and "revoke" not in efeitos(cobranca["asaas_payment_id"])


def test_reentregas_depois_do_total_nao_concedem_nem_duplicam(user_id, externo):
    cobranca = _paga(user_id)
    pid = cobranca["asaas_payment_id"]
    externo["refunds"] = DOIS
    entregar(PARCIAL, cobranca)
    antes = (efeitos(pid), len(list_grants(user_id)))
    entregar("PAYMENT_RECEIVED", cobranca)
    assert _ativos(user_id) == [] and ler(cobranca["id"])["status"] == "refunded"
    entregar("PAYMENT_REFUNDED", cobranca)
    assert ler(cobranca["id"])["status"] == "refunded" and _ativos(user_id) == []
    assert (efeitos(pid), len(list_grants(user_id))) == antes


def test_fora_de_ordem_cobranca_pending_cancela_e_received_nao_concede(user_id, externo):
    conta(user_id, "free", None)
    cobranca = nova_cobranca(user_id)
    externo["refunds"] = DOIS
    entregar(PARCIAL, cobranca)
    entregar(PARCIAL, cobranca)
    assert ler(cobranca["id"])["status"] == "canceled"
    entregar("PAYMENT_RECEIVED", cobranca)
    assert _ativos(user_id) == [] and ler(cobranca["id"])["status"] == "canceled"


@pytest.mark.parametrize("falha", [{"get_erro": RuntimeError("asaas fora")}, {"refunds": "x"}], ids=["fora", "malformado"])
def test_asaas_fora_fecha_como_parcial_sem_revogar(user_id, externo, falha):
    cobranca = _paga(user_id)
    externo.update(falha)
    externo["alerta"].clear()
    evt = entregar(PARCIAL, cobranca)
    assert evento(evt)["processed_at"] is not None and "revoke" not in efeitos(cobranca["asaas_payment_id"])
    assert ler(cobranca["id"])["status"] == "refunded_partial" and len(_ativos(user_id)) == 1
    assert any("acumulado indisponível" in a for a in externo["alerta"])


def test_revoke_que_falha_na_1a_passada_e_refeito_na_retentativa(user_id, externo, monkeypatch):
    """O marcador #1437 e a transição já saíram; a retentativa relê o evento como parcial e a cobrança
    já está `refunded`: sem o marcador valendo como promoção, fechava sem revoke (acesso vivo)."""
    from core.services import pix_drain

    cobranca = _paga(user_id)
    pid = cobranca["asaas_payment_id"]
    externo["refunds"] = DOIS
    orig = pix_drain.EXECUTORES["revoke"]

    def _falha(*a, **k):
        raise RuntimeError("revoke fora")

    monkeypatch.setitem(pix_drain.EXECUTORES, "revoke", _falha)
    evt = entregar(PARCIAL, cobranca)
    assert (evento(evt)["processed_at"], ler(cobranca["id"])["status"]) == (None, "refunded")
    assert len(_ativos(user_id)) == 1
    monkeypatch.setitem(pix_drain.EXECUTORES, "revoke", orig)
    pix_drain.drenar_evento(evt)
    assert evento(evt)["processed_at"] is not None and _ativos(user_id) == []
    assert {"revoke", "afiliado_estorno"} <= efeitos(pid)


def test_paid_orphan_com_parciais_somando_tudo_vira_refunded(user_id, externo):
    cobranca = _paga(user_id)
    with get_conn() as conn:
        conn.execute("update pix_charges set status = 'paid_orphan', user_id = null where id = %s",
                     (cobranca["id"],))
        conn.commit()
    externo["refunds"] = DOIS
    entregar(PARCIAL, cobranca)
    assert ler(cobranca["id"])["status"] == "refunded"


def test_orfao_que_falha_no_rotulo_e_refeito_como_total(user_id, externo, monkeypatch):
    """Codex #1632: no órfão o `_orfao` volta antes do `estorno_no_dreno`; sem o marcador gravado antes,
    a retentativa (já `refunded`) relia o evento como parcial e rotulava `refunded_partial`."""
    from core.services import pix_drain

    cobranca = _paga(user_id)
    with get_conn() as conn:
        conn.execute("update pix_charges set status = 'paid_orphan', user_id = null where id = %s",
                     (cobranca["id"],))
        conn.commit()
    externo["refunds"] = DOIS
    vistos = []

    def _rotulo(cob, status, tentativas=0):
        vistos.append(status)
        if len(vistos) == 1:
            raise RuntimeError("rótulo fora")

    monkeypatch.setattr(pix_drain, "rotular_reembolso", _rotulo)
    evt = entregar(PARCIAL, cobranca)
    assert evento(evt)["processed_at"] is None
    pix_drain.drenar_evento(evt)
    assert evento(evt)["processed_at"] is not None
    assert vistos == ["refunded", "refunded"]


def test_parcial_mais_velho_que_o_grant_ainda_revoga(user_id, externo):
    """Codex #1632: parcial enfileirado ANTES do RECEIVED atrasado, drenado depois. A decisão vem do GET
    ao Asaas, não da versão do evento: a guarda de versão do `revoke_grant` não pode descartá-lo."""
    import uuid

    from core.services.pix_drain import drenar_evento
    from db.webhook_outbox import registrar_evento

    cobranca = _paga(user_id)
    externo["refunds"] = DOIS
    eid = f"evt_{uuid.uuid4().hex[:16]}"
    registrar_evento(eid, PARCIAL, {"id": eid, "event": PARCIAL, "payment": {
        "id": cobranca["asaas_payment_id"], "externalReference": cobranca["external_reference"],
        "value": 499.0, "status": "PARTIALLY_REFUNDED"}}, 1)
    drenar_evento(eid)
    assert ler(cobranca["id"])["status"] == "refunded" and _ativos(user_id) == []


@pytest.mark.parametrize("status", ["expired", "canceled"])
def test_expirada_ou_cancelada_com_parciais_somando_tudo_barra_o_received(user_id, externo, status):
    """Codex #1632: OVERDUE/DELETED drenado antes do RECEIVED; os parciais somam tudo e o RECEIVED
    atrasado não pode conceder (nem gerar comissão)."""
    conta(user_id, "free", None)
    cobranca = nova_cobranca(user_id)
    with get_conn() as conn:
        conn.execute("update pix_charges set status = %s where id = %s", (status, cobranca["id"]))
        conn.commit()
    externo["refunds"] = DOIS
    entregar(PARCIAL, cobranca)
    assert estorno_registrado(cobranca["asaas_payment_id"])
    entregar("PAYMENT_RECEIVED", cobranca)
    assert _ativos(user_id) == []


_CADERNOS = [{"price": "price_cad_a", "url": "https://drive.test/a", "nome": "Caderno A", "valor_cents": 1990}]


def test_com_cadernos_o_devido_e_plano_mais_cadernos(user_id, externo):
    conta(user_id, "free", None)
    cobranca = nova_cobranca(user_id, extras=_CADERNOS)  # 499,00 + 19,90 = 518,90
    entregar("PAYMENT_RECEIVED", cobranca)
    externo["refunds"] = [{"value": 499.00, "status": "DONE"}]  # só o plano: segue parcial
    entregar(PARCIAL, cobranca)
    assert (ler(cobranca["id"])["status"], len(_ativos(user_id))) == ("refunded_partial", 1)
    externo["refunds"] = [{"value": 499.00}, {"value": 19.90}]
    entregar(PARCIAL, cobranca)
    assert ler(cobranca["id"])["status"] == "refunded" and _ativos(user_id) == []
