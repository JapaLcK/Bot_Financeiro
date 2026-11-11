"""Comissão do Pix anual (1/12 do pago, uma vez por cliente) e o estorno do Pix pela máquina do cartão.

Postgres real e o dreno real (`entregar` grava na outbox e drena; `mundo_externo` troca só GA4,
Meta, e-mail, alerta e o `GET` do Asaas). A regra da primeira cobrança é a mesma do cartão
(`tests/test_afiliado_comissao_primeira.py`); aqui está o que é do Pix.

Controles negativos (rodados à mão, §3 do `CLAUDE.md`; o que fica vermelho está no relato do PR):
  P1  `MESES_DO_PIX` → 1 no efeito                 → 19900.
  P3  base = `price_cents`                         → campanha e crédito somem da base.
  P4  marcador só quando gera                       → o cartão depois do Pix a 0% gera.
  P5/P6/P6b sem os passos 5 e 6 (no cartão, também sem a pré-checagem do webhook) → a segunda origem
      também gera (P6b: mesmo com a comissão do Pix estornada).
  P7  sem `trava_indicado`                          → 2 comissões.
  P9  exceção da comissão engolida no efeito        → nenhuma comissão, evento fechado.
  P10/P11/P15 `marcar` volta a só rotular           → a comissão segue `pending`, sem pendência.
  P12 comissão também na cobrança `refunded`        → nasce comissão do estorno.
  P13 junções e backfill só com `source='stripe'`   → rótulo some, saque não estorna, pagamento falso.
  P10a total = `amount_cents` (sem os cadernos)     → `refunded_cents` sem os cadernos.
  P10b sem `avisar_pendencia` no `marcar`           → nenhum aviso ao admin.
  P17 parcial deduplicado como os outros efeitos    → o 2º parcial é ignorado e a pendência não reabre.
  P18 sem a checagem da comissão no parcial         → sem afiliado, o evento fica preso no Asaas.
  P19 soma sem filtro de `status` ou sem recusar negativo → cancelado conta; negativo vira número.

Fora do escopo, de propósito: estorno total antes do `RECEIVED` (fora de ordem) seguido do cartão
gera a comissão do cartão. O cliente nunca ficou com o Pix; comportamento anterior a este PR.
"""
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest

import db
import db.affiliates as afiliados
from _dreno_pix_helpers import efeitos, entregar, evento, ler, mundo_externo, nova_cobranca
from core.services.pix_drain import drenar_evento
from core.services.pix_pricing import DURACAO_DIAS, MESES_DO_PIX
from db.affiliate_payments import PAGAMENTO_DA_COMISSAO_SQL, origem_da_chave, registrar_pagamento
from db.affiliates import (
    SaqueComPendencia,
    create_affiliate,
    mark_payout_paid,
    record_commission_for_invoice,
    record_commission_for_pix,
    reject_payout,
    request_payout,
    set_affiliate_status,
)
from db.connection import get_conn
from db.schema import BACKFILL_AFILIADO_PAGAMENTOS_SQL
from _billing_grants_helpers import conta
from test_afiliado_area import _conta, _me, _painel_aberto, _vence  # noqa: F401
from test_afiliado_comissao_primeira import _afiliado, _comissoes, _esperando, _logs, _marca  # noqa: F401
from test_afiliado_pagantes import _indicado, cobrar  # noqa: F401

AGORA = datetime(2026, 11, 9, tzinfo=timezone.utc)
_CADERNOS = [{"price": "price_cad_a", "url": "https://drive.test/a", "nome": "Caderno A", "valor_cents": 1990}]


@pytest.fixture
def externo(monkeypatch):
    return mundo_externo(monkeypatch)


def _pix(ind, **kw):
    conta(ind.uid, "free", None)
    return nova_cobranca(ind.uid, **kw)


def _anual(ind, preco=19900, credito=0):
    return _pix(ind, plan="pro", plan_stored="pro", price_cents=preco, credit_cents=credito,
                amount_cents=preco - credito)


def _revisao(chave):
    with get_conn() as conn:
        return conn.execute("select * from affiliate_refund_reviews where stripe_invoice_id = %s", (chave,)).fetchone()


def _acoes(chave):
    with get_conn() as conn:
        return [r["action"] for r in conn.execute(
            "select e.action from affiliate_refund_review_events e join affiliate_refund_reviews v on v.id = e.review_id"
            " where v.stripe_invoice_id = %s order by e.id", (chave,)).fetchall()]


def _linha(uid):
    (linha,) = _comissoes_completas(uid)
    return linha


def _comissoes_completas(uid):
    with get_conn() as conn:
        return conn.execute("select * from affiliate_commissions where referred_user_id = %s order by id",
                            (uid,)).fetchall()


# ── P0, P14: constantes e fonte única ────────────────────────────────────────

def test_p0_o_pix_vende_o_anual_e_divide_por_12():
    assert (DURACAO_DIAS, MESES_DO_PIX) == (365, 12)


@pytest.mark.parametrize("chave", ["in_1Abc", "pix:7", "pix:", "pixel", "in_pix:7"])
def test_p14_origem_da_chave_e_o_sql_dao_o_mesmo_pagamento(chave):
    with get_conn() as conn:
        sql = PAGAMENTO_DA_COMISSAO_SQL.format(c="c", p="p")
        achou = conn.execute(f"select {sql} as casa from (select %s::text as stripe_invoice_id) c,"
                             " (select %s::text as source, %s::text as payment_ref) p",
                             (chave, *origem_da_chave(chave))).fetchone()["casa"]
    assert achou is True


# ── P1–P3: valor, base e divisor ─────────────────────────────────────────────

def test_p1_pix_anual_a_100_por_cento(externo):
    ind = _indicado(_afiliado())
    cobranca = _anual(ind)
    entregar("PAYMENT_RECEIVED", cobranca)
    linha = _linha(ind.uid)
    assert (linha["stripe_invoice_id"], linha["amount_cents"], linha["invoice_amount_cents"], linha["paid_months"],
            linha["commission_bps"], linha["payment_check_pending"]) == (
        f"pix:{cobranca['id']}", 1658, 19900, 12, 10_000, False)
    assert linha["stripe_invoice_id"] == cobranca["external_reference"] and _marca(ind.uid) == linha["stripe_invoice_id"]


def test_p2_pix_a_33_por_cento(externo):
    c = _conta()
    c.aff = create_affiliate(c.uid, commission_bps=3300)
    ind = _indicado(c)
    entregar("PAYMENT_RECEIVED", _anual(ind, preco=19990))
    assert _linha(ind.uid)["amount_cents"] == 550   # 549,725 → 550 (o `//` dava 549)


@pytest.mark.parametrize("preco,credito,base,esperado", [
    (11940, 0, 11940, 995),      # campanha de 40%: o preço já vem com desconto
    (19900, 5000, 14900, 1242),  # crédito de upgrade: amount = price − credit
], ids=["campanha_40", "credito_de_upgrade"])
def test_p3_base_e_o_valor_pago(externo, preco, credito, base, esperado):
    ind = _indicado(_afiliado())
    entregar("PAYMENT_RECEIVED", _anual(ind, preco=preco, credito=credito))
    linha = _linha(ind.uid)
    assert (linha["invoice_amount_cents"], linha["amount_cents"]) == (base, esperado)


def test_p3_cadernos_ficam_fora_da_base(externo):
    ind = _indicado(_afiliado())
    conta(ind.uid, "free", None)
    cobranca = nova_cobranca(ind.uid, plan="pro", plan_stored="pro", price_cents=19900, credit_cents=0,
                             amount_cents=19900, extras=_CADERNOS)
    entregar("PAYMENT_RECEIVED", cobranca)
    assert (_linha(ind.uid)["invoice_amount_cents"], _linha(ind.uid)["amount_cents"]) == (19900, 1658)


# ── P4–P8: elegibilidade entre Pix e cartão, e reprocessamento ──────────────

def test_p4_zero_no_pix_encerra_e_o_cartao_depois_nao_gera(externo, cobrar, monkeypatch):
    c = _conta()
    c.aff = create_affiliate(c.uid, commission_bps=0)
    ind = _indicado(c)
    cobranca = _anual(ind)
    entregar("PAYMENT_RECEIVED", cobranca)
    assert (_comissoes(ind.uid), _marca(ind.uid)) == ([], cobranca["external_reference"])
    afiliados.set_affiliate_commission(c.aff["id"], 10_000)
    cobrar(ind.uid, valor=4990)
    assert _comissoes(ind.uid) == []


