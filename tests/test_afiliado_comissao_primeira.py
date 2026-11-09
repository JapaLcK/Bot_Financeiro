"""Comissão por percentual individual, uma vez por cliente, na primeira cobrança paga (cartão).

Postgres real, `/billing/webhook` real (FakeStripe) e rota real do admin. A regra mora em
`db/affiliates.py::_comissao_da_primeira`; o marcador é `affiliate_referrals.first_charge_ref`.
O Pix entra no PR seguinte (`tests/test_afiliado_comissao_pix.py`).

Controles negativos (rodados à mão, §3 do `CLAUDE.md`; o que fica vermelho está no relato do PR):
  T0  `//` no lugar do ROUND_HALF_UP             → 413 e 1000 viram 412 e 999.
  T1  DEFAULT_COMMISSION_BPS = 1000             → comissão 499.
  T1a `_meses_do_plano` sempre 1                 → 19900.
  T1c divisor só pela assinatura                 → a fatura anual reprocessada paga 19900 (os dois formatos);
      sem o `Price.retrieve` do formato dahlia     → idem, só no dahlia;
      sem o atalho "mesmo price da assinatura"     → o retrieve falha e a mensal vira 416;
      price expandido sem `recurring` consultado  → o retrieve falha e a mensal vira 1/12.
      aviso com o price da assinatura          → o id da linha que falhou some do log.
  T1d divisor calculado antes da checagem da referral → `retrieve` e aviso para cliente sem afiliado.
  T2  sem `commission_bps` no insert             → coluna nula.
  T4  sem as duas guardas de valor zero (webhook e função) → a fatura do trial vira marcador.
  T5/T5b/T5c sem os passos 5 e 6 (marcador e histórico) E sem a pré-checagem do marcador no webhook
      (as três camadas seguram sozinhas) → renovação, reassinatura ou a renovação depois do estorno
      total da 1ª geram.
  T6/T7 marcador só quando gera                  → o reenvio da 1ª gera depois de o % subir.
  T9  sem `trava_indicado`                       → 2 comissões; histórico "qualquer outro" → 0.
  T10 marcador antes do insert                   → DeadlockDetected.
  T11 sem a guarda do bloco de migração          → o 2500 do admin volta a 10000.
  T11b `lock table` na fila (sem `nowait`)       → DeadlockDetected com dois webhooks antigos em voo;
       na ordem inversa, o cadastro derruba a migração.
  T11c sem o `raise` no teto                      → a migração passa calada sem as colunas.
  T12 sem `Depends(_get_current_admin)`          → o usuário comum altera.
  T15 sem a leitura do histórico                 → o cartão gera depois de pagamento antigo.
  T17 `except` da comissão só com `print`        → a falha some sem rastro.
  T17b conferência de volta no `try` da comissão → "não registrada" para comissão que existe.
  T18 `returning *` no insert da comissão         → `FeatureNotSupported` depois da coluna refeita.
"""
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

import db
import db.affiliates as afiliados
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import csrf
from _billing_grants_helpers import garantir_system_event_logs
from db.affiliate_payments import registrar_pagamento
from db.affiliates import (
    create_affiliate,
    get_affiliate_by_user,
    record_commission_for_invoice,
    set_affiliate_status,
    valor_da_comissao,
)
from db import schema
from db.connection import get_conn
from test_afiliado_area import _conta, _me, _painel_aberto  # noqa: F401
from test_afiliado_pagantes import _admin, _indicado, cobrar, pool_de  # noqa: F401
from test_billing_webhook_lifecycle import _T_LIFE, _fake_sub, _post

