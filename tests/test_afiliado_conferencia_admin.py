"""#1538, PR 1: comissões em conferência na Stripe no admin, com Estornar e Concluir conferência.

Rota real, Postgres real; a Stripe é a falsa de `test_afiliado_conferencia` (`fonte`), com `formas` e `_presa`
para as faturas que nunca confirmam. A invariante de toda decisão: efeito financeiro e evento exatamente uma
vez. Os controles negativos estão no relato do PR; os positivos ficam aqui, nos casos que dizem "positivo".
"""
import json
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest

import core.admin_dashboard as admin_dashboard
import core.services.afiliado_reconciliacao as reconciliacao
import core.services.afiliado_reembolso as servico
import db
import db.affiliate_reviews_manutencao as manutencao
from _apoio_auth_app import csrf
from db import schema
from db.affiliate_reviews import registrar_reembolso
from db.affiliates import get_affiliate_stats, record_commission_for_invoice, record_referral, reject_payout, request_payout
from db.connection import get_conn
from test_afiliado_area import _afiliado, _comissao, _conta, _esperando_lock, _vence  # noqa: F401
from test_afiliado_conferencia import _cobranca, _enviar, fonte  # noqa: F401
from test_afiliado_exclusao import _saque_com_pix
from test_afiliado_fila_conferencia import _presa, formas  # noqa: F401
from test_afiliado_pagantes import AGORA, _admin
from test_afiliado_pendencia import OVERVIEW, _paga_e_saca, _parcial, _rev, _total
from test_afiliado_reembolso import _logs, _paga, mundo  # noqa: F401

JUST = "Indicado pediu o dinheiro de volta pelo suporte"
EVID = "ticket:SUP-4821"


@pytest.fixture
def adm(fonte, monkeypatch):
    return _admin(monkeypatch)


def _marca(com):
    manutencao.marcar_conferencia([com.id])
    return com


def _nascida(c, reais=100):
    """Pelo caminho real: a 1ª cobrança do cartão sem linha da fatura nasce marcada e sem `marked_at` (A5). A versão
    vista dela é (None, None), e "continua marcada" é a única guarda que a separa da que a Stripe já liberou."""
    indicado = _conta(plano=False)
    assert record_referral(c.aff["code"], indicado.uid)
    fatura = f"in_{uuid.uuid4().hex[:12]}"
    linha = record_commission_for_invoice(indicado.uid, fatura, reais * 1000)
    _vence(linha["id"])
    com = SimpleNamespace(id=linha["id"], indicado=indicado, fatura=fatura)
    assert (_db(com)["payment_check_pending"], _db(com)["payment_check_marked_at"]) == (True, None)
    return com


# As duas formas de entrar em conferência: a marca do webhook/D10 (com instante) e o nascimento marcado (sem).
ORIGENS = pytest.mark.parametrize("origem", ["marcada", "nascida"])


def _em_conferencia(c, origem, reais=100):
    return _marca(_comissao(c, reais)) if origem == "marcada" else _nascida(c, reais)


def _tentada(com, formas, monkeypatch, forma="sem_cobranca"):
    """A volta do worker: a fatura não confirma e grava o motivo (o `tried_at` também)."""
    _presa(formas, monkeypatch, com, forma)
    assert servico.conferir_comissao(com.id, com.fatura, adiar=True, lido=_db(com)["payment_check_marked_at"]) is False
    return com


def _db(com):
    with get_conn() as conn:
        return conn.execute("select * from affiliate_commissions where id = %s", (com.id,)).fetchone()


def _linha(adm, com):
    return next((x for x in adm.get(OVERVIEW).json()["commission_check_list"] if x["id"] == com.id), None)


def _decide(adm, com, acao, visto=None, **corpo):
    """Sem `visto`, a linha que a lista mostra agora: o formulário aberto e enviado sem fato novo no meio."""
    visto = visto or _linha(adm, com)
    base = {"justification": JUST, "evidence": EVID, "marked_at": visto["marked_at"], "reason": visto["reason"],
            "amount_cents": visto["amount_cents"]}
    return adm.post(f"/admin/api/affiliates/commissions/{com.id}/check-{acao}",
                    headers={**csrf(adm), "content-type": "application/json"}, content=json.dumps(base | corpo).encode())


def _eventos(com):
    with get_conn() as conn:
        return conn.execute("select * from affiliate_refund_review_events where commission_id = %s order by id",
                            (com.id,)).fetchall()


def _saques(c):
    with get_conn() as conn:
        return conn.execute("select id, status, amount_cents from affiliate_payouts where affiliate_id = %s order by id",
                            (c.aff["id"],)).fetchall()


def _fotografia(c, com):
    """Tudo que uma decisão poderia mexer em dinheiro."""
    s = get_affiliate_stats(c.aff["id"])
    with get_conn() as conn:
        revs = conn.execute("select r.status, r.recovery_cents, r.recovery_closed_cents from affiliate_refund_reviews r"
                            " join affiliate_commissions c on c.id = r.commission_id where c.affiliate_id = %s"
                            " order by r.id", (c.aff["id"],)).fetchall()
        coms = conn.execute("select id, status, amount_cents, payout_id from affiliate_commissions"
                            " where affiliate_id = %s order by id", (c.aff["id"],)).fetchall()
    return {"stats": {k: v for k, v in s.items() if k.endswith("_cents")}, "saques": _saques(c), "revs": revs,
            "coms": coms}


# ── A lista ──────────────────────────────────────────────────────────────────

def test_lista_mostra_motivo_datas_e_acoes(adm, formas, monkeypatch):
    c = _afiliado()
    com = _marca(_comissao(c, 100))
    linha = _linha(adm, com)
    assert (linha["code"], linha["amount_cents"], linha["reason"], linha["tried_at"]) == (c.aff["code"], 10_000, None, None)
    assert linha["marked_at"] and linha["marked_on"] == linha["marked_at"]
    assert linha["actions"] == {"reverse": None}  # sem tentativa: só Estornar
    _tentada(com, formas, monkeypatch)
    linha = _linha(adm, com)
    assert linha["reason"] == "sem_cobranca" and linha["tried_at"]
    assert linha["actions"] == {"release": "Liberar indisponível: origem do pagamento ainda não conferida.",
                                "reverse": None}
    _marca(com)  # remarcada: o motivo velho fica na coluna, mas não vale sem tentativa
    assert _db(com)["payment_check_reason"] == "sem_cobranca"
    assert _linha(adm, com)["reason"] is None