def test_p5_pix_depois_do_cartao_nao_gera(externo, cobrar):
    ind = _indicado(_afiliado())
    fatura = cobrar(ind.uid, valor=4990)
    entregar("PAYMENT_RECEIVED", _anual(ind))
    assert [x["stripe_invoice_id"] for x in _comissoes(ind.uid)] == [fatura]


def test_p6_cartao_depois_do_pix_nao_gera(externo, cobrar):
    ind = _indicado(_afiliado())
    cobranca = _anual(ind)
    entregar("PAYMENT_RECEIVED", cobranca)
    cobrar(ind.uid, valor=4990)
    assert [(x["stripe_invoice_id"], x["amount_cents"]) for x in _comissoes(ind.uid)] == [
        (cobranca["external_reference"], 1658)]


def test_p6_positivo_desativado_no_pix_tambem_encerra(externo, cobrar):
    c = _afiliado()
    ind = _indicado(c)
    assert set_affiliate_status(c.aff["id"], "disabled")
    cobranca = _anual(ind)
    entregar("PAYMENT_RECEIVED", cobranca)
    assert set_affiliate_status(c.aff["id"], "active")
    cobrar(ind.uid, valor=4990)
    assert (_comissoes(ind.uid), _marca(ind.uid)) == ([], cobranca["external_reference"])


def test_p6b_pix_com_comissao_estornada_e_o_cartao_depois_nao_gera(externo, cobrar):
    ind = _indicado(_afiliado())
    cobranca = _anual(ind)
    entregar("PAYMENT_RECEIVED", cobranca)
    entregar("PAYMENT_REFUNDED", cobranca)
    cobrar(ind.uid, valor=4990)
    assert [(x["stripe_invoice_id"], x["amount_cents"], x["status"]) for x in _comissoes_completas(ind.uid)] == [
        (cobranca["external_reference"], 1658, "reversed")]


def test_p7_pix_e_cartao_do_mesmo_cliente_em_paralelo_geram_exatamente_uma():
    """Pix e fatura pagos no mesmo segundo, cada um com o pagamento já gravado (como o dreno e o webhook)."""
    c = _afiliado()
    contagens = []
    for _ in range(40):
        ind = _indicado(c)
        pix = {"user_id": ind.uid, "external_reference": f"pix:{uuid.uuid4().int % 10**9}", "amount_cents": 19900,
               "paid_at": AGORA}
        fatura = f"in_{uuid.uuid4().hex[:12]}"
        assert registrar_pagamento(ind.uid, "pix", origem_da_chave(pix["external_reference"])[1], 19900, AGORA)
        assert registrar_pagamento(ind.uid, "stripe", fatura, 4990, AGORA)
        largada = threading.Barrier(2, timeout=10)

        def no_pix():
            largada.wait()
            return record_commission_for_pix(pix, MESES_DO_PIX)

        def no_cartao():
            largada.wait()
            return record_commission_for_invoice(ind.uid, fatura, 4990, AGORA)

        with ThreadPoolExecutor(2) as pool:
            [f.result(timeout=15) for f in (pool.submit(no_pix), pool.submit(no_cartao))]
        contagens.append(len(_comissoes(ind.uid)))
    assert set(contagens) == {1}, contagens


def test_p8_reprocessamento_do_pix_gera_uma(externo):
    ind = _indicado(_afiliado())
    cobranca = _anual(ind)
    entregar("PAYMENT_CONFIRMED", cobranca)
    entregar("PAYMENT_RECEIVED", cobranca)
    entregar("PAYMENT_RECEIVED", cobranca)
    with get_conn() as conn:  # o efeito roda de novo: o `unique` e o marcador seguram
        conn.execute("delete from pix_payment_effects where asaas_payment_id = %s and effect = 'afiliado'",
                     (cobranca["asaas_payment_id"],))
        conn.commit()
    entregar("PAYMENT_RECEIVED", cobranca)
    assert len(_comissoes(ind.uid)) == 1 and "afiliado" in efeitos(cobranca["asaas_payment_id"])


def test_p9_falha_da_comissao_vira_retentativa(externo, monkeypatch):
    real = afiliados.record_commission_for_pix
    chamadas = []

    def falha_uma_vez(*a, **k):
        chamadas.append(1)
        if len(chamadas) == 1:
            raise RuntimeError("banco fora")
        return real(*a, **k)

    monkeypatch.setattr(afiliados, "record_commission_for_pix", falha_uma_vez)
    ind = _indicado(_afiliado())
    cobranca = _anual(ind)
    evt = entregar("PAYMENT_RECEIVED", cobranca)
    assert (_comissoes(ind.uid), evento(evt)["attempts"], evento(evt)["processed_at"]) == ([], 1, None)
    assert "afiliado" not in efeitos(cobranca["asaas_payment_id"])
    drenar_evento(evt)
    assert [x["amount_cents"] for x in _comissoes(ind.uid)] == [1658]
    assert evento(evt)["processed_at"] is not None


# ── P10–P12, P15: estorno ────────────────────────────────────────────────────

def _cara(c, preco=99900):
    """Indicado com Pix pago acima do mínimo do saque: 99900 ÷ 12 = 8325."""
    ind = _indicado(c)
    cobranca = _anual(ind, preco=preco)
    entregar("PAYMENT_RECEIVED", cobranca)
    return ind, cobranca


def test_p10a_estorno_total_de_comissao_solta(externo):
    ind = _indicado(_afiliado())
    conta(ind.uid, "free", None)
    cobranca = nova_cobranca(ind.uid, plan="pro", plan_stored="pro", price_cents=99900, credit_cents=0,
                             amount_cents=99900, extras=_CADERNOS)
    entregar("PAYMENT_RECEIVED", cobranca)
    entregar("PAYMENT_REFUNDED", cobranca)
    chave = cobranca["external_reference"]
    assert _linha(ind.uid)["status"] == "reversed"
    assert (_revisao(chave)["status"], _revisao(chave)["tipo"], _revisao(chave)["refunded_cents"]) == (
        "resolved", "total", 99900 + 1990)  # o cobrado: plano + cadernos
    assert _acoes(chave) == ["auto_reversed"]


@pytest.fixture
def avisos(monkeypatch):
    from core.services import admin_notify

    enviados = []
    monkeypatch.setattr(admin_notify, "canal_configurado", lambda: True)
    monkeypatch.setattr(admin_notify, "notify_afiliado_alerta", lambda texto: enviados.append(texto) or True)
    return enviados


def test_p10b_estorno_total_com_a_comissao_em_saque_bloqueia(externo, avisos):
    c = _afiliado()
    ind, cobranca = _cara(c)
    _vence(_linha(ind.uid)["id"])
    saque = request_payout(c.aff["id"])
    entregar("PAYMENT_REFUNDED", cobranca)
    assert _revisao(cobranca["external_reference"])["status"] == "open"
    assert len(avisos) == 1  # o admin é avisado da pendência, como na Stripe
    with pytest.raises(SaqueComPendencia):
        mark_payout_paid(saque["id"])


def test_p10c_estorno_total_com_a_comissao_paga_vira_recuperacao(externo):
    c = _afiliado()
    ind, cobranca = _cara(c)
    _vence(_linha(ind.uid)["id"])
    assert mark_payout_paid(request_payout(c.aff["id"])["id"])
    entregar("PAYMENT_REFUNDED", cobranca)
    rev = _revisao(cobranca["external_reference"])
    assert (rev["status"], rev["recovery_cents"]) == ("recovery", 8325)


def test_p11_estorno_parcial_abre_revisao_com_o_acumulado_do_asaas(externo):
    c = _afiliado()
    ind, cobranca = _cara(c)
    externo["refunds"] = [{"value": 30.0, "status": "DONE"}, {"value": 20.0, "status": "DONE"}]
    entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    rev = _revisao(cobranca["external_reference"])
    assert (rev["status"], rev["tipo"], rev["refunded_cents"]) == ("open", "partial", 5000)
    assert _linha(ind.uid)["status"] == "pending"
    (comissao,) = _me(c)["commissions"]
    assert (comissao["in_review"], comissao["payment_refund"]) == (True, "partial")


@pytest.mark.parametrize("forma", [{"refunds": None}, {"refunds": "x"}, {"erro": RuntimeError("asaas fora")}],
                         ids=["sem_lista", "texto", "asaas_fora"])
def test_p11_asaas_fora_ou_forma_inesperada_vira_retentativa(externo, forma):
    ind, cobranca = _cara(_afiliado())
    externo["refunds"] = forma.get("refunds")
    externo["get_erro"] = forma.get("erro")
    evt = entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    assert (evento(evt)["attempts"], evento(evt)["processed_at"]) == (1, None)
    assert _revisao(cobranca["external_reference"]) is None and _linha(ind.uid)["status"] == "pending"
    externo["refunds"], externo["get_erro"] = [{"value": 50.0}], None
    drenar_evento(evt)
    assert _revisao(cobranca["external_reference"])["refunded_cents"] == 5000