AGORA = datetime(2026, 11, 9, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _logs():
    garantir_system_event_logs()


def _afiliado(bps=None):
    c = _conta()
    c.aff = create_affiliate(c.uid) if bps is None else create_affiliate(c.uid, commission_bps=bps)
    return c


def _comissoes(uid):
    with get_conn() as conn:
        return conn.execute(
            "select stripe_invoice_id, invoice_amount_cents, amount_cents, commission_bps, paid_months"
            " from affiliate_commissions where referred_user_id = %s order by id", (uid,)).fetchall()


def _marca(uid):
    with get_conn() as conn:
        return conn.execute("select first_charge_ref from affiliate_referrals where referred_user_id = %s",
                            (uid,)).fetchone()["first_charge_ref"]


def _evento(cobrar, tipo, objeto, created):
    r = _post(TestClient(dashboard.app), cobrar.fake, {"type": tipo, "id": f"evt_{uuid.uuid4().hex}",
                                                       "created": created, "data": {"object": objeto}})
    assert r.status_code == 200, r.text


def _set_bps(admin, c, bps):
    r = admin.post(f"/admin/api/affiliates/{c.aff['id']}/commission", headers=csrf(admin), json={"commission_bps": bps})
    assert r.status_code == 200, r.text


# ── T0: arredondamento ───────────────────────────────────────────────────────

@pytest.mark.parametrize("base,bps,meses,esperado", [
    (19900, 10000, 12, 1658), (19990, 3300, 12, 550), (9900, 5000, 12, 413),
    (11940, 10000, 12, 995), (1999, 5000, 1, 1000), (4990, 3333, 1, 1663),
])
def test_t0_valor_da_comissao(base, bps, meses, esperado):
    assert valor_da_comissao(base, bps, meses) == esperado


# ── T1, T1a, T1b, T2, T3: valor, base, percentual e divisor ─────────────────

def test_t1_padrao_100_pelo_admin_e_mensal_sem_divisao(cobrar, monkeypatch):
    c = _conta()
    admin = _admin(monkeypatch)
    assert admin.post("/admin/api/affiliates", headers=csrf(admin), json={"email": c.email}).status_code == 200
    c.aff = get_affiliate_by_user(c.uid)
    assert c.aff["commission_bps"] == 10_000
    ind = _indicado(c)
    fatura = cobrar(ind.uid, valor=4990)
    assert _comissoes(ind.uid) == [{"stripe_invoice_id": fatura, "invoice_amount_cents": 4990,
                                    "amount_cents": 4990, "commission_bps": 10_000, "paid_months": 1}]


def test_t1_positivo_percentual_explicito_e_respeitado(cobrar):
    ind = _indicado(_afiliado(2500))
    cobrar(ind.uid, valor=4990)
    assert [(x["amount_cents"], x["commission_bps"]) for x in _comissoes(ind.uid)] == [(1248, 2500)]


def test_t1a_cartao_anual_divide_por_12(cobrar):
    cobrar.fake.subs["sub_afil"] = _fake_sub("active", interval="year")
    ind = _indicado(_afiliado())
    cobrar(ind.uid, valor=19900)
    assert [(x["amount_cents"], x["invoice_amount_cents"], x["paid_months"]) for x in _comissoes(ind.uid)] == [
        (1658, 19900, 12)]


def test_t1b_intervalo_desconhecido_cai_em_12_e_avisa(cobrar):
    sub = _fake_sub("active", "price_sem_recorrencia")
    del sub["items"]["data"][0]["price"]["recurring"]
    cobrar.fake.subs["sub_afil"] = sub
    ind = _indicado(_afiliado())
    fatura = cobrar(ind.uid, valor=19900)
    assert [(x["amount_cents"], x["paid_months"]) for x in _comissoes(ind.uid)] == [(1658, 12)]
    with get_conn() as conn:
        logs = conn.execute("select details from system_event_logs where event_type = %s and user_id = %s",
                            ("affiliate_commission_interval_unknown", ind.uid)).fetchall()
    assert len(logs) == 1 and logs[0]["details"] == {"price_ids": ["price_sem_recorrencia"]}
    assert fatura not in str(logs[0]["details"])


def _linha_de_plano(price_id, interval=None):
    """Linha da fatura. Com `interval`, o formato antigo (intervalo no price e no plan); sem, o da API
    `dahlia`, que a produção usa: só o id em `pricing.price_details.price`."""
    linha = {"amount": 19900, "pricing": {"price_details": {"price": price_id}}, "discount_amounts": []}
    if interval:
        ciclo = {"interval": interval, "interval_count": 1}
        linha.update(price={"id": price_id, "recurring": ciclo}, plan=ciclo)
    return linha


def _price(price_id, interval):
    return {"id": price_id, "recurring": {"interval": interval, "interval_count": 1}}


@pytest.mark.parametrize("formato,da_fatura,da_assinatura,valor,esperado", [
    # a anual reprocessada depois da troca agendada para mensal
    ("antigo", "year", "month", 19900, (1658, 12)), ("dahlia", "year", "month", 19900, (1658, 12)),
    # positivo: a mensal não vira 1/12 com a assinatura já anual
    ("antigo", "month", "year", 4990, (4990, 1)), ("dahlia", "month", "year", 4990, (4990, 1)),
], ids=["antigo_anual_assinatura_mensal", "dahlia_anual_assinatura_mensal",
        "antigo_mensal_assinatura_anual", "dahlia_mensal_assinatura_anual"])
def test_t1c_divisor_vem_da_fatura_e_nao_da_assinatura_de_agora(cobrar, formato, da_fatura, da_assinatura, valor,
                                                                esperado):
    cobrar.fake.subs["sub_afil"] = _fake_sub("active", "price_agora", interval=da_assinatura)
    cobrar.fake.prices["price_da_fatura"] = _price("price_da_fatura", da_fatura)
    ind = _indicado(_afiliado())
    cobrar(ind.uid, valor=valor, linhas=[_linha_de_plano("price_da_fatura", da_fatura if formato == "antigo" else None)])
    assert [(x["amount_cents"], x["paid_months"]) for x in _comissoes(ind.uid)] == [esperado]


def _avisos_de_intervalo(uid):
    with get_conn() as conn:
        return conn.execute("select count(*) as n from system_event_logs where event_type = %s and user_id = %s",
                            ("affiliate_commission_interval_unknown", uid)).fetchone()["n"]


def test_t1c_dahlia_mesmo_price_da_assinatura_nao_consulta_a_stripe(cobrar):
    """Sem `prices` no fake, qualquer `Price.retrieve` falharia e daria 12."""
    ind = _indicado(_afiliado())
    cobrar(ind.uid, valor=4990, linhas=[_linha_de_plano("price_desconhecido")])
    assert [(x["amount_cents"], x["paid_months"]) for x in _comissoes(ind.uid)] == [(4990, 1)]
    assert _avisos_de_intervalo(ind.uid) == 0


def test_t1c_dahlia_upgrade_com_proracao_mensal_vale_o_anual(cobrar):
    cobrar.fake.subs["sub_afil"] = _fake_sub("active", "price_anual", interval="year")
    cobrar.fake.prices["price_mensal"] = _price("price_mensal", "month")
    ind = _indicado(_afiliado())
    cobrar(ind.uid, valor=18400, linhas=[_linha_de_plano("price_mensal"), _linha_de_plano("price_anual")])
    assert [(x["amount_cents"], x["paid_months"]) for x in _comissoes(ind.uid)] == [(1533, 12)]


def test_t1c_dahlia_retrieve_falha_cai_em_12_com_aviso(cobrar):
    ind = _indicado(_afiliado())
    cobrar(ind.uid, valor=4990, linhas=[_linha_de_plano("price_que_a_stripe_nao_devolve")])
    assert [(x["amount_cents"], x["paid_months"]) for x in _comissoes(ind.uid)] == [(416, 12)]
    with get_conn() as conn:
        avisos = conn.execute("select details from system_event_logs where event_type = %s and user_id = %s",
                              ("affiliate_commission_interval_unknown", ind.uid)).fetchall()
    assert [a["details"] for a in avisos] == [{"price_ids": ["price_que_a_stripe_nao_devolve"]}]  # o da linha


def test_t1c_price_expandido_sem_recorrencia_e_avulso_sem_consultar(cobrar):
    """Formato antigo com o price expandido e `recurring: null`: avulso. Sem `prices` no fake, um
    `retrieve` falharia e a 1ª mensal pagaria 1/12."""
    ind = _indicado(_afiliado())
    avulsa = {"amount": 500, "pricing": {"price_details": {"price": "price_avulso"}}, "discount_amounts": [],
              "price": {"id": "price_avulso", "recurring": None}, "plan": None}
    cobrar(ind.uid, valor=2490, linhas=[avulsa, _linha_de_plano("price_desconhecido", "month")])
    assert [(x["amount_cents"], x["paid_months"]) for x in _comissoes(ind.uid)] == [(2490, 1)]
    assert _avisos_de_intervalo(ind.uid) == 0


def _conta_retrieve(cobrar):
    chamadas = []
    real = cobrar.fake.Price.retrieve

    class _Price:
        @staticmethod
        def retrieve(pid):
            chamadas.append(pid)
            return real(pid)

    cobrar.fake.Price = _Price
    return chamadas


@pytest.mark.parametrize("caso", ["sem_referral", "ja_decidido"])
def test_t1d_cliente_que_nao_pode_gerar_nao_consulta_a_stripe_nem_avisa(cobrar, caso):
    """Troca de plano de qualquer cliente traz linha com price diferente do da assinatura: o divisor
    só se calcula para quem ainda pode gerar."""
    chamadas = _conta_retrieve(cobrar)
    if caso == "sem_referral":
        uid = _conta(plano=False).uid
    else:
        uid = _indicado(_afiliado()).uid
        cobrar(uid, valor=4990)
        assert _marca(uid)
    cobrar(uid, valor=19900, linhas=[_linha_de_plano("price_de_outra_troca")])
    assert (chamadas, _avisos_de_intervalo(uid)) == ([], 0)


def test_t1d_positivo_indicado_que_ainda_pode_gerar_consulta(cobrar):
    chamadas = _conta_retrieve(cobrar)
    cobrar.fake.prices["price_anual"] = _price("price_anual", "year")
    ind = _indicado(_afiliado())
    cobrar(ind.uid, valor=19900, linhas=[_linha_de_plano("price_anual")])
    assert chamadas == ["price_anual"] and [x["amount_cents"] for x in _comissoes(ind.uid)] == [1658]


def test_t1c_dahlia_linha_avulsa_nao_conta(cobrar):
    cobrar.fake.prices["price_avulso"] = {"id": "price_avulso", "recurring": None}
    ind = _indicado(_afiliado())
    cobrar(ind.uid, valor=2490, linhas=[_linha_de_plano("price_avulso"), _linha_de_plano("price_desconhecido")])
    assert [(x["amount_cents"], x["paid_months"]) for x in _comissoes(ind.uid)] == [(2490, 1)]


@pytest.mark.parametrize("bps,esperado", [(0, []), (3333, [(1663, 3333, 4990)]), (10_000, [(4990, 10_000, 4990)])],
                         ids=["zero", "intermediario", "cem"])
def test_t2_percentual_no_cartao_grava_bps_e_base(cobrar, bps, esperado):
    ind = _indicado(_afiliado(bps))
    fatura = cobrar(ind.uid, valor=4990)
    linhas = [(x["amount_cents"], x["commission_bps"], x["invoice_amount_cents"]) for x in _comissoes(ind.uid)]
    assert (linhas, _marca(ind.uid)) == (esperado, fatura)


def _linha(price, amount, *descontos):
    return {"amount": amount, "pricing": {"price_details": {"price": price}},
            "discount_amounts": [{"amount": d} for d in descontos]}


@pytest.mark.parametrize("com_extra", [True, False], ids=["cupom_e_caderno", "positivo_sem_extras"])
def test_t3_base_e_o_plano_liquido(cobrar, com_extra):
    sub = _fake_sub("active", "price_plano")
    sub["metadata"] = {"ebook_price": "price_caderno"} if com_extra else {}
    cobrar.fake.subs["sub_afil"] = sub
    ind = _indicado(_afiliado())
    linhas = [_linha("price_caderno", 990, 495), _linha("price_plano", 1990, 995)] if com_extra else []
    cobrar(ind.uid, valor=1490, linhas=linhas)
    base = 995 if com_extra else 1490
    assert [(x["invoice_amount_cents"], x["amount_cents"]) for x in _comissoes(ind.uid)] == [(base, base)]


# ── T4, T4b, T5: trial, falha, cancelamento e cobranças seguintes ───────────

def test_t4a_trial_nao_decide_e_a_primeira_paga_depois_gera(cobrar):
    ind = _indicado(_afiliado())
    cobrar(ind.uid, valor=0)
    assert (_comissoes(ind.uid), _marca(ind.uid)) == ([], None)
    fatura = cobrar(ind.uid, valor=1990)
    assert [x["stripe_invoice_id"] for x in _comissoes(ind.uid)] == [fatura] and _marca(ind.uid) == fatura


def test_t4a_funcao_ignora_valor_zerado():
    ind = _indicado(_afiliado())
    assert record_commission_for_invoice(ind.uid, f"in_{uuid.uuid4().hex[:12]}", 0) is None
    assert (_comissoes(ind.uid), _marca(ind.uid)) == ([], None)


def test_t4b_cancelado_no_trial_nao_decide_e_reassinatura_gera_uma(cobrar):
    ind = _indicado(_afiliado())
    cobrar(ind.uid, valor=0)
    _evento(cobrar, "customer.subscription.deleted",
            {"id": "sub_afil", "metadata": {"finbot_user_id": str(ind.uid)}, "status": "canceled"}, _T_LIFE + 500)
    assert (_comissoes(ind.uid), _marca(ind.uid)) == ([], None)
    primeira = cobrar(ind.uid, valor=1990)
    cobrar(ind.uid, valor=1990)  # renovação dela
    assert [x["stripe_invoice_id"] for x in _comissoes(ind.uid)] == [primeira]


def test_t4c_falha_nao_gera_e_a_retentativa_paga_gera_uma(cobrar):
    ind = _indicado(_afiliado())
    fatura = f"in_{uuid.uuid4().hex[:12]}"
    _evento(cobrar, "invoice.payment_failed",
            {"id": fatura, "metadata": {"finbot_user_id": str(ind.uid)}, "subscription": "sub_afil",
             "amount_due": 1990, "attempt_count": 1}, _T_LIFE - 100)
    assert (_comissoes(ind.uid), _marca(ind.uid)) == ([], None)
    cobrar(ind.uid, fatura=fatura, valor=1990)
    cobrar(ind.uid, fatura=fatura, valor=1990, tipo="invoice.payment_succeeded")
    assert [x["stripe_invoice_id"] for x in _comissoes(ind.uid)] == [fatura]


def test_t5_renovacao_nova_assinatura_e_reativacao_nao_geram(cobrar):
    ind = _indicado(_afiliado())
    primeira = cobrar(ind.uid, valor=1990)
    cobrar(ind.uid, valor=1990)                   # renovação
    cobrar.fake.subs["sub_afil"] = _fake_sub("active", interval="year")
    cobrar(ind.uid, valor=19900)                  # nova assinatura, anual
    cobrar.fake.subs["sub_afil"] = _fake_sub("active")
    cobrar(ind.uid, valor=4990)                   # reativação
    assert [x["stripe_invoice_id"] for x in _comissoes(ind.uid)] == [primeira]


def test_t5b_estorno_total_da_primeira_nao_libera_a_renovacao(cobrar):
    from db.affiliate_reviews import registrar_reembolso

    ind = _indicado(_afiliado())
    primeira = cobrar(ind.uid, valor=4990)
    registrar_reembolso(primeira, "total", 4990, AGORA)
    cobrar(ind.uid, valor=4990)
    assert [(x["stripe_invoice_id"]) for x in _comissoes(ind.uid)] == [primeira]


def test_t5c_pagou_cancelou_e_reassinou_nao_gera(cobrar):
    ind = _indicado(_afiliado())
    primeira = cobrar(ind.uid, valor=4990)
    _evento(cobrar, "customer.subscription.deleted",
            {"id": "sub_afil", "metadata": {"finbot_user_id": str(ind.uid)}, "status": "canceled"}, _T_LIFE + 500)
    cobrar(ind.uid, valor=4990)  # a assinatura nova, paga
    assert [x["stripe_invoice_id"] for x in _comissoes(ind.uid)] == [primeira]


# ── T6, T7, T8: o que encerra a elegibilidade, e a duplicidade ──────────────

def test_t6_zero_encerra_e_subir_o_percentual_nao_reabre(cobrar, monkeypatch):
    c = _afiliado(0)
    ind = _indicado(c)
    primeira = cobrar(ind.uid, valor=4990)
    _set_bps(_admin(monkeypatch), c, 10_000)
    cobrar(ind.uid, fatura=primeira, valor=4990, tipo="invoice.payment_succeeded")  # reenvio da 1ª
    cobrar(ind.uid, valor=4990)                                                     # renovação
    assert (_comissoes(ind.uid), _marca(ind.uid)) == ([], primeira)


def test_t6_positivo_cem_desde_o_inicio_gera(cobrar):
    ind = _indicado(_afiliado(10_000))
    cobrar(ind.uid, valor=4990)
    assert [x["amount_cents"] for x in _comissoes(ind.uid)] == [4990]


def test_t7_desativado_encerra_mesmo_depois_de_reativado(cobrar):
    c = _afiliado()
    ind = _indicado(c)
    assert set_affiliate_status(c.aff["id"], "disabled")
    primeira = cobrar(ind.uid, valor=4990)
    assert set_affiliate_status(c.aff["id"], "active")
    cobrar(ind.uid, fatura=primeira, valor=4990, tipo="invoice.payment_succeeded")
    cobrar(ind.uid, valor=4990)
    assert (_comissoes(ind.uid), _marca(ind.uid)) == ([], primeira)


def test_t8_paid_succeeded_e_reenvio_geram_uma(cobrar):
    ind = _indicado(_afiliado())
    fatura = cobrar(ind.uid, valor=4990)
    cobrar(ind.uid, fatura=fatura, valor=4990, tipo="invoice.payment_succeeded")
    cobrar(ind.uid, fatura=fatura, valor=4990)
    assert [x["stripe_invoice_id"] for x in _comissoes(ind.uid)] == [fatura]


# ── T9, T10: concorrência ────────────────────────────────────────────────────

def test_t9a_mesma_fatura_em_duas_threads():
    c = _afiliado()
    for _ in range(10):
        ind = _indicado(c)
        fatura = f"in_{uuid.uuid4().hex[:12]}"
        largada = threading.Barrier(2, timeout=10)

        def comissao():
            largada.wait()
            return record_commission_for_invoice(ind.uid, fatura, 4990, AGORA)

        with ThreadPoolExecutor(2) as pool:
            [f.result(timeout=15) for f in [pool.submit(comissao), pool.submit(comissao)]]
        assert len(_comissoes(ind.uid)) == 1


def test_t9b_duas_faturas_do_mesmo_cliente_em_paralelo_geram_exatamente_uma():
    """Pagas no mesmo segundo, cada uma com a linha de pagamento já gravada (como no webhook)."""
    c = _afiliado()
    contagens = []
    for _ in range(40):
        ind = _indicado(c)
        faturas = [f"in_{uuid.uuid4().hex[:12]}" for _ in range(2)]
        for f in faturas:
            assert registrar_pagamento(ind.uid, "stripe", f, 4990, AGORA)
        largada = threading.Barrier(2, timeout=10)

        def comissao(f):
            largada.wait()
            return record_commission_for_invoice(ind.uid, f, 4990, AGORA)

        with ThreadPoolExecutor(2) as pool:
            [x.result(timeout=15) for x in [pool.submit(comissao, f) for f in faturas]]
        contagens.append(len(_comissoes(ind.uid)))
    assert set(contagens) == {1}, contagens


def _esperando(n):
    """`n` conexões deste banco paradas em lock. Com prazo: sem a trava ninguém para."""
    fim = time.monotonic() + 10
    while time.monotonic() < fim:
        with get_conn() as conn:
            achadas = conn.execute("select count(*) as n from pg_stat_activity where datname = current_database()"
                                   " and wait_event_type = 'Lock'").fetchone()["n"]
        if achadas >= n:
            return
        time.sleep(0.05)
    raise AssertionError(f"esperava {n} conexões em lock")


@pytest.mark.parametrize("quem", ["indicado", "afiliado"])
def test_t10_exclusao_concorrente_sem_deadlock(quem):
    """A comissão segura a FK do indicado e do afiliado (`key share`) e pede a referral por último.
    Com a referral segura por um terceiro, a exclusão chega no meio: ela espera a comissão, nunca
    o contrário. Marcador antes do insert (ou `for update` na referral) fecha o ciclo."""
    c = _afiliado()
    ind = _indicado(c)
    fatura = f"in_{uuid.uuid4().hex[:12]}"
    with ThreadPoolExecutor(2) as pool, get_conn() as segura:
        segura.execute("select id from affiliate_referrals where referred_user_id = %s for share", (ind.uid,))
        try:
            webhook = pool.submit(record_commission_for_invoice, ind.uid, fatura, 4990, AGORA)
            _esperando(1)
            exclusao = pool.submit(db.delete_user_data, ind.uid if quem == "indicado" else c.uid)
            _esperando(2)
        finally:
            segura.commit()
        erros = [webhook.exception(timeout=30), exclusao.exception(timeout=30)]
    assert not any(isinstance(e, psycopg.errors.DeadlockDetected) for e in erros), erros
    assert erros == [None, None], erros
    with get_conn() as conn:
        assert conn.execute("select count(*) as n from users where id = %s",
                            (ind.uid if quem == "indicado" else c.uid,)).fetchone()["n"] == 0


# ── T11: migração única ──────────────────────────────────────────────────────

def test_t11_migracao_roda_uma_vez_e_preserva_o_historico(monkeypatch):
    c = _afiliado()
    ind = _indicado(c)
    fatura = f"in_{uuid.uuid4().hex[:12]}"
    with get_conn() as conn:
        conn.execute("alter table affiliate_commissions drop column commission_bps, drop column paid_months")
        conn.execute("alter table affiliate_referrals drop column first_charge_ref")
        conn.execute("alter table affiliates alter column commission_bps set default 1000")
        conn.execute("update affiliates set commission_bps = 1000 where id = %s", (c.aff["id"],))
        conn.execute("insert into affiliate_commissions(affiliate_id, referred_user_id, stripe_invoice_id,"
                     " invoice_amount_cents, amount_cents, available_at) values (%s, %s, %s, 4990, 499, now())",
                     (c.aff["id"], ind.uid, fatura))
        conn.commit()
    db.init_db()
    linha = _comissoes(ind.uid)
    assert [(x["amount_cents"], x["commission_bps"], x["paid_months"]) for x in linha] == [(499, 1000, 1)]
    assert get_affiliate_by_user(c.uid)["commission_bps"] == 10_000
    _set_bps(_admin(monkeypatch), c, 2500)
    db.init_db()
    assert get_affiliate_by_user(c.uid)["commission_bps"] == 2500
    assert _comissoes(ind.uid) == linha
    with get_conn() as conn:
        padrao = conn.execute("select column_default from information_schema.columns where table_name = 'affiliates'"
                              " and column_name = 'commission_bps'").fetchone()["column_default"]
    assert padrao == "10000"


JOIN_ANTIGO = ("select r.affiliate_id from affiliate_referrals r join affiliates a on a.id = r.affiliate_id"
               " where r.referred_user_id = %s")
COMISSAO_ANTIGA = ("insert into affiliate_commissions(affiliate_id, referred_user_id, stripe_invoice_id,"
                   " invoice_amount_cents, amount_cents, available_at) values (%s, %s, %s, 4990, 499, now())")


def _sem_as_colunas_novas():
    with get_conn() as conn:
        conn.execute("alter table affiliate_commissions drop column commission_bps, drop column paid_months")
        conn.execute("alter table affiliate_referrals drop column first_charge_ref")
        conn.commit()


def _ate(cond, prazo=10):
    fim = time.monotonic() + prazo
    while time.monotonic() < fim:
        if cond():
            return
        time.sleep(0.05)
    raise AssertionError("prazo esgotado")


def _migracao_na_trava():
    with get_conn() as conn:
        return conn.execute("select count(*) as n from pg_stat_activity where datname = current_database()"
                            " and state = 'active' and pid <> pg_backend_pid()"
                            " and query ilike '%%lock table affiliate%%'").fetchone()["n"] > 0


def _em_lock():
    with get_conn() as conn:
        return conn.execute("select count(*) as n from pg_stat_activity where datname = current_database()"
                            " and wait_event_type = 'Lock'").fetchone()["n"]


def test_t11b_migracao_com_dois_webhooks_antigos_em_voo_sem_deadlock():
    """Deploy com dois webhooks do código antigo no meio (leram referral e afiliado, vão gravar a comissão).
    `lock table` na fila travaria `affiliates` e esperaria a referral que o 2º segura, enquanto ele espera
    `affiliates`: deadlock e comissão perdida. Com `nowait` em laço a migração nunca segura tabela esperando."""
    c = _afiliado()
    ind1, ind2 = _indicado(c), _indicado(c)
    _sem_as_colunas_novas()
    erros, leu, segue = {}, threading.Event(), threading.Event()

    def webhook2():
        with get_conn() as w2:
            try:
                w2.execute(JOIN_ANTIGO, (ind2.uid,))
                leu.set()
                segue.wait(30)
                w2.execute(COMISSAO_ANTIGA, (c.aff["id"], ind2.uid, f"in_{uuid.uuid4().hex[:12]}"))
                w2.commit()
            except psycopg.Error as e:
                w2.rollback()
                erros["w2"] = e
                leu.set()

    with ThreadPoolExecutor(2) as pool, get_conn() as w1:
        w1.execute(JOIN_ANTIGO, (ind1.uid,))
        migracao = pool.submit(db.init_db)
        _ate(_migracao_na_trava)
        w2 = pool.submit(webhook2)
        _ate(lambda: leu.is_set() or _em_lock() >= 2)
        try:
            w1.execute(COMISSAO_ANTIGA, (c.aff["id"], ind1.uid, f"in_{uuid.uuid4().hex[:12]}"))
            w1.commit()
        except psycopg.Error as e:
            w1.rollback()
            erros["w1"] = e
        segue.set()
        w2.result(timeout=60)
        erros["migracao"] = migracao.exception(timeout=60)
    assert erros == {"migracao": None}, erros
    with get_conn() as conn:
        bps = conn.execute("select commission_bps from affiliate_commissions where affiliate_id = %s",
                           (c.aff["id"],)).fetchall()
    assert [x["commission_bps"] for x in bps] == [10_000, 10_000]  # migrou depois dos dois


def test_t11b_migracao_com_cadastro_por_link_em_voo_sem_deadlock():
    """`record_referral` lê o afiliado e grava a referral: é o par que derruba a ordem inversa."""
    c = _afiliado()
    novo = _conta(plano=False)
    _sem_as_colunas_novas()
    with ThreadPoolExecutor(1) as pool, get_conn() as cadastro:
        cadastro.execute("select id from affiliates where id = %s", (c.aff["id"],))
        migracao = pool.submit(db.init_db)
        _ate(_migracao_na_trava)
        try:
            cadastro.execute("insert into affiliate_referrals(affiliate_id, referred_user_id, code_used)"
                             " values (%s, %s, %s)", (c.aff["id"], novo.uid, c.aff["code"]))
            cadastro.commit()
            erro = None
        except psycopg.Error as e:
            cadastro.rollback()
            erro = e
        assert (erro, migracao.exception(timeout=60)) == (None, None)


def test_t11c_tabelas_ocupadas_alem_do_teto_derrubam_o_boot_sem_estado_parcial():
    """O teto real é de ~8 s (cabe no `STARTUP_STEP_TIMEOUT` de 12 s); aqui o mesmo bloco roda com 3 tentativas. Pular a migração não é opção:
    o código novo lê as colunas. O erro sobe, e nada do bloco fica gravado."""
    bloco = next(b for b in re.findall(r'"""(\s*do \$\$ begin.*?end \$\$\s*)"""', Path(schema.__file__).read_text(), re.S)
                 if "lock table affiliates" in b)
    teto = re.search(r"for tentativa in 1\.\.(\d+) loop", bloco).group(1)
    assert int(teto) * 0.05 < 12, "o laço tem de caber no STARTUP_STEP_TIMEOUT do lifespan"
    c = _afiliado()
    _sem_as_colunas_novas()
    try:
        with get_conn() as leitor, get_conn() as boot:
            leitor.execute("select 1 from affiliates limit 1")
            with pytest.raises(psycopg.errors.LockNotAvailable, match="o boot para aqui"):
                boot.execute(bloco.replace(f"1..{teto} loop", "1..3 loop").replace(f"tentativa = {teto}", "tentativa = 3"))
            boot.rollback()
            leitor.rollback()
        with get_conn() as conn:
            colunas = conn.execute("select attname from pg_attribute where attnum > 0 and not attisdropped and attname"
                                   " in ('commission_bps', 'paid_months', 'first_charge_ref') and attrelid in"
                                   " ('affiliate_commissions'::regclass, 'affiliate_referrals'::regclass)").fetchall()
        assert colunas == [] and get_affiliate_by_user(c.uid)["commission_bps"] == 10_000
    finally:
        db.init_db()


# ── T12, T13, T14: a rota do admin ───────────────────────────────────────────

URL = "/admin/api/affiliates/{}/commission"


def test_t12_so_admin_altera(monkeypatch):
    c = _afiliado(1000)
    url = URL.format(c.aff["id"])
    admin = _admin(monkeypatch)  # antes: sem admin configurado a rota dá 503 para todos
    anonimo = TestClient(dashboard.app, base_url="https://testserver")
    assert anonimo.post(url, headers=csrf(anonimo), json={"commission_bps": 9000}).status_code == 401
    usuario = TestClient(dashboard.app, base_url="https://testserver")
    assert usuario.post(url, headers={**c.h, **csrf(usuario)}, json={"commission_bps": 9000}).status_code in (401, 403)
    admin.cookies.delete(dashboard.CSRF_COOKIE_NAME)
    assert admin.post(url, json={"commission_bps": 9000}).status_code == 403
    assert get_affiliate_by_user(c.uid)["commission_bps"] == 1000
    # positivo: o admin com CSRF grava
    assert admin.post(url, headers=csrf(admin), json={"commission_bps": 9000}).status_code == 200
    assert get_affiliate_by_user(c.uid)["commission_bps"] == 9000


@pytest.mark.parametrize("corpo,status,gravado", [
    ({"commission_bps": 0}, 200, 0), ({"commission_bps": 50}, 200, 50), ({"commission_bps": 10_000}, 200, 10_000),
    ({}, 422, 1000), ({"commission_bps": True}, 422, 1000), ({"commission_bps": "+5"}, 422, 1000),
    ({"commission_bps": "1_000"}, 422, 1000), ({"commission_bps": 2500.5}, 400, 1000),
    ({"commission_bps": -1}, 400, 1000), ({"commission_bps": 10_001}, 400, 1000),
], ids=["zero", "cinquenta", "cem", "ausente", "true", "mais", "sublinhado", "decimal", "negativo", "acima"])
def test_t13_validacao_da_edicao(monkeypatch, corpo, status, gravado):
    c = _afiliado(1000)
    admin = _admin(monkeypatch)
    r = admin.post(URL.format(c.aff["id"]), headers=csrf(admin), json=corpo)
    assert r.status_code == status, r.text
    assert get_affiliate_by_user(c.uid)["commission_bps"] == gravado


def test_t13_inexistente(monkeypatch):
    admin = _admin(monkeypatch)
    assert admin.post(URL.format(2_000_000_000), headers=csrf(admin), json={"commission_bps": 10}).status_code == 404


def test_t14_edicao_nao_toca_historico(cobrar, monkeypatch):
    c = _afiliado(1000)
    ind = _indicado(c)
    cobrar(ind.uid, valor=4990)
    with get_conn() as conn:
        antes = conn.execute("select * from affiliate_commissions where referred_user_id = %s", (ind.uid,)).fetchall()
    _set_bps(_admin(monkeypatch), c, 5000)
    with get_conn() as conn:
        depois = conn.execute("select * from affiliate_commissions where referred_user_id = %s", (ind.uid,)).fetchall()
    assert depois == antes and antes[0]["amount_cents"] == 499


# ── T15: histórico de antes do marcador ──────────────────────────────────────

@pytest.mark.parametrize("fonte", ["stripe", "pix"])
def test_t15_pagamento_antigo_sem_marcador_barra_o_cartao(cobrar, fonte):
    ind = _indicado(_afiliado())
    assert registrar_pagamento(ind.uid, fonte, f"antigo_{uuid.uuid4().hex[:8]}", 4990,
                               datetime.now(timezone.utc) - timedelta(days=400))
    cobrar(ind.uid, valor=4990)
    assert (_comissoes(ind.uid), _marca(ind.uid)) == ([], None)


# ── T17: falha da comissão deixa rastro ──────────────────────────────────────

def test_t17_falha_transitoria_da_comissao_deixa_log_com_a_fatura(cobrar, monkeypatch):
    """Pagamento gravado, comissão falha e o webhook responde 200: a renovação já vê o pagamento
    anterior e não gera. O log de erro com a fatura é o que permite ao admin reenviar o evento."""
    real = afiliados._comissao_da_primeira
    chamadas = []

    def falha_uma_vez(*a, **k):
        chamadas.append(a[1])
        if len(chamadas) == 1:
            raise psycopg.errors.DeadlockDetected("simulado")
        return real(*a, **k)

    monkeypatch.setattr(afiliados, "_comissao_da_primeira", falha_uma_vez)
    ind = _indicado(_afiliado())
    primeira = cobrar(ind.uid, valor=4990)
    cobrar(ind.uid, valor=4990)
    with get_conn() as conn:
        erros = conn.execute("select details from system_event_logs where user_id = %s and level = 'error'"
                             " and event_type = 'affiliate_commission_failed'", (ind.uid,)).fetchall()
    assert _comissoes(ind.uid) == []
    assert [e["details"] for e in erros] == [{"invoice_id": primeira, "erro": "DeadlockDetected"}]


def test_t17b_falha_na_conferencia_nao_vira_comissao_nao_registrada(cobrar, monkeypatch):
    """A comissão nasceu; só a conferência na Stripe (A5) falhou e fica para o worker."""
    from core.services import afiliado_reembolso

    def quebra(*a, **k):
        raise psycopg.errors.OperationalError("simulado")

    monkeypatch.setattr(afiliado_reembolso, "conferir_comissao", quebra)
    ind = _indicado(_afiliado())
    fatura = cobrar(ind.uid, valor=4990)
    with get_conn() as conn:
        eventos = conn.execute("select event_type, details from system_event_logs where user_id = %s"
                               " and event_type like 'affiliate_commission%%' order by id", (ind.uid,)).fetchall()
    assert [x["amount_cents"] for x in _comissoes(ind.uid)] == [4990]
    assert [e["event_type"] for e in eventos] == ["affiliate_commission_check_failed", "affiliate_commission_created"]
    assert eventos[0]["details"]["invoice_id"] == fatura


# ── T16: contrato do /me ─────────────────────────────────────────────────────

def test_t16_me_traz_paid_months(cobrar):
    c = _afiliado()
    cobrar.fake.subs["sub_afil"] = _fake_sub("active", interval="year")
    cobrar(_indicado(c).uid, valor=19900)
    assert [(x["paid_months"], x["invoice_amount"], x["amount"]) for x in _me(c)["commissions"]] == [(12, 199.0, 16.58)]


# ── T18: plano em cache × coluna refeita ─────────────────────────────────────

def test_t18_insert_da_comissao_sobrevive_a_coluna_refeita(pool_de):
    """A conexão do pool já preparou o insert (o psycopg prepara depois de 5 execuções) quando uma
    migração refaz uma coluna de `affiliate_commissions` (aqui, como o teste A5 da conferência). Com
    `returning *` o tipo do resultado muda e o Postgres recusa o plano: a comissão não nasceria."""
    c = _afiliado()
    indicados = [_indicado(c) for _ in range(7)]
    pool = pool_de(1)
    for ind in indicados[:6]:
        assert record_commission_for_invoice(ind.uid, f"in_{uuid.uuid4().hex[:12]}", 4990, AGORA)
    with psycopg.connect(pool.conninfo, autocommit=True) as outra:
        outra.execute("alter table affiliate_commissions drop column payment_check_pending")
        outra.execute("alter table affiliate_commissions add column payment_check_pending boolean not null default false")
    linha = record_commission_for_invoice(indicados[6].uid, f"in_{uuid.uuid4().hex[:12]}", 4990, AGORA)
    assert linha and (linha["amount_cents"], linha["paid_months"]) == (4990, 1)