def test_lista_acoes_por_estado(adm, formas, monkeypatch):
    c = _afiliado()
    disputa = _tentada(_marca(_comissao(c, 100)), formas, monkeypatch, "disputa_aberta")
    assert _linha(adm, disputa)["actions"] == {"reverse": None}  # disputa: sem Liberar, Estornar pode (a issue)
    paga = _tentada(_marca(_paga_e_saca(c)[0]), formas, monkeypatch, "forma_inesperada")
    acoes = _linha(adm, paga)["actions"]
    assert acoes == {"conclude": None, "reverse": "Comissão já paga: reembolso confirmado vira recuperação."}
    d = _afiliado()
    no_saque = _comissao(d, 100)
    saque = request_payout(d.aff["id"])["id"]
    _tentada(_marca(no_saque), formas, monkeypatch)
    assert _linha(adm, no_saque)["actions"]["reverse"].startswith(f"A comissão está no saque {saque}, ainda aberto.")


# ── Estornar ─────────────────────────────────────────────────────────────────

def test_estornar_solta_sem_cobranca(adm, formas, monkeypatch):
    """Positivo: o caminho legítimo estorna, tira a marca e grava um evento completo."""
    c = _afiliado()
    com = _tentada(_marca(_comissao(c, 100)), formas, monkeypatch)
    _comissao(c, 80)
    for ruim in ({"justification": "curta"}, {"evidence": None}, {"evidence": ""}, {"evidence": "com espaço"},
                 {"evidence": "ref?x"}, {"evidence": "abc"}):
        r = _decide(adm, com, "reverse", **ruim)
        assert r.status_code == 422, (ruim, r.text)
    assert _decide(adm, com, "reverse", evidence="x" * 121).status_code == 422
    antes = get_affiliate_stats(c.aff["id"])
    assert _eventos(com) == []
    r = _decide(adm, com, "reverse")
    assert r.status_code == 200, r.text
    linha = _db(com)
    assert (linha["status"], linha["payment_check_pending"], linha["payment_check_tried_at"],
            linha["payment_check_reason"], linha["payment_check_marked_at"]) == ("reversed", False, None, None, None)
    assert com.id not in [x["id"] for x in manutencao.comissoes_a_conferir(1000)]
    depois = get_affiliate_stats(c.aff["id"])
    assert depois["held_cents"] == antes["held_cents"] - 10_000
    assert depois["available_cents"] == antes["available_cents"] == 8_000
    [e] = _eventos(com)
    assert (e["action"], e["actor"], e["justification"], e["receipt_ref"], e["affiliate_code"], e["review_id"],
            e["before_cents"], e["after_cents"]) == ("check_reversed", admin_dashboard.ADMIN_DASHBOARD_USERNAME, JUST,
                                                     EVID, c.aff["code"], None, 10_000, 0)
    assert e["state_before"] | {"marcada_em": None, "tentada_em": None} == {
        "status": "pending", "payout_id": None, "payout_status": None, "marcada": True, "motivo": "sem_cobranca",
        "marcada_em": None, "tentada_em": None, "valor_cents": 10_000, "em_revisao": False, "recuperacao_aberta": False}
    assert e["state_before"]["marcada_em"] and e["state_before"]["tentada_em"]
    assert e["state_after"] == {"status": "reversed", "payout_id": None, "payout_status": None, "marcada": False,
                                "motivo": None, "marcada_em": None, "tentada_em": None, "valor_cents": 10_000,
                                "em_revisao": False, "recuperacao_aberta": False}
    historico = next(h for h in adm.get(OVERVIEW).json()["refund_history"] if h["id"] == e["id"])
    assert (historico["code"], historico["commission_id"], historico["receipt_ref"]) == (c.aff["code"], com.id, EVID)
    assert _linha(adm, com) is None


@ORIGENS
def test_estornar_sem_tentativa_vale(adm, origem):
    """Estornar não espera a 1ª conferência (§3): a marca nova, de motivo nulo, estorna."""
    com = _em_conferencia(_afiliado(), origem)
    assert _decide(adm, com, "reverse").status_code == 200
    assert _db(com)["status"] == "reversed"


def test_estornar_no_saque_aberto_recusa_e_nada_muda(adm, formas, monkeypatch):
    """P2 (D11): o bloqueio e o valor do saque ficam, e nenhum evento. Depois de rejeitar, estorna (positivo)."""
    c = _afiliado()
    com, outra = _comissao(c, 100), _comissao(c, 70)
    saque = _saque_com_pix(c)
    _tentada(_marca(com), formas, monkeypatch)
    antes = _fotografia(c, com)
    r = _decide(adm, com, "reverse")
    assert r.status_code == 409 and r.json()["detail"].startswith(f"A comissão está no saque {saque}, ainda aberto."), r.text
    assert _fotografia(c, com) == antes
    assert [(s["status"], s["amount_cents"]) for s in antes["saques"]] == [("requested", 17_000)]
    assert _db(com)["payment_check_pending"] is True and _eventos(com) == []
    assert next(p for p in adm.get(OVERVIEW).json()["payouts"] if p["id"] == saque)["status"] == "requested"
    from db.affiliates import get_payout
    assert get_payout(saque)["checking"] is True
    formas.cobra(outra.fatura)
    assert adm.get(f"/admin/api/affiliates/payouts/{saque}/pix").status_code != 200
    assert reject_payout(saque)
    assert _db(com)["payment_check_pending"] is True and _db(com)["payout_id"] is None  # volta marcada
    assert _decide(adm, com, "reverse").status_code == 200
    assert request_payout(c.aff["id"])["amount_cents"] == 7_000  # o saque novo sai sem ela


