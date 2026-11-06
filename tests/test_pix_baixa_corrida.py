"""#1267: corridas RECEIVED x UNDONE x estorno no mesmo pagamento. Cada teste injeta o outro evento
num ponto exato do primeiro; as guardas são `veto_grant`, a releitura sob o lock em
`desfazer_baixa`/`reconciliar_grant` e o lock do `grant` no `revoke` do estorno."""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager

import core.services.asaas as asaas
import core.services.pix_baixa_desfeita as m
import core.services.pix_drain as d
import core.services.pix_drain_effects as eff
from _dreno_pix_helpers import entregar, evento, ler, nova_cobranca
from core.services.pix_drain import drenar_evento
from test_pix_baixa_desfeita import UNDONE, _ativos, _morre_antes_de_reabrir, _paga, conta, externo  # noqa: F401


def _antes_do_lock_grant(monkeypatch, modulo, acao):
    real = modulo.lock_efeito
    feito = []

    @contextmanager
    def lk(p, e):
        if e == "grant" and not feito:
            feito.append(1)
            acao()
        with real(p, e):
            yield

    monkeypatch.setattr(modulo, "lock_efeito", lk)


def test_undone_entre_get_e_transicao_nao_concede(user_id, externo, monkeypatch):  # B2
    conta(user_id, "free", None)
    cob = nova_cobranca(user_id)
    real = d.conferir_valor

    def p(*a, **k):
        monkeypatch.setattr(d, "conferir_valor", real)
        entregar(UNDONE, cob)  # le pending: nao revoga nem reabre
        return real(*a, **k)

    monkeypatch.setattr(d, "conferir_valor", p)
    entregar("PAYMENT_RECEIVED", cob)  # GET: PENDING
    assert _ativos(user_id) == []


def test_pix_real_esperando_o_lock_com_cobranca_reaberta_concede_na_retentativa(user_id, externo, monkeypatch):  # B1
    cob = _paga(user_id)

    def undone_e_pix_real():
        externo["get_status"] = "PENDING"
        entregar(UNDONE, cob)
        externo["get_status"] = "RECEIVED"

    _antes_do_lock_grant(monkeypatch, d, undone_e_pix_real)
    externo["get_status"] = "RECEIVED"
    eid = entregar("PAYMENT_RECEIVED", cob)
    assert evento(eid)["processed_at"] is None and evento(eid)["attempts"] == 1  # nao fecha sem acesso
    drenar_evento(eid)
    assert len(_ativos(user_id)) == 1 and ler(cob["id"])["status"] == "paid"
    assert evento(eid)["processed_at"] is not None


def test_pix_real_sem_marcador_concede_sem_get(user_id, externo, monkeypatch):  # positivo da guarda
    gets = []
    monkeypatch.setattr(asaas, "buscar_pagamento", lambda p: gets.append(p) or {"status": "PENDING"})
    _paga(user_id)
    assert gets == [] and len(_ativos(user_id)) == 1


def test_reconciliar_com_estorno_antes_do_lock_nao_reconcede(user_id, externo, monkeypatch):  # T6
    cob, _ = _morre_antes_de_reabrir(externo, monkeypatch, user_id)
    _antes_do_lock_grant(monkeypatch, m, lambda: entregar("PAYMENT_REFUNDED", cob))
    externo["get_status"] = "RECEIVED"
    entregar("PAYMENT_RECEIVED", cob)
    assert _ativos(user_id) == [] and ler(cob["id"])["status"] == "refunded"


def test_estorno_durante_o_grant_espera_e_revoga_depois(user_id, externo, monkeypatch):  # T7
    conta(user_id, "free", None)
    cob = nova_cobranca(user_id)
    real = eff.EXECUTORES["grant"]
    t = []

    def p(c, e):
        monkeypatch.setitem(eff.EXECUTORES, "grant", real)
        t.append(threading.Thread(target=entregar, args=("PAYMENT_REFUNDED", cob)))
        t[0].start()
        time.sleep(0.5)  # o revoke do estorno tem de esperar o lock do grant
        return real(c, e)

    monkeypatch.setitem(eff.EXECUTORES, "grant", p)
    entregar("PAYMENT_RECEIVED", cob)
    t[0].join(10)
    assert _ativos(user_id) == [] and ler(cob["id"])["status"] == "refunded"



def test_estorno_total_antes_do_lock_sem_marcador_nao_concede(user_id, externo, monkeypatch):  # M6
    conta(user_id, "free", None)
    cob = nova_cobranca(user_id)
    _antes_do_lock_grant(monkeypatch, d, lambda: entregar("PAYMENT_REFUNDED", cob))
    entregar("PAYMENT_RECEIVED", cob)
    assert _ativos(user_id) == [] and ler(cob["id"])["status"] == "refunded"


def test_cobranca_paga_antes_do_lock_do_undone_e_reaberta(user_id, externo, monkeypatch):  # M3
    conta(user_id, "free", None)
    cob = nova_cobranca(user_id)

    def pix_real_e_volta():
        externo["get_status"] = "RECEIVED"
        entregar("PAYMENT_RECEIVED", cob)
        externo["get_status"] = "PENDING"

    _antes_do_lock_grant(monkeypatch, m, pix_real_e_volta)
    entregar(UNDONE, cob)  # GET PENDING; le pending, vira paid antes do lock
    assert ler(cob["id"])["status"] == "pending" and _ativos(user_id) == []


def test_estorno_parcial_entre_falha_e_retentativa_do_grant_concede(user_id, externo, monkeypatch):  # M9
    conta(user_id, "free", None)
    cob = nova_cobranca(user_id)
    real = eff.EXECUTORES["grant"]
    monkeypatch.setitem(eff.EXECUTORES, "grant", lambda c, e: (_ for _ in ()).throw(RuntimeError("x")))
    eid = entregar("PAYMENT_RECEIVED", cob)
    assert _ativos(user_id) == [] and evento(eid)["processed_at"] is None
    entregar("PAYMENT_PARTIALLY_REFUNDED", cob)
    assert ler(cob["id"])["status"] == "refunded_partial"
    monkeypatch.setitem(eff.EXECUTORES, "grant", real)
    drenar_evento(eid)
    assert len(_ativos(user_id)) == 1


def test_undone_de_outro_pagamento_com_cobranca_paga_antes_do_lock_nao_revoga(user_id, externo, monkeypatch):  # #1257
    conta(user_id, "free", None)
    cob = nova_cobranca(user_id)  # pending, paga depois por `asaas_payment_id`

    def pix_real():
        externo["get_status"] = "RECEIVED"
        entregar("PAYMENT_RECEIVED", cob)
        externo["get_status"] = "PENDING"

    _antes_do_lock_grant(monkeypatch, m, pix_real)
    entregar(UNDONE, cob, payment_id="pay_retido_outro")
    assert len(_ativos(user_id)) == 1 and ler(cob["id"])["status"] == "paid"