def test_p11_parcial_seguido_do_total_vira_total_e_nao_rebaixa(externo):
    ind, cobranca = _cara(_afiliado())
    externo["refunds"] = [{"value": 50.0}]
    entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    entregar("PAYMENT_REFUNDED", cobranca)
    rev = _revisao(cobranca["external_reference"])
    assert (rev["tipo"], rev["refunded_cents"]) == ("total", 99900)


def test_p12_estorno_antes_do_efeito_afiliado_nao_gera_e_encerra(externo, cobrar, monkeypatch):
    def falha(*a, **k):
        raise RuntimeError("afiliado ficou pendente")

    real = afiliados.record_commission_for_pix
    monkeypatch.setattr(afiliados, "record_commission_for_pix", falha)
    ind = _indicado(_afiliado())
    cobranca = _anual(ind)
    compra = entregar("PAYMENT_RECEIVED", cobranca)
    assert "afiliado" not in efeitos(cobranca["asaas_payment_id"])
    monkeypatch.setattr(afiliados, "record_commission_for_pix", real)
    entregar("PAYMENT_REFUNDED", cobranca)
    drenar_evento(compra)  # a retentativa da compra: a guarda D3 a fecha
    assert _comissoes(ind.uid) == []
    with get_conn() as conn:
        pagamentos = conn.execute("select p.source, p.refund_status from affiliate_referral_payments p"
                                  " join affiliate_referrals r on r.id = p.referral_id where r.referred_user_id = %s",
                                  (ind.uid,)).fetchall()
    assert [(p["source"], p["refund_status"]) for p in pagamentos] == [("pix", "total")]
    cobrar(ind.uid, valor=4990)
    assert _comissoes(ind.uid) == []


def test_p15_estorno_do_pix_de_indicado_excluido_estorna_a_comissao(externo):
    ind, cobranca = _cara(_afiliado())
    db.delete_user_data(ind.uid)
    entregar("PAYMENT_REFUNDED", cobranca)
    with get_conn() as conn:
        status = conn.execute("select status from affiliate_commissions where stripe_invoice_id = %s",
                              (cobranca["external_reference"],)).fetchone()["status"]
    assert (status, _revisao(cobranca["external_reference"])["status"]) == ("reversed", "resolved")


# ── P13: o que presumia Stripe ───────────────────────────────────────────────

def test_p13_me_mostra_o_rotulo_do_reembolso_da_comissao_pix(externo):
    c = _afiliado()
    ind, cobranca = _cara(c)
    entregar("PAYMENT_REFUNDED", cobranca)
    (comissao,) = _me(c)["commissions"]
    assert (comissao["status"], comissao["payment_refund"]) == ("reversed", "total")
    assert "pix" not in str(comissao)  # a chave é dado interno: não sai


def test_p13_reject_payout_estorna_a_comissao_pix_rotulada_total(externo):
    c = _afiliado()
    ind, cobranca = _cara(c)
    _vence(_linha(ind.uid)["id"])
    saque = request_payout(c.aff["id"])
    with get_conn() as conn:  # o rótulo sozinho, sem pendência: o caso que a junção cobre
        conn.execute("update affiliate_referral_payments set refund_status = 'total' where source = 'pix'"
                     " and payment_ref = %s", (str(cobranca["id"]),))
        conn.commit()
    assert reject_payout(saque["id"])
    assert _linha(ind.uid)["status"] == "reversed"


def test_p13_backfill_do_boot_nao_cria_pagamento_stripe_falso(externo):
    ind, cobranca = _cara(_afiliado())
    with get_conn() as conn:
        conn.execute("delete from affiliate_referral_payments where payment_ref = %s", (str(cobranca["id"]),))
        conn.execute(BACKFILL_AFILIADO_PAGAMENTOS_SQL)
        linhas = conn.execute("select p.source, p.payment_ref from affiliate_referral_payments p"
                              " join affiliate_referrals r on r.id = p.referral_id where r.referred_user_id = %s",
                              (ind.uid,)).fetchall()
        conn.commit()
    assert [(x["source"], x["payment_ref"]) for x in linhas] == [("pix", str(cobranca["id"]))]


def test_p13_script_de_backfill_nao_manda_a_chave_pix_para_a_stripe(externo):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import backfill_afiliado_pagamentos as script

    ind, cobranca = _cara(_afiliado())
    assert cobranca["external_reference"] not in [x["fatura"] for x in script._comissoes()]


# ── Exclusão concorrente (o T10 do cartão, no Pix) ──────────────────────────

@pytest.mark.parametrize("quem", ["indicado", "afiliado"])
def test_p16_exclusao_concorrente_com_a_comissao_pix_sem_deadlock(quem):
    import psycopg

    c = _afiliado()
    ind = _indicado(c)
    pix = {"user_id": ind.uid, "external_reference": f"pix:{uuid.uuid4().int % 10**9}", "amount_cents": 19900,
           "paid_at": AGORA}
    with ThreadPoolExecutor(2) as pool, get_conn() as segura:
        segura.execute("select id from affiliate_referrals where referred_user_id = %s for share", (ind.uid,))
        try:
            dreno = pool.submit(record_commission_for_pix, pix, MESES_DO_PIX)
            _esperando(1)
            exclusao = pool.submit(db.delete_user_data, ind.uid if quem == "indicado" else c.uid)
            _esperando(2)
        finally:
            segura.commit()
        erros = [dreno.exception(timeout=30), exclusao.exception(timeout=30)]
    assert not any(isinstance(e, psycopg.errors.DeadlockDetected) for e in erros), erros
    assert erros == [None, None], erros


# ── P17: cada parcial conta ──────────────────────────────────────────────────

def test_p17_segundo_parcial_atualiza_o_acumulado_e_o_total_fecha(externo):
    ind, cobranca = _cara(_afiliado())
    chave = cobranca["external_reference"]
    externo["refunds"] = [{"value": 10.0}]
    entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    r1 = _revisao(chave)["refunded_cents"]
    externo["refunds"] = [{"value": 10.0}, {"value": 20.0}]
    entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    r2 = _revisao(chave)["refunded_cents"]
    entregar("PAYMENT_REFUNDED", cobranca)
    assert (r1, r2, _revisao(chave)["tipo"], _revisao(chave)["refunded_cents"]) == (1000, 3000, "total", 99900)


def test_p17_segundo_parcial_reabre_a_pendencia_que_o_admin_resolveu(externo):
    from db.affiliate_reviews import resolver_parcial

    ind, cobranca = _cara(_afiliado())
    chave = cobranca["external_reference"]
    externo["refunds"] = [{"value": 10.0}]
    entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    rev = _revisao(chave)
    assert resolver_parcial(rev["id"], "admin", 8325, "aprovado inteiro: devolveu pouco", rev["refunded_cents"])
    externo["refunds"] = [{"value": 10.0}, {"value": 900.0}]
    entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    assert (_revisao(chave)["status"], _revisao(chave)["refunded_cents"]) == ("open", 91000)
    assert "reopened" in _acoes(chave)


def test_p17_reentrega_do_mesmo_parcial_nao_muda_nada(externo, avisos):
    ind, cobranca = _cara(_afiliado())
    externo["refunds"] = [{"value": 50.0}]
    entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    antes = _revisao(cobranca["external_reference"])
    entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    assert _revisao(cobranca["external_reference"]) == antes and len(avisos) == 1


# ── P18: sem afiliado, o parcial não depende do Asaas ───────────────────────

@pytest.mark.parametrize("asaas", [{"erro": RuntimeError("asaas fora")}, {"refunds": None},
                                   {"refunds": [{"value": "abc"}]}], ids=["asaas_fora", "refunds_nulo", "value_texto"])
def test_p18_parcial_de_cliente_sem_afiliado_fecha_na_primeira(externo, asaas):
    u = _conta(plano=False)
    cobranca = _anual(u)
    entregar("PAYMENT_RECEIVED", cobranca)
    externo["get_erro"], externo["refunds"] = asaas.get("erro"), asaas.get("refunds")
    evt = entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    for _ in range(3):
        drenar_evento(evt)
    assert (evento(evt)["attempts"], evento(evt)["processed_at"] is not None) == (0, True)
    assert sum("PAYMENT_PARTIALLY_REFUNDED" in m for m in externo["alerta"]) == 1
    with get_conn() as conn:
        rotulo = conn.execute("select refund_status from affiliate_referral_payments where source = 'pix'"
                              " and payment_ref = %s", (str(cobranca["id"]),)).fetchone()
    assert rotulo is None  # sem referral não há pagamento de indicado para rotular