def test_estornar_paga_ou_estornada_recusa(adm, formas, monkeypatch):
    c = _afiliado()
    paga = _tentada(_marca(_paga_e_saca(c)[0]), formas, monkeypatch)
    antes = _fotografia(c, paga)
    r = _decide(adm, paga, "reverse")
    assert r.status_code == 409 and r.json()["detail"] == "Comissão já paga: reembolso confirmado vira recuperação."
    assert _fotografia(c, paga) == antes and _eventos(paga) == []
    estornada = _marca(_comissao(c, 100))
    visto = _linha(adm, estornada)
    with get_conn() as conn:  # a estornada que ficou marcada antes deste PR
        conn.execute("update affiliate_commissions set status = 'reversed' where id = %s", (estornada.id,))
        conn.commit()
    assert _decide(adm, estornada, "reverse", visto).status_code == 409
    assert _eventos(estornada) == []


def test_estornar_fecha_a_pendencia_aberta(adm, fonte):
    c = _afiliado()
    com = _paga(c)
    fonte.cobra(com.fatura, devolvido=30_000)
    _parcial(com)
    assert _rev(com.fatura)["status"] == "open"
    _marca(com)
    assert _decide(adm, com, "reverse").status_code == 200
    assert _rev(com.fatura)["status"] == "resolved"
    with get_conn() as conn:
        acoes = [e["action"] for e in conn.execute(
            "select action from affiliate_refund_review_events where review_id = %s or commission_id = %s order by id",
            (_rev(com.fatura)["id"], com.id)).fetchall()]
    assert acoes == ["commission_reversed", "check_reversed"]


def test_rota_antiga_reverse_tira_a_marca(adm):
    com = _marca(_comissao(_afiliado(), 100))
    r = adm.post(f"/admin/api/affiliates/commissions/{com.id}/reverse", headers=csrf(adm))
    assert r.status_code == 200, r.text
    assert (_db(com)["status"], _db(com)["payment_check_pending"], _db(com)["payment_check_marked_at"]) == (
        "reversed", False, None)


# ── Concluir conferência (paga) ──────────────────────────────────────────────

def test_concluir_paga_nao_mexe_em_saldo_saque_nem_recuperacao(adm, formas, monkeypatch):
    """Positivo e P6: só a marca sai. Com uma recuperação aberta da mesma fatura, ela fica igual."""
    c = _afiliado()
    com, saque = _paga_e_saca(c)
    registrar_reembolso(com.fatura, "total", 100_000, AGORA)
    assert _rev(com.fatura)["status"] == "recovery"
    _tentada(_marca(com), formas, monkeypatch)
    antes = _fotografia(c, com)
    r = _decide(adm, com, "conclude")
    assert r.status_code == 200, r.text
    assert _fotografia(c, com) == antes
    linha = _db(com)
    assert (linha["status"], linha["payout_id"], linha["payment_check_pending"], linha["payment_check_reason"]) == (
        "paid", saque, False, None)
    [e] = _eventos(com)
    assert (e["action"], e["before_cents"], e["after_cents"], e["receipt_ref"]) == ("check_concluded", 10_000, 10_000, EVID)
    assert e["state_before"]["recuperacao_aberta"] is True and e["state_after"]["recuperacao_aberta"] is True
    assert (e["state_after"]["status"], e["state_after"]["marcada"]) == ("paid", False)


def test_concluir_recusa_pendente_disputa_e_sem_tentativa(adm, formas, monkeypatch):
    c = _afiliado()
    pendente = _tentada(_marca(_comissao(c, 100)), formas, monkeypatch)
    r = _decide(adm, pendente, "conclude")
    assert r.status_code == 409 and r.json()["detail"] == "Esta ação não vale para esta comissão."
    disputa = _tentada(_marca(_paga_e_saca(c)[0]), formas, monkeypatch, "disputa_aberta")
    assert _decide(adm, disputa, "conclude").status_code == 409
    nova = _marca(_paga_e_saca(_afiliado())[0])
    r = _decide(adm, nova, "conclude")
    assert r.status_code == 409 and r.json()["detail"].startswith("Aguarde a conferência"), r.text
    assert formas.consultas == 2  # só as duas `_tentada`: recusa por regra não consulta a Stripe
    for com in (pendente, disputa, nova):
        assert _db(com)["payment_check_pending"] is True and _eventos(com) == []


def test_concluir_confere_na_hora(adm, formas, monkeypatch):
    """A Stripe acha o reembolso no clique: a regra normal decide (recuperação), e nada manual é gravado."""
    c = _afiliado()
    com = _tentada(_marca(_paga_e_saca(c)[0]), formas, monkeypatch)
    formas.cobra(com.fatura, devolvido=100_000)
    r = _decide(adm, com, "conclude")
    assert r.status_code == 409 and r.json()["detail"].startswith("A Stripe respondeu agora"), r.text
    assert _rev(com.fatura)["status"] == "recovery" and _db(com)["payment_check_pending"] is False
    assert _eventos(com) == []


def test_concluir_com_a_stripe_fora_nao_grava(adm, formas, monkeypatch):
    import stripe

    c = _afiliado()
    com = _tentada(_marca(_paga_e_saca(c)[0]), formas, monkeypatch)
    antes = _db(com)
    formas.falha = stripe.APIConnectionError("fora")
    r = _decide(adm, com, "conclude")
    assert r.status_code == 503 and r.json()["detail"] == admin_dashboard._STRIPE_FORA
    assert _db(com) == antes and _eventos(com) == []


def test_concluir_com_prazo_estourado_nao_decide_depois(adm, formas, monkeypatch):
    """A thread segue depois do 503; a decisão manual nunca roda nela."""
    c = _afiliado()
    com = _tentada(_marca(_paga_e_saca(c)[0]), formas, monkeypatch)
    monkeypatch.setattr(admin_dashboard, "PRAZO_CONFERENCIA_S", 0.5)
    real = servico.devolvido_na_stripe

    def lenta(fatura, chave):
        time.sleep(2)
        return real(fatura, chave)

    monkeypatch.setattr(servico, "devolvido_na_stripe", lenta)
    r = _decide(adm, com, "conclude")
    assert r.status_code == 503, r.text
    fim = time.monotonic() + 5
    while admin_dashboard._VAGAS_CONFERENCIA._value < admin_dashboard.VAGAS_CONFERENCIA and time.monotonic() < fim:
        time.sleep(0.05)
    assert admin_dashboard._VAGAS_CONFERENCIA._value == admin_dashboard.VAGAS_CONFERENCIA  # a thread terminou
    assert _db(com)["payment_check_pending"] is True and _eventos(com) == []


# ── Repetição e concorrência ─────────────────────────────────────────────────

def _em_paralelo(*chamadas):
    barreira = threading.Barrier(len(chamadas))

    def roda(f):
        barreira.wait()
        return f()

    with ThreadPoolExecutor(len(chamadas)) as pool:
        return [x.status_code for x in pool.map(roda, chamadas)]


@ORIGENS
@pytest.mark.parametrize("modo", ["sequencial", "paralelo"])
def test_duplo_clique_estornar(adm, formas, monkeypatch, modo, origem):
    c = _afiliado()
    com = _tentada(_em_conferencia(c, origem), formas, monkeypatch)
    _comissao(c, 60)
    visto = _linha(adm, com)
    if modo == "sequencial":
        codigos = [_decide(adm, com, "reverse", visto).status_code for _ in range(2)]
    else:
        codigos = _em_paralelo(*[lambda: _decide(adm, com, "reverse", visto)] * 2)
    assert sorted(codigos) == [200, 409]
    assert [e["action"] for e in _eventos(com)] == ["check_reversed"]
    assert get_affiliate_stats(c.aff["id"])["available_cents"] == 6_000


@pytest.mark.parametrize("modo", ["sequencial", "paralelo", "dois_admins"])
def test_duplo_clique_concluir(adm, formas, monkeypatch, modo):
    c = _afiliado()
    com = _tentada(_marca(_paga_e_saca(c)[0]), formas, monkeypatch)
    antes = _fotografia(c, com)
    visto = _linha(adm, com)
    outro = _admin(monkeypatch) if modo == "dois_admins" else adm
    if modo == "sequencial":
        codigos = [_decide(adm, com, "conclude", visto).status_code for _ in range(2)]
    else:
        codigos = _em_paralelo(lambda: _decide(adm, com, "conclude", visto),
                               lambda: _decide(outro, com, "conclude", visto))
    assert sorted(codigos) == [200, 409]
    assert [e["action"] for e in _eventos(com)] == ["check_concluded"]
    assert _fotografia(c, com) == antes


def test_estornar_e_concluir_em_paralelo_na_paga(adm, formas, monkeypatch):
    c = _afiliado()
    com = _tentada(_marca(_paga_e_saca(c)[0]), formas, monkeypatch)
    visto = _linha(adm, com)
    outro = _admin(monkeypatch)
    codigos = _em_paralelo(lambda: _decide(adm, com, "reverse", visto), lambda: _decide(outro, com, "conclude", visto))
    assert codigos == [409, 200]
    assert [e["action"] for e in _eventos(com)] == ["check_concluded"] and _db(com)["status"] == "paid"


def test_worker_no_meio_nao_ressuscita_a_marca(adm, formas, monkeypatch):
    """21a: o worker leu a marca e está no HTTP quando o admin estorna; a escrita dele depois não grava nada."""
    c = _afiliado()
    com = _tentada(_marca(_comissao(c, 100)), formas, monkeypatch)
    manutencao.marcar_conferencia([com.id])  # nova marca: o worker a pega já
    visto = _linha(adm, com)
    no_http, segue = threading.Event(), threading.Event()
    real = servico.devolvido_na_stripe

    def devolvido(fatura, chave):
        if fatura == com.fatura:
            no_http.set()
            assert segue.wait(5)
        return real(fatura, chave)

    monkeypatch.setattr(servico, "devolvido_na_stripe", devolvido)
    with ThreadPoolExecutor(1) as pool:
        worker = pool.submit(servico.conferir_comissao, com.id, com.fatura, adiar=True, lido=_db(com)["payment_check_marked_at"])
        assert no_http.wait(5)
        assert _decide(adm, com, "reverse", visto).status_code == 200
        segue.set()
        assert worker.result() is False
    linha = _db(com)
    assert (linha["status"], linha["payment_check_pending"], linha["payment_check_tried_at"],
            linha["payment_check_reason"]) == ("reversed", False, None, None)
    assert len(_eventos(com)) == 1


def test_worker_acha_reembolso_durante_o_concluir(adm, formas, monkeypatch):
    """21b: worker e admin conferem a mesma paga, e a Stripe mostra o reembolso: a recuperação nasce uma vez."""
    c = _afiliado()
    com = _tentada(_marca(_paga_e_saca(c)[0]), formas, monkeypatch)
    visto = _linha(adm, com)
    formas.cobra(com.fatura, devolvido=100_000)
    lido = _db(com)["payment_check_marked_at"]
    barreira = threading.Barrier(2)

    def worker():
        barreira.wait()
        return servico.conferir_comissao(com.id, com.fatura, adiar=True, lido=lido)

    def admin():
        barreira.wait()
        return _decide(adm, com, "conclude", visto).status_code

    with ThreadPoolExecutor(2) as pool:
        w, a = pool.submit(worker), pool.submit(admin)
        assert a.result() == 409
        w.result()
    with get_conn() as conn:
        n = conn.execute("select count(*) as n from affiliate_refund_review_events where review_id = %s"
                         " and action = 'paid_before_refund'", (_rev(com.fatura)["id"],)).fetchone()["n"]
    assert n == 1 and _rev(com.fatura)["status"] == "recovery" and _eventos(com) == []