def test_p18_parcial_de_indicado_sem_comissao_rotula_sem_o_asaas(externo):
    c = _conta()
    c.aff = create_affiliate(c.uid, commission_bps=0)
    ind = _indicado(c)
    cobranca = _anual(ind)
    entregar("PAYMENT_RECEIVED", cobranca)
    externo["get_erro"] = RuntimeError("asaas fora")
    evt = entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    assert evento(evt)["processed_at"] is not None
    with get_conn() as conn:
        rotulo = conn.execute("select refund_status from affiliate_referral_payments where source = 'pix'"
                              " and payment_ref = %s", (str(cobranca["id"]),)).fetchone()["refund_status"]
    assert rotulo == "partial"


# ── P19: a soma de `refunds` ─────────────────────────────────────────────────

def test_p19_estorno_cancelado_ou_pendente_nao_conta(externo):
    ind, cobranca = _cara(_afiliado())
    externo["refunds"] = [{"value": 50.0, "status": "DONE"}, {"value": 900.0, "status": "CANCELLED"},
                          {"value": 30.0, "status": "PENDING"}]
    entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    assert _revisao(cobranca["external_reference"])["refunded_cents"] == 5000


@pytest.mark.parametrize("valor", [-50.0, None, float("nan"), True], ids=["negativo", "ausente", "nan", "bool"])
def test_p19_value_invalido_vira_retentativa(externo, valor):
    ind, cobranca = _cara(_afiliado())
    externo["refunds"] = [{"value": valor}]
    evt = entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    assert (evento(evt)["attempts"], _revisao(cobranca["external_reference"])) == (1, None)


# ── P20: a tabela parcial × comissão (`docs/CLAUDE.md`, "Afiliado no dreno do Pix") ──

def _alertas(externo, marca):
    return sum(marca in m for m in externo["alerta"])


def _n_efeito(pid, efeito):
    with get_conn() as conn:
        return conn.execute("select count(*) as n from pix_payment_effects where asaas_payment_id = %s and effect = %s",
                            (pid, efeito)).fetchone()["n"]


def test_p20_retentativa_nao_repete_o_alerta_do_parcial(externo):
    """Comissão e Asaas fora: durante as retentativas, nenhum alerta; ele sai uma vez, na passada que
    conclui (antes, um por passada)."""
    ind, cobranca = _cara(_afiliado())
    externo["get_erro"] = RuntimeError("asaas fora")
    evt = entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    for _ in range(3):
        drenar_evento(evt)
    assert (evento(evt)["attempts"], _alertas(externo, "PAYMENT_PARTIALLY_REFUNDED")) == (4, 0)
    assert _revisao(cobranca["external_reference"]) is None  # durante as retentativas, como antes
    externo["get_erro"], externo["refunds"] = None, [{"value": 50.0}]
    drenar_evento(evt)
    assert evento(evt)["processed_at"] is not None and _alertas(externo, "PAYMENT_PARTIALLY_REFUNDED") == 1
    assert _revisao(cobranca["external_reference"])["refunded_cents"] == 5000


@pytest.mark.parametrize("asaas", [{"erro": RuntimeError("asaas fora")}, {"refunds": []},
                                   {"refunds": [{"value": "abc"}]}], ids=["asaas_fora", "soma_zero", "forma_invalida"])
def test_p20_teto_abre_pendencia_zero_que_bloqueia_o_saque(externo, avisos, asaas):
    """Sem o teto o evento retentaria até a purga de 7 dias, que fecha calada: nenhuma pendência e a
    comissão sacável. Na 6ª passada (`TENTATIVAS_ATE_TRAVAR`) ele segue com 0: `open/0`, aviso e alerta."""
    from core.services.pix_drain import TENTATIVAS_ATE_TRAVAR

    c = _afiliado()
    ind, cobranca = _cara(c)
    _vence(_linha(ind.uid)["id"])
    saque = request_payout(c.aff["id"])
    externo["get_erro"], externo["refunds"] = asaas.get("erro"), asaas.get("refunds")
    evt = entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    for _ in range(TENTATIVAS_ATE_TRAVAR - 1):
        drenar_evento(evt)
    assert (evento(evt)["attempts"], _revisao(cobranca["external_reference"]), len(avisos)) == (TENTATIVAS_ATE_TRAVAR, None, 0)
    drenar_evento(evt)
    rev = _revisao(cobranca["external_reference"])
    assert (rev["status"], rev["refunded_cents"], evento(evt)["processed_at"] is not None) == ("open", 0, True)
    assert len(avisos) == 1 and _alertas(externo, "PAYMENT_PARTIALLY_REFUNDED") == 1
    assert _alertas(externo, "evento travado") == 0
    with pytest.raises(SaqueComPendencia):
        mark_payout_paid(saque["id"])


def test_p20_n1_parcial_depois_da_exclusao_do_afiliado_com_comissao_paga(externo):
    c = _afiliado()
    ind, cobranca = _cara(c)
    _vence(_linha(ind.uid)["id"])
    assert mark_payout_paid(request_payout(c.aff["id"])["id"])
    db.delete_user_data(c.uid)
    chave = cobranca["external_reference"]
    assert (_revisao(chave)["status"], _revisao(chave)["paid_cents"]) == ("resolved", 8325)
    externo["refunds"] = [{"value": 900.0}]
    entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    assert (_revisao(chave)["status"], _revisao(chave)["refunded_cents"]) == ("open", 90000)  # como a Stripe


def test_p20_comissao_estornada_nao_consulta_o_asaas(externo):
    ind, cobranca = _cara(_afiliado())
    with get_conn() as conn:
        conn.execute("update affiliate_commissions set status = 'reversed' where referred_user_id = %s", (ind.uid,))
        conn.commit()
    externo["get_erro"] = RuntimeError("asaas fora")
    evt = entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    assert (evento(evt)["attempts"], evento(evt)["processed_at"] is not None) == (0, True)


@pytest.mark.parametrize("refunds", [[], [{"value": 50.0, "status": "PENDING"}],
                                     [{"value": 50.0, "status": "CANCELLED"}]], ids=["vazio", "pendente", "cancelado"])
def test_p20_com_comissao_soma_zero_retenta_ate_o_estorno_concluir(externo, refunds):
    ind, cobranca = _cara(_afiliado())
    externo["refunds"] = refunds
    evt = entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    assert (evento(evt)["attempts"], _revisao(cobranca["external_reference"])) == (1, None)
    externo["refunds"] = [{"value": 50.0, "status": "DONE"}]
    drenar_evento(evt)
    assert (_revisao(cobranca["external_reference"])["status"], _revisao(cobranca["external_reference"])["refunded_cents"]) == (
        "open", 5000)


@pytest.mark.parametrize("refunds,esperado", [
    ([{"value": 50}], 5000), ([{"value": 0.1}, {"value": 0.2}], 30), ([], 0),
    ([{"value": 50.0, "status": "done"}, {"value": 9.0, "status": "Pending"}], 5000),
], ids=["inteiro", "soma_de_float", "vazio", "status_minusculo"])
def test_p20_estornado_cents(monkeypatch, refunds, esperado):
    import core.services.asaas as asaas
    from core.services.afiliado_pix import estornado_cents

    monkeypatch.setattr(asaas, "buscar_pagamento", lambda pid: {"refunds": refunds})
    assert estornado_cents("pay_x") == esperado


@pytest.mark.parametrize("valor", [1e300, 0.005, 33.333, "50.00"], ids=["absurdo", "meio_centavo", "tres_casas", "texto"])
def test_p20_estornado_cents_recusa_forma_invalida(monkeypatch, valor):
    """A regra do `value` do webhook (`centavos_do_valor`): nada de arredondar dinheiro de forma estranha."""
    import core.services.asaas as asaas
    from core.services.afiliado_pix import estornado_cents

    monkeypatch.setattr(asaas, "buscar_pagamento", lambda pid: {"refunds": [{"value": valor}]})
    with pytest.raises(ValueError):
        estornado_cents("pay_x")


def test_p20_reentregas_e_acumulado_menor_nao_mudam_nada(externo, avisos):
    ind, cobranca = _cara(_afiliado())
    externo["refunds"] = [{"value": 50.0}]
    eventos = [entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca) for _ in range(4)]
    externo["refunds"] = [{"value": 10.0}]
    eventos.append(entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca))
    rev = _revisao(cobranca["external_reference"])
    assert (rev["status"], rev["refunded_cents"], len(avisos)) == ("open", 5000, 1)
    assert _n_efeito(cobranca["asaas_payment_id"], "afiliado_estorno_parcial") == 1
    assert all(evento(e)["processed_at"] for e in eventos) and _acoes(cobranca["external_reference"]) == []