def test_webhook_entre_ver_e_decidir(adm, formas, monkeypatch):
    """22a: a marca nova do webhook depois da lista. 22b: entre a conferência na hora e a trava."""
    c = _afiliado()
    com = _tentada(_marca(_comissao(c, 100)), formas, monkeypatch)
    visto = _linha(adm, com)
    time.sleep(0.01)
    assert manutencao.marcar_conferencia_da_fatura(com.fatura) == com.id
    r = _decide(adm, com, "reverse", visto)
    assert r.status_code == 409 and r.json()["detail"] == "A comissão mudou: recarregue antes de decidir."
    assert _db(com)["status"] == "pending" and _eventos(com) == []

    paga = _tentada(_marca(_paga_e_saca(c)[0]), formas, monkeypatch)
    real = servico.conferir_comissao

    def conferir(commission_id, fatura, **k):
        feita = real(commission_id, fatura, **k)
        manutencao.marcar_conferencia_da_fatura(fatura)  # o webhook marca depois da consulta, antes da trava
        return feita

    monkeypatch.setattr(servico, "conferir_comissao", conferir)
    r = _decide(adm, paga, "conclude")
    assert r.status_code == 409 and r.json()["detail"] == "A comissão mudou: recarregue antes de decidir."
    assert _db(paga)["payment_check_pending"] is True and _eventos(paga) == []


def test_webhook_depois_da_decisao(adm, fonte, formas, monkeypatch):
    """22c e 22d: depois do commit, o webhook não desfaz nem duplica a decisão."""
    c = _afiliado()
    estornada = _tentada(_marca(_comissao(c, 100)), formas, monkeypatch)
    assert _decide(adm, estornada, "reverse").status_code == 200
    assert manutencao.marcar_conferencia_da_fatura(estornada.fatura) is None  # estornada não volta à fila
    formas.cobra(estornada.fatura, devolvido=100_000)
    assert _enviar("charge.refunded", _cobranca(estornada.fatura)).status_code == 200
    assert _rev(estornada.fatura) is None and len(_eventos(estornada)) == 1
    assert _db(estornada)["status"] == "reversed"

    paga = _tentada(_marca(_paga_e_saca(c)[0]), formas, monkeypatch)
    assert _decide(adm, paga, "conclude").status_code == 200
    assert manutencao.marcar_conferencia_da_fatura(paga.fatura) == paga.id  # remarcada: volta ao worker
    assert (_db(paga)["status"], _db(paga)["payment_check_pending"]) == ("paid", True)
    assert len(_eventos(paga)) == 1


def test_reconciliacao_depois_de_concluir(adm, formas, monkeypatch):
    """23: concluída, a paga segue na reconciliação: o reembolso achado depois vira recuperação uma vez."""
    with get_conn() as conn:
        conn.execute("insert into affiliate_paid_refund_checks select c.stripe_invoice_id, now()"
                     " from affiliate_commissions c where c.status = 'paid' on conflict do nothing")
        conn.commit()
    c = _afiliado()
    com = _tentada(_marca(_paga_e_saca(c)[0]), formas, monkeypatch)
    assert _decide(adm, com, "conclude").status_code == 200
    formas.cobra(com.fatura, devolvido=100_000)
    for _ in range(2):
        reconciliacao.reconciliar_pagas(1000)
        with get_conn() as conn:
            conn.execute("delete from affiliate_paid_refund_checks where stripe_invoice_id = %s", (com.fatura,))
            conn.commit()
    assert _rev(com.fatura)["status"] == "recovery" and _rev(com.fatura)["recovery_cents"] == 10_000
    with get_conn() as conn:
        n = conn.execute("select count(*) as n from affiliate_refund_review_events where review_id = %s",
                         (_rev(com.fatura)["id"],)).fetchone()["n"]
    assert n == 1


def test_estornar_e_rejeitar_saque_em_paralelo(adm, formas, monkeypatch):
    """24: nunca estornada E no saque."""
    for _ in range(3):
        c = _afiliado()
        com, _outra = _comissao(c, 100), _comissao(c, 70)
        saque = request_payout(c.aff["id"])["id"]
        _tentada(_marca(com), formas, monkeypatch)
        visto = _linha(adm, com)
        barreira = threading.Barrier(2)

        def rejeita():
            barreira.wait()
            return reject_payout(saque)

        def estorna():
            barreira.wait()
            return _decide(adm, com, "reverse", visto).status_code

        with ThreadPoolExecutor(2) as pool:
            r, e = pool.submit(rejeita), pool.submit(estorna)
            assert r.result() is True
            codigo = e.result()
        linha = _db(com)
        assert (linha["status"], linha["payout_id"]) in (("reversed", None), ("pending", None))
        assert (codigo == 200) == (linha["status"] == "reversed") and len(_eventos(com)) == (codigo == 200)


# ── Corpo, privacidade e logs ────────────────────────────────────────────────

@pytest.mark.parametrize("campo,valor,esperado", [
    ("marked_at", "nao-e-data", 422), ("marked_at", 123, 422), ("marked_at", "2026-01-01\x00", 422),
    ("marked_at", "\ud800", 422), ("reason", ["x"], 422), ("reason", 1.5, 422), ("evidence", "ab\x00cd", 422),
    ("evidence", "\ud800abcd", 422), ("reason", "sem\x00cobranca", 409), ("reason", "\ud800", 409),
    ("amount_cents", None, 422), ("amount_cents", True, 422), ("amount_cents", "10000", 422),
    ("amount_cents", 10000.0, 422), ("amount_cents", 7000, 409),
], ids=["data_ruim", "data_numero", "data_nul", "data_surrogate", "motivo_lista", "motivo_numero", "evid_nul",
        "evid_surrogate", "motivo_nul", "motivo_surrogate", "valor_null", "valor_bool", "valor_texto", "valor_float",
        "valor_outro"])
def test_corpo_venenoso_nunca_da_500(adm, formas, monkeypatch, campo, valor, esperado):
    """O motivo só é comparado em Python: com NUL ou surrogate, não bate com o visto (409), e nunca chega ao SQL."""
    c = _afiliado()
    com = _tentada(_marca(_comissao(c, 100)), formas, monkeypatch)
    visto = _linha(adm, com)
    corpo = {"justification": JUST, "evidence": EVID, "marked_at": visto["marked_at"], "reason": visto["reason"],
             "amount_cents": visto["amount_cents"], campo: valor}
    texto = json.dumps(corpo)  # o `json=` do cliente não codifica surrogate solitário
    for acao in ("reverse", "conclude"):
        r = adm.post(f"/admin/api/affiliates/commissions/{com.id}/check-{acao}",
                     headers={**csrf(adm), "content-type": "application/json"}, content=texto.encode())
        assert r.status_code == esperado, (acao, r.text)
    assert _db(com)["status"] == "pending" and _eventos(com) == []


def test_logs_sem_justificativa_evidencia_nem_motivo(adm, formas, monkeypatch):
    c = _afiliado()
    com = _tentada(_marca(_comissao(c, 100)), formas, monkeypatch)
    paga = _tentada(_marca(_paga_e_saca(c)[0]), formas, monkeypatch, "forma_inesperada")
    assert _decide(adm, com, "reverse").status_code == 200
    assert _decide(adm, paga, "conclude").status_code == 200
    with get_conn() as conn:
        logs = conn.execute("select event_type, message, details::text as d from system_event_logs"
                            " where event_type in ('affiliate_commission_check_reversed',"
                            " 'affiliate_commission_check_concluded') and (details->>'commission_id')::bigint = any(%s)",
                            ([com.id, paga.id],)).fetchall()
    assert len(logs) == 2
    texto = " ".join(x["message"] + x["d"] for x in logs)
    assert [p for p in (JUST, EVID, "sem_cobranca", "forma_inesperada", com.fatura, paga.fatura) if p in texto] == []


def test_overview_nao_leva_a_fatura_nem_o_indicado(adm, formas, monkeypatch):
    c = _afiliado()
    com = _tentada(_marca(_comissao(c, 100)), formas, monkeypatch)
    assert _decide(adm, com, "reverse").status_code == 200
    outra = _tentada(_marca(_comissao(c, 50)), formas, monkeypatch)
    texto = adm.get(OVERVIEW).text
    assert _linha(adm, outra) is not None
    assert [p for p in (com.indicado.email, outra.indicado.email, com.fatura, outra.fatura, "stripe_invoice_id",
                        '"fatura"', "referred_user_id") if p in texto] == []


# ── Migração ─────────────────────────────────────────────────────────────────

_NOVAS = ("commission_id", "affiliate_code", "state_before", "state_after")
_ACOES_MAIN = ("'partial_approved', 'payout_rejected', 'payment_recorded', 'paid_before_refund', 'auto_reversed',"
               " 'commission_reversed', 'recovery_recovered', 'recovery_waived', 'reopened', 'affiliate_deleted'")


def _tabela_da_main():
    """A tabela como a `main` a deixa: sem as quatro colunas, com os dois `check` antigos (um sem nome).
    `not valid`: eventos de outros testes deste worker violariam o `check` antigo."""
    with get_conn() as conn:
        conn.execute("alter table affiliate_refund_review_events " + ", ".join(f"drop column {c}" for c in _NOVAS))
        conn.execute("alter table affiliate_refund_review_events drop constraint affiliate_refund_review_events_action_check")
        conn.execute(f"alter table affiliate_refund_review_events add check (action in ({_ACOES_MAIN})) not valid")
        conn.execute("alter table affiliate_refund_review_events add check (review_id is not null or payout_id is not null)"
                     " not valid")
        conn.commit()


def _checks():
    with get_conn() as conn:
        return sorted(x["conname"] for x in conn.execute(
            "select conname from pg_constraint where conrelid = 'affiliate_refund_review_events'::regclass"
            " and contype = 'c'").fetchall())


def test_init_db_migra_a_tabela_da_main(adm, formas, monkeypatch):
    c = _afiliado()
    com = _comissao(c, 100)
    _tabela_da_main()
    try:
        assert len(_checks()) == 2
        db.init_db()
        with get_conn() as conn:
            colunas = conn.execute("select column_name, data_type from information_schema.columns"
                                   " where table_name = 'affiliate_refund_review_events' and column_name = any(%s)"
                                   " order by column_name", (list(_NOVAS),)).fetchall()
            indice = conn.execute("select 1 from pg_indexes where indexname = 'idx_affiliate_refund_review_events_commission'"
                                  ).fetchone()
        assert [tuple(x.values()) for x in colunas] == [("affiliate_code", "text"), ("commission_id", "bigint"),
                                                       ("state_after", "jsonb"), ("state_before", "jsonb")]
        assert indice and _checks() == ["affiliate_refund_review_events_action_check",
                                        "affiliate_refund_review_events_ancora_check"]
        with get_conn() as conn:  # o evento antigo segue inserível; sem âncora, não
            conn.execute("insert into affiliate_refund_review_events(action, actor, payout_id) values"
                         " ('partial_approved', 'teste', 1)")
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute("insert into affiliate_refund_review_events(action, actor) values ('check_reversed', 'x')")
            conn.rollback()
        db.init_db()  # a segunda não faz nada
        assert len(_checks()) == 2
        _tentada(_marca(com), formas, monkeypatch)
        assert _decide(adm, com, "reverse").status_code == 200 and len(_eventos(com)) == 1
    finally:
        db.init_db()


def test_migracao_com_a_tabela_ocupada():
    """Tabela ocupada derruba o boot com erro claro, sem entrar na fila de locks, e nada fica gravado."""
    bloco = next(b for b in re.findall(r'"""(\s*do \$\$ declare r record; begin.*?end \$\$\s*)"""',
                                       Path(schema.__file__).read_text(), re.S)
                 if "lock table affiliate_refund_review_events" in b)
    _tabela_da_main()
    try:
        with get_conn() as leitor, get_conn() as boot:
            leitor.execute("select 1 from affiliate_refund_review_events limit 1")
            boot.execute("set lock_timeout = '1s'")  # sem o `nowait`, cada tentativa esperaria até aqui
            t0 = time.monotonic()
            with pytest.raises(psycopg.errors.LockNotAvailable, match="o boot para aqui"):
                boot.execute(bloco.replace("1..160 loop", "1..3 loop").replace("tentativa = 160", "tentativa = 3"))
            assert time.monotonic() - t0 < 1, "a migração entrou na fila de locks"
            boot.rollback()
            leitor.rollback()
        with get_conn() as conn:
            ficou = conn.execute("select 1 from pg_attribute where attrelid = 'affiliate_refund_review_events'::regclass"
                                 " and attname = 'commission_id' and not attisdropped").fetchone()
        assert ficou is None and len(_checks()) == 2
    finally:
        db.init_db()