def test_p20_parcial_depois_do_total_nao_consulta_nem_rebaixa(externo):
    ind, cobranca = _cara(_afiliado())
    externo["refunds"] = [{"value": 30.0}]
    entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    entregar("PAYMENT_REFUNDED", cobranca)
    externo["get_erro"] = RuntimeError("asaas fora")
    evt = entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    rev = _revisao(cobranca["external_reference"])
    assert (rev["tipo"], rev["refunded_cents"], evento(evt)["processed_at"] is not None) == ("total", 99900, True)
    assert _linha(ind.uid)["status"] == "reversed"


def test_p20_reentrega_depois_de_resolvida_nao_reabre(externo):
    from db.affiliate_reviews import resolver_parcial

    ind, cobranca = _cara(_afiliado())
    externo["refunds"] = [{"value": 10.0}]
    entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    rev = _revisao(cobranca["external_reference"])
    assert resolver_parcial(rev["id"], "admin", 8325, "aprovado inteiro: devolveu pouco", rev["refunded_cents"])
    entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    assert _revisao(cobranca["external_reference"])["status"] == "resolved"


def test_p20_parcial_antes_da_compra_com_o_asaas_fora_quando_a_comissao_nasce(externo, monkeypatch):
    """A10: a comissão nasce depois do parcial; o rótulo final do `registrar` consulta o Asaas e,
    fora do ar, a compra retenta até acertar o valor."""
    real = afiliados.record_commission_for_pix

    def falha(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(afiliados, "record_commission_for_pix", falha)
    ind = _indicado(_afiliado())
    cobranca = _anual(ind, preco=99900)
    compra = entregar("PAYMENT_RECEIVED", cobranca)
    monkeypatch.setattr(afiliados, "record_commission_for_pix", real)
    externo["refunds"] = [{"value": 50.0}]
    assert evento(entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca))["processed_at"] is not None
    externo["get_erro"] = RuntimeError("asaas fora")
    drenar_evento(compra)
    assert (_revisao(cobranca["external_reference"])["status"], evento(compra)["processed_at"]) == ("open", None)
    externo["get_erro"] = None
    drenar_evento(compra)
    rev = _revisao(cobranca["external_reference"])
    assert (rev["status"], rev["refunded_cents"], evento(compra)["processed_at"] is not None) == ("open", 5000, True)


def test_p20_total_reentregue_segue_deduplicado(externo, avisos):
    c = _afiliado()
    ind, cobranca = _cara(c)
    _vence(_linha(ind.uid)["id"])
    request_payout(c.aff["id"])
    entregar("PAYMENT_REFUNDED", cobranca)
    entregar("PAYMENT_REFUNDED", cobranca)
    assert len(avisos) == 1 and _n_efeito(cobranca["asaas_payment_id"], "afiliado_estorno") == 1


@pytest.mark.parametrize("refunds,texto", [
    ([{"value": 50.0, "status": "PENDING"}], "R$ 50.00"),
    ([{"value": 50.0}, {"status": "DONE"}], "R$ 50.00"),
    ([{"value": 30.0, "status": "DONE"}, {"value": 20.0, "status": "CANCELLED"}], "R$ 50.00"),
    (None, "acumulado indisponível"),
    ([{"value": "abc"}], "acumulado indisponível"),
], ids=["pendente", "item_sem_value", "cancelado", "sem_lista", "lixo"])
def test_p21_texto_do_alerta_do_parcial_e_o_de_antes(externo, refunds, texto):
    """O alerta ao operador soma todo `value`, como no 200baa01; só o dinheiro do afiliado usa a regra estrita.
    `lixo` antes derrubava o evento (o `float` levantava); agora vira "indisponível"."""
    u = _conta(plano=False)
    cobranca = _anual(u)
    entregar("PAYMENT_RECEIVED", cobranca)
    externo["refunds"] = refunds
    evt = entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    (alerta,) = [m for m in externo["alerta"] if "PAYMENT_PARTIALLY_REFUNDED" in m]
    assert f"devolvido: {texto}" in alerta and evento(evt)["processed_at"] is not None


@pytest.mark.parametrize("antes,abre", [(4, False), (5, True)])
def test_p22_limiar_do_teto_com_o_numero_escrito(externo, avisos, antes, abre):
    """Com 4 falhas anteriores ainda retenta; com 5, abre `open/0`. Mudar `TENTATIVAS_ATE_TRAVAR`
    sem decidir deixa este teste vermelho."""
    ind, cobranca = _cara(_afiliado())
    externo["get_erro"] = RuntimeError("asaas fora")
    evt = entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca, tentativas=antes)
    rev = _revisao(cobranca["external_reference"])
    assert (rev is not None and (rev["status"], rev["refunded_cents"]) == ("open", 0)) is abre
    assert (evento(evt)["processed_at"] is not None) is abre


def test_p22_orfao_com_o_asaas_fora_chega_ao_teto(externo, avisos):
    """Titular excluído: o parcial passa por `_orfao`, que também conta as tentativas do evento."""
    from core.services.pix_drain import TENTATIVAS_ATE_TRAVAR

    ind, cobranca = _cara(_afiliado())
    db.delete_user_data(ind.uid)
    externo["get_erro"] = RuntimeError("asaas fora")
    evt = entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    for _ in range(TENTATIVAS_ATE_TRAVAR - 1):
        drenar_evento(evt)
    assert (evento(evt)["attempts"], _revisao(cobranca["external_reference"])) == (TENTATIVAS_ATE_TRAVAR, None)
    drenar_evento(evt)
    rev = _revisao(cobranca["external_reference"])
    assert (rev["status"], rev["refunded_cents"], evento(evt)["processed_at"] is not None) == ("open", 0, True)


def test_p22_compra_que_ja_falhou_nao_gasta_o_teto_no_rotulo(externo, avisos, monkeypatch):
    """A comissão falhou 5 vezes por outro motivo; quando ela nasce, o Asaas está fora. O rótulo final do
    `registrar` não herda as tentativas da compra: ela retenta e o valor sai quando o Asaas volta."""
    from core.services.pix_drain import TENTATIVAS_ATE_TRAVAR

    real = afiliados.record_commission_for_pix

    def falha(*a, **k):
        raise RuntimeError("banco fora")

    monkeypatch.setattr(afiliados, "record_commission_for_pix", falha)
    ind = _indicado(_afiliado())
    cobranca = _anual(ind, preco=99900)
    compra = entregar("PAYMENT_RECEIVED", cobranca)
    for _ in range(TENTATIVAS_ATE_TRAVAR - 1):
        drenar_evento(compra)
    externo["refunds"] = [{"value": 50.0}]
    assert evento(entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca))["processed_at"] is not None
    monkeypatch.setattr(afiliados, "record_commission_for_pix", real)
    externo["get_erro"] = RuntimeError("asaas fora")
    drenar_evento(compra)  # a comissão nasce; o rótulo falha no Asaas: a compra retenta
    assert (evento(compra)["processed_at"], _revisao(cobranca["external_reference"])["refunded_cents"]) == (None, 0)
    externo["get_erro"] = None
    drenar_evento(compra)
    rev = _revisao(cobranca["external_reference"])
    assert (rev["status"], rev["refunded_cents"], evento(compra)["processed_at"] is not None) == ("open", 5000, True)



# ── P23: o que o #1508 acrescentou à máquina, com a chave Pix ──────────────

def test_p23_conferencia_do_saque_na_stripe_pula_a_comissao_pix(externo):
    """QR e "Marcar pago" consultam na Stripe cada fatura do saque: a chave `pix:` não é fatura, e o
    estorno dela chega pelo dreno. Sem o filtro, a Stripe recusaria a chave e o saque não sairia."""
    from db.affiliate_reviews import faturas_do_saque

    c = _afiliado()
    ind, cobranca = _cara(c)
    _vence(_linha(ind.uid)["id"])
    saque = request_payout(c.aff["id"])
    assert faturas_do_saque(saque["id"]) == []