# ── Rodada 2 do Tester: a versão vista, a nascida marcada e os outros estornos ──

def test_remarcada_e_retentada_com_o_mesmo_motivo(adm, formas, monkeypatch):
    """M1: o webhook remarca e o worker grava o MESMO motivo. Só o `marked_at` separa as duas versões."""
    c = _afiliado()
    com = _tentada(_marca(_comissao(c, 100)), formas, monkeypatch)
    visto = _linha(adm, com)
    time.sleep(0.01)
    assert manutencao.marcar_conferencia_da_fatura(com.fatura) == com.id
    _tentada(com, formas, monkeypatch)
    agora = _linha(adm, com)
    assert agora["reason"] == visto["reason"] and agora["marked_at"] != visto["marked_at"]
    r = _decide(adm, com, "reverse", visto)
    assert r.status_code == 409 and r.json()["detail"] == "A comissão mudou: recarregue antes de decidir."
    assert _db(com)["status"] == "pending" and _eventos(com) == []
    assert _decide(adm, com, "reverse", agora).status_code == 200  # positivo: a versão nova decide


def test_mesma_marca_com_motivo_novo(adm, formas, monkeypatch):
    """M1, o par: a mesma marca, e o worker passa a ver outro motivo (aqui, disputa). O admin decidiu pelo antigo."""
    c = _afiliado()
    com = _tentada(_marca(_comissao(c, 100)), formas, monkeypatch)
    visto = _linha(adm, com)
    _tentada(com, formas, monkeypatch, "disputa_aberta")
    agora = _linha(adm, com)
    assert (agora["marked_at"], agora["reason"]) == (visto["marked_at"], "disputa_aberta")
    assert _decide(adm, com, "reverse", visto).status_code == 409
    assert _db(com)["status"] == "pending" and _eventos(com) == []


def test_nascida_marcada_liberada_pela_stripe_nao_e_estornada(adm):
    """M2: a versão (None, None) é a mesma antes e depois de a Stripe liberar; só "continua marcada" segura."""
    c = _afiliado()
    com = _nascida(c)
    visto = _linha(adm, com)
    assert (visto["marked_at"], visto["reason"]) == (None, None)
    assert manutencao.liberar_conferencia(com.id, None)  # o worker: a Stripe confirmou, comissão legítima
    antes = get_affiliate_stats(c.aff["id"])
    r = _decide(adm, com, "reverse", visto)
    assert r.status_code == 409 and r.json()["detail"] == "A comissão mudou: recarregue antes de decidir."
    assert _db(com)["status"] == "pending" and get_affiliate_stats(c.aff["id"]) == antes and _eventos(com) == []


def test_estorno_automatico_d9_tira_a_marca(adm, formas, monkeypatch):
    """B1: o estorno da solta pelo reembolso total (D9) sai da conferência como os outros estornos."""
    c = _afiliado()
    com = _tentada(_marca(_comissao(c, 100)), formas, monkeypatch)
    registrar_reembolso(com.fatura, "total", 100_000, datetime.now(timezone.utc))
    linha = _db(com)
    assert (linha["status"], linha["payment_check_pending"], linha["payment_check_reason"]) == ("reversed", False, None)
    assert com.id not in [x["id"] for x in manutencao.comissoes_a_conferir(1000)]
    assert _linha(adm, com) is None


def test_aprovar_zero_tira_a_marca(adm, fonte):
    """B1: a revisão parcial aprovada com 0 equivale a estorno, e também sai da conferência."""
    c = _afiliado()
    com = _comissao(c, 100)
    fonte.cobra(com.fatura, devolvido=30_000)
    _parcial(com)
    rev = _rev(com.fatura)
    _marca(com)
    r = adm.post(f"/admin/api/affiliates/refund-reviews/{rev['id']}/approve",
                 headers={**csrf(adm), "content-type": "application/json"},
                 content=json.dumps({"approved_cents": 0, "refunded_cents": rev["refunded_cents"],
                                     "justification": JUST}).encode())
    assert r.status_code == 200, r.text
    linha = _db(com)
    assert (linha["status"], linha["payment_check_pending"], linha["payment_check_marked_at"]) == ("reversed", False, None)
    assert com.id not in [x["id"] for x in manutencao.comissoes_a_conferir(1000)]


def test_estado_anterior_relido_depois_da_trava(adm, formas, monkeypatch):
    """B2: a pendência nasce enquanto o Estornar espera a trava da comissão. O estado anterior gravado é o que a
    decisão encontrou depois da trava (em revisão), e o mesmo estorno fecha a pendência."""
    c = _afiliado()
    com = _tentada(_marca(_comissao(c, 100)), formas, monkeypatch)
    visto = _linha(adm, com)
    assert visto["in_review"] is False
    with get_conn() as conn, ThreadPoolExecutor(1) as pool:
        conn.execute("select id from affiliate_commissions where id = %s for update", (com.id,))
        decisao = pool.submit(_decide, adm, com, "reverse", visto)
        assert _esperando_lock(1)
        # o que `registrar_reembolso` grava num parcial, sob a mesma trava
        conn.execute("insert into affiliate_refund_reviews(stripe_invoice_id, commission_id, tipo, refunded_cents,"
                     " amount_cents, status) values (%s, %s, 'partial', 30000, 10000, 'open')", (com.fatura, com.id))
        conn.commit()
        r = decisao.result()
    assert r.status_code == 200, r.text
    [e] = _eventos(com)
    assert e["state_before"]["em_revisao"] is True and e["state_after"]["em_revisao"] is False
    assert (_rev(com.fatura)["status"], _db(com)["status"]) == ("resolved", "reversed")


def _aprovar(adm, rev, cents):
    return adm.post(f"/admin/api/affiliates/refund-reviews/{rev['id']}/approve",
                    headers={**csrf(adm), "content-type": "application/json"},
                    content=json.dumps({"approved_cents": cents, "refunded_cents": rev["refunded_cents"],
                                        "justification": JUST}).encode())


def _parcial_marcada(c, fonte):
    com = _comissao(c, 100)
    fonte.cobra(com.fatura, devolvido=30_000)
    _parcial(com)
    return _marca(com), _rev(com.fatura)


def test_valor_mudou_entre_ver_e_estornar(adm, fonte):
    """B3: a revisão parcial baixa o valor depois da lista. O estorno vale para o valor que o admin viu."""
    c = _afiliado()
    com, rev = _parcial_marcada(c, fonte)
    visto = _linha(adm, com)
    assert visto["amount_cents"] == 10_000
    assert _aprovar(adm, rev, 7_000).status_code == 200
    r = _decide(adm, com, "reverse", visto)
    assert r.status_code == 409 and r.json()["detail"] == "A comissão mudou: recarregue antes de decidir."
    assert (_db(com)["status"], _db(com)["amount_cents"]) == ("pending", 7_000) and _eventos(com) == []
    assert _decide(adm, com, "reverse").status_code == 200  # positivo: com a lista de agora
    assert _eventos(com)[0]["before_cents"] == 7_000


def test_estornar_e_aprovar_parcial_em_paralelo(adm, fonte):
    """B3 em paralelo: uma das duas decisões vale, e o estorno, quando vence, é sobre o valor visto."""
    for _ in range(3):
        c = _afiliado()
        com, rev = _parcial_marcada(c, fonte)
        visto = _linha(adm, com)
        codigos = _em_paralelo(lambda: _decide(adm, com, "reverse", visto), lambda: _aprovar(adm, rev, 7_000))
        assert sorted(codigos) == [200, 409], codigos
        eventos = _eventos(com)
        if codigos[0] == 200:
            assert [(e["before_cents"], e["state_before"]["valor_cents"]) for e in eventos] == [(10_000, 10_000)]
        else:
            assert eventos == [] and (_db(com)["status"], _db(com)["amount_cents"]) == ("pending", 7_000)


def test_cabecalho_e_lista_contam_desde_a_marca(adm):
    """B6: comissão antiga marcada agora. "A mais antiga" do cabeçalho e a 1ª da lista são a mesma data."""
    with get_conn() as conn:
        conn.execute("update affiliate_commissions set payment_check_pending = false where payment_check_pending")
        conn.commit()
    c = _afiliado()
    antiga = _comissao(c, 100)
    with get_conn() as conn:
        conn.execute("update affiliate_commissions set created_at = now() - interval '30 days' where id = %s",
                     (antiga.id,))
        conn.commit()
    time.sleep(0.01)
    _marca(antiga)
    _marca(_comissao(c, 50))
    visao = adm.get(OVERVIEW).json()
    lista, resumo = visao["commission_check_list"], visao["commission_checks"]
    assert [x["id"] for x in lista][0] == antiga.id and resumo["count"] == len(lista) == 2
    assert resumo["oldest"] == lista[0]["marked_on"] == lista[0]["marked_at"]


def _saque_rejeitado_com_reembolso_total(c, fonte):
    """Saque com duas comissões marcadas (D10, ou "Pagamento já realizado" com a Stripe fora). A 1ª tem reembolso
    total: o webhook a deixa em revisão, e o "Rejeitar" que o 409 do Estornar recomenda a estorna."""
    reembolsada, outra = _paga(c), _paga(c, 70)
    saque = request_payout(c.aff["id"])["id"]
    manutencao.marcar_conferencia([reembolsada.id, outra.id])
    marca_da_outra = _db(outra)["payment_check_marked_at"]
    fonte.cobra(reembolsada.fatura, devolvido=100_000)
    _total(reembolsada)
    assert reject_payout(saque)
    return reembolsada, outra, marca_da_outra


def test_rejeitar_saque_com_reembolso_total_tira_a_marca(adm, fonte):
    """B1, o 4º estorno: `reject_payout` estorna a de reembolso total e ela sai da conferência."""
    reembolsada, _, _ = _saque_rejeitado_com_reembolso_total(_afiliado(), fonte)
    linha = _db(reembolsada)
    assert (linha["status"], linha["payout_id"], linha["payment_check_pending"], linha["payment_check_marked_at"]) == (
        "reversed", None, False, None)
    assert reembolsada.id not in [x["id"] for x in manutencao.comissoes_a_conferir(1000)]
    assert _linha(adm, reembolsada) is None


def test_rejeitar_saque_devolve_a_marcada_com_a_marca(adm, fonte):
    """Positivo: a do mesmo saque que só volta ao saldo segue em conferência, com a mesma marca."""
    _, outra, marca = _saque_rejeitado_com_reembolso_total(_afiliado(), fonte)
    linha = _db(outra)
    assert (linha["status"], linha["payout_id"], linha["payment_check_pending"], linha["payment_check_marked_at"]) == (
        "pending", None, True, marca)
    assert _linha(adm, outra)["actions"] == {"reverse": None}


@pytest.mark.parametrize("texto", [
    "\u200b" * 20, "\x01" * 20, "\u2060\ufeff" * 10, "\u200b" * 5 + "abc", "\u200b \u2060 \ufeff\t\u200b" * 4 + "abc",
    "\u3164" * 10, "\u2800" * 20, "\u0301" * 20, "\ufffc" * 10, "\U00013441" * 10,
    "\u115f" * 10, "\uffa0" * 10, "\U0001d159" * 10, "\ufffd" * 10,
])
def test_justificativa_so_invisivel_recusada(texto):
    with pytest.raises(ValueError):
        servico.validar_justificativa(texto)


@pytest.mark.parametrize("texto", [
    "Reembolso confirmado após análise", "Ação revisada 👍🏽 ok", "abcdefgh 👨\u200d👩\u200d👧 ij",
    "e\u0301" * 10,
])
def test_justificativa_visivel_aceita(texto):
    assert servico.validar_justificativa(texto) == texto