def test_p23_parcial_de_comissao_em_saque_orfao_abre_e_bloqueia(externo):
    """Afiliado excluído com a comissão num saque ainda aberto: `_comissao_que_saiu` a refaz como em saque,
    então o acumulado decide (`precisa_do_acumulado`) e a pendência bloqueia o saque órfão."""
    from db.affiliate_reviews import saque_bloqueado

    c = _afiliado()
    ind, cobranca = _cara(c)
    _vence(_linha(ind.uid)["id"])
    saque = request_payout(c.aff["id"])
    db.delete_user_data(c.uid)
    externo["refunds"] = [{"value": 50.0}]
    entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    rev = _revisao(cobranca["external_reference"])
    assert (rev["status"], rev["refunded_cents"]) == ("open", 5000)
    with get_conn() as conn, conn.cursor() as cur:
        assert saque_bloqueado(cur, saque["id"]) is True


def _so_cartao(c, fatura):
    """Comissão do cartão já conferida (A5) e vencida, para entrar no saque."""
    linha = record_commission_for_invoice(_indicado(c).uid, fatura, 99900)
    with get_conn() as conn:
        conn.execute("update affiliate_commissions set payment_check_pending = false, available_at = now() - interval '1 day'"
                     " where id = %s", (linha["id"],))
        conn.commit()


def test_p23_saque_orfao_misto_confere_so_o_cartao(externo):
    """Ramo do saque órfão: depois da exclusão as comissões saem, e as faturas vêm das linhas gravadas."""
    from db.affiliate_reviews import faturas_do_saque

    c = _afiliado()
    ind, cobranca = _cara(c)
    _so_cartao(c, f"in_misto_{uuid.uuid4().hex[:8]}")
    _vence(_linha(ind.uid)["id"])
    saque = request_payout(c.aff["id"])
    cartao = [f["fatura"] for f in faturas_do_saque(saque["id"])]
    db.delete_user_data(c.uid)
    assert [(f["commission_id"], f["fatura"]) for f in faturas_do_saque(saque["id"])] == [(None, cartao[0])]


def test_p23_script_de_backfill_pula_a_pix_paga_de_afiliado_excluido(externo):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import backfill_afiliado_pagamentos as script

    c = _afiliado()
    ind, cobranca = _cara(c)
    _vence(_linha(ind.uid)["id"])
    assert mark_payout_paid(request_payout(c.aff["id"])["id"])
    db.delete_user_data(c.uid)
    assert _revisao(cobranca["external_reference"])["paid_cents"] == 8325  # o ramo 2 do script
    assert cobranca["external_reference"] not in [x["fatura"] for x in script._comissoes()]


@pytest.mark.parametrize("so_pix,esperado", [(True, 200), (False, 503)], ids=["so_pix", "com_cartao"])
def test_p23_saque_so_pix_nao_precisa_da_stripe(externo, monkeypatch, so_pix, esperado):
    """QR e "Marcar pago" conferem na Stripe as faturas do saque; sem nenhuma, não há o que perguntar."""
    from test_afiliado_area import _sacar
    from test_afiliado_pagantes import _admin

    c = _afiliado()
    ind, cobranca = _cara(c)
    _vence(_linha(ind.uid)["id"])
    if not so_pix:
        _so_cartao(c, f"in_{uuid.uuid4().hex[:12]}")
    r = _sacar(c)
    assert r.status_code == 200, r.text
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    admin = _admin(monkeypatch)
    assert admin.get(f"/admin/api/affiliates/payouts/{r.json()['payout']['id']}/pix").status_code == esperado


def _saque_so_pix(c, monkeypatch):
    """Saque com uma comissão Pix vencida, sem `STRIPE_SECRET_KEY`, e o cliente admin."""
    from test_afiliado_area import _sacar
    from test_afiliado_pagantes import _admin

    ind, cobranca = _cara(c)
    _vence(_linha(ind.uid)["id"])
    r = _sacar(c)
    assert r.status_code == 200, r.text
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    return cobranca, r.json()["payout"]["id"], _admin(monkeypatch)


def _status_do_saque(saque):
    with get_conn() as conn:
        return conn.execute("select status from affiliate_payouts where id = %s", (saque,)).fetchone()["status"]


def test_p23_marcar_pago_de_saque_so_pix_sem_chave(externo, monkeypatch):
    from _apoio_auth_app import csrf

    cobranca, saque, admin = _saque_so_pix(_afiliado(), monkeypatch)
    r = admin.post(f"/admin/api/affiliates/payouts/{saque}/paid", headers=csrf(admin), json={})
    assert (r.status_code, _status_do_saque(saque)) == (200, "paid"), r.text


def test_p23_atalho_sem_stripe_nao_pula_o_bloqueio_da_pendencia_pix(externo, monkeypatch):
    """O estorno do Pix chegou pelo dreno: a pendência bloqueia o saque mesmo sem nada a perguntar à Stripe."""
    from _apoio_auth_app import csrf

    cobranca, saque, admin = _saque_so_pix(_afiliado(), monkeypatch)
    entregar("PAYMENT_REFUNDED", cobranca)
    assert _revisao(cobranca["external_reference"])["status"] == "open"
    assert admin.get(f"/admin/api/affiliates/payouts/{saque}/pix").status_code == 409
    assert admin.post(f"/admin/api/affiliates/payouts/{saque}/paid", headers=csrf(admin), json={}).status_code == 409
    assert _status_do_saque(saque) == "requested"


def test_p23_aviso_que_falha_no_pix_identifica_a_pendencia(externo):
    """Comissão que saiu com o afiliado e aviso sem canal: o log diz qual pendência, sem comissão nem evento."""
    c = _afiliado()
    ind, cobranca = _cara(c)
    _vence(_linha(ind.uid)["id"])
    assert mark_payout_paid(request_payout(c.aff["id"])["id"])
    db.delete_user_data(c.uid)
    externo["refunds"] = [{"value": 900.0}]
    entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    rev = _revisao(cobranca["external_reference"])
    with get_conn() as conn:
        logs = [x["details"] for x in conn.execute(
            "select details from system_event_logs where event_type = 'affiliate_refund_commission_kept'"
            " and (details->>'review_id')::bigint = %s", (rev["id"],)).fetchall()]
    assert len(logs) == 1 and logs[0]["commission_id"] is None and "event_id" not in logs[0]


# ── P24: valor desconhecido (teto do parcial) e baixa desfeita seguram a comissão ───────────

UNDONE = "PAYMENT_RECEIVED_IN_CASH_UNDONE"


def _esgota_parcial(externo, cobranca):
    """Asaas fora: drena o parcial até o teto (`TENTATIVAS_ATE_TRAVAR`), onde ele segue com 0."""
    from core.services.pix_drain import TENTATIVAS_ATE_TRAVAR

    externo["get_erro"] = RuntimeError("asaas fora")
    evt = entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    for _ in range(TENTATIVAS_ATE_TRAVAR):
        drenar_evento(evt)
    externo["get_erro"] = None
    assert evento(evt)["processed_at"] is not None


def _parcial_resolvido(externo, c):
    """RECEIVED, 1º parcial de R$ 100 (Asaas), admin fecha: revisão `resolved` com X = 10000."""
    from db.affiliate_reviews import resolver_parcial

    ind, cobranca = _cara(c)
    _vence(_linha(ind.uid)["id"])
    externo["refunds"] = [{"value": 100.0}]
    entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    rev = _revisao(cobranca["external_reference"])
    assert rev["status"] == "open" and rev["refunded_cents"] == 10000
    assert resolver_parcial(rev["id"], "admin", 8325, "ok", rev["refunded_cents"])
    assert _revisao(cobranca["external_reference"])["status"] == "resolved"
    return ind, cobranca


def test_p24_parcial_resolvido_e_segundo_com_asaas_fora_reabre_sem_apagar_o_valor(externo, avisos):
    c = _afiliado()
    ind, cobranca = _parcial_resolvido(externo, c)
    saque = request_payout(c.aff["id"])
    n = len(avisos)
    externo["refunds"] = []
    _esgota_parcial(externo, cobranca)
    rev = _revisao(cobranca["external_reference"])
    assert (rev["status"], rev["refunded_cents"]) == ("open", 10000)
    assert len(avisos) == n + 1
    with pytest.raises(SaqueComPendencia):
        mark_payout_paid(saque["id"])


def test_p24_positivo_segundo_parcial_maior_reabre_com_o_valor_novo(externo):
    ind, cobranca = _parcial_resolvido(externo, _afiliado())
    externo["refunds"] = [{"value": 100.0}, {"value": 50.0}]
    entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    rev = _revisao(cobranca["external_reference"])
    assert (rev["status"], rev["refunded_cents"]) == ("open", 15000)


def test_p24_positivo_reentrega_do_mesmo_parcial_nao_reabre_a_resolvida(externo):
    ind, cobranca = _parcial_resolvido(externo, _afiliado())
    entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)  # Asaas ok, mesmo acumulado
    assert _revisao(cobranca["external_reference"])["status"] == "resolved"


def test_p24_positivo_comissao_estornada_com_fallback_zero_nao_cria_pendencia(externo):
    ind, cobranca = _cara(_afiliado())
    with get_conn() as conn:
        conn.execute("update affiliate_commissions set status = 'reversed' where referred_user_id = %s", (ind.uid,))
        conn.commit()
    externo["refunds"] = []
    _esgota_parcial(externo, cobranca)
    rev = _revisao(cobranca["external_reference"])
    assert rev is None or rev["status"] != "open"


def test_p24_baixa_desfeita_segura_a_comissao_e_o_pix_real_depois_nao_solta(externo, avisos):
    c = _afiliado()
    ind, cobranca = _cara(c)
    _vence(_linha(ind.uid)["id"])
    chave = cobranca["external_reference"]
    saque = request_payout(c.aff["id"])
    entregar(UNDONE, cobranca)  # get_status PENDING: o Asaas não mostra pago
    assert _revisao(chave)["status"] == "open" and len(avisos) == 1
    with pytest.raises(SaqueComPendencia):
        mark_payout_paid(saque["id"])
    with pytest.raises(ValueError):  # e a comissão não entra em saque novo
        request_payout(c.aff["id"])
    externo["get_status"] = "RECEIVED"
    entregar("PAYMENT_RECEIVED", cobranca)
    assert len(_comissoes_completas(ind.uid)) == 1 and _revisao(chave)["status"] == "open"
    from db.plan_grants import list_grants
    assert [g["status"] for g in list_grants(ind.uid) if g["source"] == "pix"] == ["active"]


def test_p24_baixa_desfeita_reentregue_nao_duplica_a_pendencia(externo, avisos):
    ind, cobranca = _cara(_afiliado())
    chave = cobranca["external_reference"]
    entregar(UNDONE, cobranca)
    antes = _revisao(chave)
    entregar(UNDONE, cobranca)
    assert _revisao(chave) == antes and len(avisos) == 1


def test_p24_baixa_desfeita_com_a_comissao_paga_abre_e_nao_mexe_no_saque(externo):
    c = _afiliado()
    ind, cobranca = _cara(c)
    _vence(_linha(ind.uid)["id"])
    saque = request_payout(c.aff["id"])
    assert mark_payout_paid(saque["id"])
    entregar(UNDONE, cobranca)
    assert _revisao(cobranca["external_reference"])["status"] == "open"
    with get_conn() as conn:
        assert conn.execute("select status from affiliate_payouts where id = %s", (saque["id"],)).fetchone()["status"] == "paid"
        assert conn.execute("select status from affiliate_commissions where referred_user_id = %s",
                            (ind.uid,)).fetchone()["status"] == "paid"


def test_p24_baixa_desfeita_de_cobranca_parcialmente_estornada_vira_revisao_total(externo):
    ind, cobranca = _cara(_afiliado())
    externo["refunds"] = [{"value": 100.0}]
    entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    entregar(UNDONE, cobranca)
    assert _revisao(cobranca["external_reference"])["tipo"] == "total"


def test_p24_positivo_sem_undone_a_comissao_e_sacavel_sem_revisao(externo):
    c = _afiliado()
    ind, cobranca = _cara(c)
    _vence(_linha(ind.uid)["id"])
    assert _revisao(cobranca["external_reference"]) is None
    assert mark_payout_paid(request_payout(c.aff["id"])["id"])


def test_p24_baixa_desfeita_sem_comissao_nao_cria_revisao(externo):
    ind = _indicado(_afiliado())
    cobranca = _pix(ind)
    entregar("PAYMENT_RECEIVED", cobranca)
    with get_conn() as conn:
        conn.execute("update affiliate_commissions set status = 'reversed' where referred_user_id = %s", (ind.uid,))
        conn.commit()
    entregar(UNDONE, cobranca)
    rev = _revisao(cobranca["external_reference"])
    assert rev is None or rev["status"] != "open"


def _falha_na_1a(monkeypatch):
    """`registrar_reembolso` levanta na 1ª chamada (efeito do afiliado que falha no meio da baixa desfeita)."""
    import db.affiliate_reviews as rv

    real, chamadas = rv.registrar_reembolso, []

    def quebra_uma_vez(*a, **kw):
        chamadas.append(1)
        if len(chamadas) == 1:
            raise RuntimeError("banco caiu")
        return real(*a, **kw)

    monkeypatch.setattr(rv, "registrar_reembolso", quebra_uma_vez)


def test_p24_baixa_desfeita_paga_com_falha_no_afiliado_o_retry_segura_a_comissao(externo, monkeypatch):
    ind, cobranca = _cara(_afiliado())
    _vence(_linha(ind.uid)["id"])
    _falha_na_1a(monkeypatch)
    evt = entregar(UNDONE, cobranca)
    assert evento(evt)["processed_at"] is None and _revisao(cobranca["external_reference"]) is None
    drenar_evento(evt)
    assert evento(evt)["processed_at"] is not None
    assert _revisao(cobranca["external_reference"])["status"] == "open"


def test_p24_baixa_desfeita_parcial_com_falha_no_afiliado_o_retry_vira_revisao_total(externo, monkeypatch):
    ind, cobranca = _cara(_afiliado())
    externo["refunds"] = [{"value": 100.0}]
    entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    assert _revisao(cobranca["external_reference"])["tipo"] == "partial"
    _falha_na_1a(monkeypatch)
    evt = entregar(UNDONE, cobranca)
    assert evento(evt)["processed_at"] is None and _revisao(cobranca["external_reference"])["tipo"] == "partial"
    drenar_evento(evt)
    assert evento(evt)["processed_at"] is not None
    assert _revisao(cobranca["external_reference"])["tipo"] == "total"


def test_p24_baixa_desfeita_nao_rotula_o_pagamento_como_reembolsado(externo):
    ind, cobranca = _cara(_afiliado())
    entregar(UNDONE, cobranca)
    with get_conn() as conn:
        rotulo = conn.execute("select refund_status from affiliate_referral_payments where source = 'pix'"
                              " and payment_ref = %s", (str(cobranca["id"]),)).fetchone()["refund_status"]
    assert rotulo not in ("partial", "total")


def test_p24_positivo_estorno_parcial_com_asaas_fora_rotula_o_pagamento(externo):
    ind, cobranca = _cara(_afiliado())
    _esgota_parcial(externo, cobranca)
    with get_conn() as conn:
        rotulo = conn.execute("select refund_status from affiliate_referral_payments where source = 'pix'"
                              " and payment_ref = %s", (str(cobranca["id"]),)).fetchone()["refund_status"]
    assert rotulo == "partial"


def test_p24_revisao_ja_aberta_e_novo_parcial_com_asaas_fora_nao_reaviso_nem_regrava(externo, avisos):
    ind, cobranca = _cara(_afiliado())
    _vence(_linha(ind.uid)["id"])
    externo["refunds"] = [{"value": 100.0}]
    entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    antes, n = _revisao(cobranca["external_reference"]), len(avisos)
    assert antes["status"] == "open" and antes["notified_at"] is not None
    _esgota_parcial(externo, cobranca)
    assert _revisao(cobranca["external_reference"]) == antes and len(avisos) == n


def test_p24_baixa_desfeita_de_cobranca_sem_afiliado_nao_cria_revisao(externo):
    cobranca = _pix(_conta(plano=False))
    entregar("PAYMENT_RECEIVED", cobranca)
    entregar(UNDONE, cobranca)
    assert _revisao(cobranca["external_reference"]) is None


# ── P24b: o desconhecido só reabre a `resolved` cuja comissão ainda não saiu ─────────────────

def _paga_e_parcial(externo, c, aprovado):
    """Comissão PAGA, parcial de R$ 100 (Asaas), admin aprova `aprovado`."""
    from db.affiliate_reviews import resolver_parcial

    ind, cobranca = _cara(c)
    _vence(_linha(ind.uid)["id"])
    assert mark_payout_paid(request_payout(c.aff["id"])["id"])
    externo["refunds"] = [{"value": 100.0}]
    entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    rev = _revisao(cobranca["external_reference"])
    assert resolver_parcial(rev["id"], "admin", aprovado, "ok", rev["refunded_cents"])
    return cobranca


def test_p24b_recovery_com_comissao_paga_e_novo_parcial_desconhecido_nao_muda_nada(externo, avisos):
    cobranca = _paga_e_parcial(externo, _afiliado(), 0)
    chave = cobranca["external_reference"]
    antes, n = _revisao(chave), len(avisos)
    assert (antes["status"], antes["approved_cents"], antes["recovery_cents"]) == ("recovery", 0, 8325)
    _esgota_parcial(externo, cobranca)
    depois = _revisao(chave)
    assert (depois["status"], depois["approved_cents"], depois["recovery_cents"]) == ("recovery", 0, 8325)
    assert depois == antes and len(avisos) == n and "reopened" not in _acoes(chave)


def test_p24b_resolvida_com_comissao_paga_e_novo_parcial_desconhecido_nao_muda_nada(externo, avisos):
    cobranca = _paga_e_parcial(externo, _afiliado(), 8325)
    chave = cobranca["external_reference"]
    antes, n = _revisao(chave), len(avisos)
    assert antes["status"] == "resolved"
    _esgota_parcial(externo, cobranca)
    assert _revisao(chave) == antes and len(avisos) == n and "reopened" not in _acoes(chave)


def test_p24b_baixa_desfeita_abre_resolvida_e_o_undone_reentregue_nao_reabre(externo, avisos):
    from db.affiliate_reviews import resolver_parcial

    c = _afiliado()
    ind, cobranca = _cara(c)
    _vence(_linha(ind.uid)["id"])
    chave = cobranca["external_reference"]
    entregar(UNDONE, cobranca)
    rev = _revisao(chave)
    assert rev["status"] == "open" and len(avisos) == 1
    assert resolver_parcial(rev["id"], "admin", 8325, "ok", rev["refunded_cents"])
    entregar(UNDONE, cobranca)  # cobrança já em `pending`: não chega a `_segurar_comissao`
    assert _revisao(chave)["status"] == "resolved" and len(avisos) == 1
    with get_conn() as conn:
        assert conn.execute("select status from pix_charges where id = %s", (cobranca["id"],)).fetchone()["status"] == "pending"


def test_p24b_saque_orfao_requested_e_novo_parcial_desconhecido_reabre(externo):
    from db.affiliate_reviews import resolver_parcial

    c = _afiliado()
    ind, cobranca = _cara(c)
    _vence(_linha(ind.uid)["id"])
    request_payout(c.aff["id"])
    externo["refunds"] = [{"value": 100.0}]
    entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    rev = _revisao(cobranca["external_reference"])
    assert resolver_parcial(rev["id"], "admin", 8325, "ok", rev["refunded_cents"])
    db.delete_user_data(c.uid)
    with get_conn() as conn:
        assert conn.execute("select count(*) n from affiliate_commissions where referred_user_id = %s",
                            (ind.uid,)).fetchone()["n"] == 0  # c None: só `_comissao_que_saiu` decide
    assert _revisao(cobranca["external_reference"])["status"] == "resolved"
    externo["refunds"] = []
    _esgota_parcial(externo, cobranca)
    assert _revisao(cobranca["external_reference"])["status"] == "open"


def test_p24b_baixa_desfeita_de_novo_depois_do_pix_real_reabre_a_resolvida_e_bloqueia_o_saque(externo, avisos):
    from db.affiliate_reviews import resolver_parcial

    c = _afiliado()
    ind, cobranca = _cara(c)
    _vence(_linha(ind.uid)["id"])
    chave = cobranca["external_reference"]
    entregar(UNDONE, cobranca)  # get_status PENDING
    rev = _revisao(chave)
    assert rev["status"] == "open" and len(avisos) == 1
    assert resolver_parcial(rev["id"], "admin", 8325, "ok", rev["refunded_cents"])
    externo["get_status"] = "RECEIVED"
    entregar("PAYMENT_RECEIVED", cobranca)  # a cobrança volta a `paid`
    assert _revisao(chave)["status"] == "resolved"
    saque = request_payout(c.aff["id"])
    externo["get_status"] = "PENDING"
    entregar(UNDONE, cobranca)  # de novo
    assert _revisao(chave)["status"] == "open" and len(avisos) == 2
    with pytest.raises(SaqueComPendencia):
        mark_payout_paid(saque["id"])


# ── P25: RECEIVED e UNDONE do mesmo pagamento em paralelo (trava `afiliado`) ────────────────

def _undone_entre_grant_e_afiliado(monkeypatch, cobranca, tipo=UNDONE):
    """O UNDONE (ou `tipo`) completo roda no meio do RECEIVED: depois do `grant`, antes do `afiliado` (via o `email`)."""
    from core.services.pix_drain_effects import EXECUTORES

    real, uma_vez = EXECUTORES["email"], []

    def email_e_undone(c, evt):
        real(c, evt)
        if not uma_vez:
            uma_vez.append(entregar(tipo, cobranca))

    monkeypatch.setitem(EXECUTORES, "email", email_e_undone)
    return uma_vez


def test_p25_undone_completo_entre_grant_e_afiliado_nao_cria_comissao_sacavel(externo, monkeypatch):
    ind = _indicado(_afiliado())
    cobranca = _anual(ind)
    uma_vez = _undone_entre_grant_e_afiliado(monkeypatch, cobranca)
    eid = entregar("PAYMENT_RECEIVED", cobranca)
    assert uma_vez and ler(cobranca["id"])["status"] == "pending"  # o UNDONE reabriu
    assert _comissoes_completas(ind.uid) == []
    assert evento(eid)["processed_at"] is None and evento(eid)["last_error"] == "CobrancaReaberta"
    drenar_evento(eid)  # retentativa: guarda da baixa desfeita fecha sem efeito
    assert evento(eid)["processed_at"] is not None and _comissoes_completas(ind.uid) == []
    assert "afiliado" not in efeitos(cobranca["asaas_payment_id"])


def test_p25_undone_espera_o_registrar_da_comissao_do_received(externo, monkeypatch):
    """A trava `afiliado` aninhada em `desfazer_baixa`: o UNDONE entregue com o `registrar` do RECEIVED em
    andamento (já passou pelo veto) não termina antes dele; sem a trava ele "segura" uma comissão que
    ainda não existe e ela nasce sacável depois."""
    ind = _indicado(_afiliado())
    cobranca = _anual(ind)
    from core.services import afiliado_pix

    real = afiliado_pix.registrar
    dentro, undone_fim, durante = threading.Event(), threading.Event(), []

    def registrar_lento(*a, **kw):
        dentro.set()
        durante.append(undone_fim.wait(1.0))  # com a trava o UNDONE não termina aqui: estoura o 1s
        return real(*a, **kw)

    monkeypatch.setattr(afiliado_pix, "registrar", registrar_lento)

    def undone():
        assert dentro.wait(10)
        entregar(UNDONE, cobranca)
        undone_fim.set()

    with ThreadPoolExecutor(1) as pool:
        f = pool.submit(undone)
        entregar("PAYMENT_RECEIVED", cobranca)
        f.result(timeout=20)
    assert durante == [False]
    chave = cobranca["external_reference"]
    assert len(_comissoes_completas(ind.uid)) == 1 and _revisao(chave)["status"] == "open"


def test_p25_undone_depois_da_comissao_abre_revisao_sem_valor(externo):
    # intercalação 2: a comissão já nasceu; o UNDONE a segura. (A ordem das travas em si não é
    # determinística sem hook no lock: sem cobertura de corrida real, só do resultado sequencial.)
    ind, cobranca = _cara(_afiliado())
    entregar(UNDONE, cobranca)
    rev = _revisao(cobranca["external_reference"])
    assert (rev["status"], rev["refunded_cents"]) == ("open", 0) and len(_comissoes_completas(ind.uid)) == 1


def test_p25_positivo_received_comum_comissiona_uma_vez(externo):
    ind = _indicado(_afiliado())
    cobranca = _anual(ind)
    eid = entregar("PAYMENT_RECEIVED", cobranca)
    drenar_evento(eid)
    assert evento(eid)["processed_at"] is not None and len(_comissoes_completas(ind.uid)) == 1


def test_p25_positivo_estorno_total_entre_grant_e_afiliado_registra_pagamento_sem_comissao(externo, monkeypatch):
    ind = _indicado(_afiliado())
    cobranca = _anual(ind)
    uma_vez = _undone_entre_grant_e_afiliado(monkeypatch, cobranca, "PAYMENT_REFUNDED")
    eid = entregar("PAYMENT_RECEIVED", cobranca)
    assert uma_vez and ler(cobranca["id"])["status"] == "refunded"
    assert evento(eid)["processed_at"] is not None and _comissoes_completas(ind.uid) == []
    with get_conn() as conn:
        assert conn.execute("select count(*) n from affiliate_referral_payments where source = 'pix' and payment_ref = %s",
                            (str(cobranca["id"]),)).fetchone()["n"] == 1
