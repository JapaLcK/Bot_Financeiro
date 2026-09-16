"""Toda superfície que MOSTRA ou AUTORIZA contra a Carteira fala o mesmo número.

A rodada 1 corrigiu `get_consolidated_balance` e o snapshot do dashboard e
deixou quatro leitores crus de pé — "achei um caso" tratado como "resolvi a
categoria" (CLAUDE.md §2). Pior: a divergência era INTRODUZIDA pelo diff, não
herdada. Medido antes do conserto, com Carteira exibida de 100,00 (cru 50 +
fusão 50):

    GET /bills            -> balance 50,00   (o dashboard mostrava 100,00)
    POST .../pay amount=80 -> 400 "Saldo insuficiente. Saldo atual: R$ 50.00"

O `/bills` não é cosmético: `frontend/dashboard.js:9690` faz
`payBillState.balance = Number(data.balance)` e pinta em `#pay-bill-balance` —
é o modal "Pagar fatura" exibindo o `R$ -1,00` que este PR existe para matar.

Controle negativo do GRUPO (§3): zerar `MERGED_WALLET_DELTA_SQL` tem de deixar
vermelhos `bills_mostra`, `pagar_fatura_autoriza`, `export` e `cashflow`.
Controle positivo: `pagar_fatura_continua_recusando_o_que_nao_cabe` — a guarda
não pode virar peneira.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

import db
from utils_text import fmt_brl as _brl
from utils_date import today_tz

from tests._fusao_of_helpers import (  # noqa: F401 (uid_pro/ia_fora são fixtures)
    conecta_banco, consolidado, ia_fora, manda, saldo_bruto, sincroniza, tx,
    uid_pro,
)


def _funde_cinquenta(uid: int) -> int:
    """Carteira exibida 100,00 = cru 50,00 + os 50,00 do gasto fundido.

    Valor gordo de propósito: com 1 real a guarda da fatura não teria como
    recusar nada, e o caso do pagamento perderia o sentido.
    """
    hoje = today_tz()
    conexao = conecta_banco(uid, "1000.00")
    db.add_launch_and_update_balance(uid, "receita", 100, None, "seed")
    manda(uid, "gastei 50 no mercado")
    sincroniza(conexao, uid, "950.00", [tx(uid, "-50.00", hoje, "MERCADO")])
    rep = db.import_open_finance_launches(uid, conexao)
    assert rep["auto_merged"] == 1, rep
    assert saldo_bruto(uid) == Decimal("50"), "a correção é de LEITURA"
    return conexao


def _fatura_de(uid: int, total: float) -> int:
    """Um cartão com uma fatura aberta de `total`, direto no banco."""
    from datetime import timedelta
    hoje = today_tz()
    card_id = db.create_card(uid, "Nubank", 10, 20)
    if isinstance(card_id, dict):
        card_id = card_id.get("id")
    with db.connection.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """insert into credit_bills
                     (user_id, card_id, period_start, period_end, status, total, paid_amount)
                   values (%s,%s,%s,%s,'open',%s,0) returning id""",
                (uid, int(card_id), hoje - timedelta(days=20), hoje + timedelta(days=5), total),
            )
            bill_id = cur.fetchone()["id"]
        conn.commit()
    return bill_id


def _fonte_carteira(uid: int) -> dict:
    """A opção "Carteira" da pergunta de origem, como o usuário responderia."""
    from core.services import funding
    return [f for f in funding.list_sources(uid) if f["kind"] == funding.CARTEIRA][0]


class _Req:
    """Request mudo: `authorize_dashboard_access` é neutralizado no teste."""


@pytest.fixture
def sem_autorizacao(monkeypatch):
    """As rotas exigem sessão; aqui o alvo é a aritmética, não o portão."""
    from frontend.routes import shared
    import frontend.finance_bot_websocket_custom as mono
    monkeypatch.setattr(shared, "authorize_dashboard_access", lambda *a, **k: None)
    monkeypatch.setattr(mono, "_authorize_dashboard_access", lambda *a, **k: None)


# ── 1. GET /bills: o número do modal "Pagar fatura" ───────────────────────

def test_bills_mostra_a_mesma_carteira_do_dashboard(uid_pro, ia_fora, sem_autorizacao):
    from frontend.routes.cards import list_bills_route
    import frontend.finance_bot_websocket_custom as dashboard

    _funde_cinquenta(uid_pro)
    _fatura_de(uid_pro, 80.0)

    r = asyncio.run(list_bills_route(_Req(), uid_pro))
    snapshot = asyncio.run(dashboard.get_financial_data(uid_pro))

    assert r["balance"] == pytest.approx(100.0), "sem o conserto: 50,00"
    assert r["balance"] == pytest.approx(snapshot["balance"]), \
        "o modal de fatura diverge do dashboard"
    assert r["balance"] == pytest.approx(float(db.get_consolidated_balance(uid_pro)["manual"]))


# ── 2. a guarda do pagamento: recusava o que o dashboard autoriza ─────────

def test_pagar_fatura_autoriza_o_que_o_dashboard_mostra(uid_pro, ia_fora, sem_autorizacao):
    """80,00 cabe na Carteira exibida (100,00) e NÃO no cru (50,00)."""
    from frontend.routes.cards import pay_bill_route, PayBillPayload

    _funde_cinquenta(uid_pro)
    bill_id = _fatura_de(uid_pro, 80.0)

    r = asyncio.run(pay_bill_route(
        _Req(), uid_pro, bill_id, PayBillPayload(amount=80.0)))

    assert r.get("ok") is True, r
    # o pagamento debitou a Carteira de verdade: 50 - 80 no cru
    assert saldo_bruto(uid_pro) == Decimal("-30")


def test_pagar_fatura_continua_recusando_o_que_nao_cabe(uid_pro, ia_fora, sem_autorizacao):
    """POSITIVO: a guarda não virou peneira. 100,01 não cabe em 100,00."""
    from fastapi import HTTPException
    from frontend.routes.cards import pay_bill_route, PayBillPayload

    _funde_cinquenta(uid_pro)
    bill_id = _fatura_de(uid_pro, 500.0)

    with pytest.raises(HTTPException) as e:
        asyncio.run(pay_bill_route(
            _Req(), uid_pro, bill_id, PayBillPayload(amount=100.01)))
    assert e.value.status_code == 400
    assert "insuficiente" in str(e.value.detail).lower(), e.value.detail


# ── 3. export PDF/XLSX: "Saldo atual" ─────────────────────────────────────

def test_export_traz_a_carteira_exibida(uid_pro, ia_fora):
    """Um PDF que contradiz a tela é pior que um PDF sem saldo."""
    from frontend.finance_bot_websocket_custom import _fetch_export_balance

    _funde_cinquenta(uid_pro)

    assert asyncio.run(_fetch_export_balance(uid_pro)) == pytest.approx(100.0), \
        "sem o conserto: 50,00"


# ── 4. cashflow.project, ramo do gate DESLIGADO ───────────────────────────

def test_cashflow_com_gate_desligado_parte_da_carteira_exibida(
        uid_pro, ia_fora, monkeypatch):
    """O gate congela a ORIGEM (carteira), não autoriza projetar do cru."""
    from core.services import cashflow
    from datetime import timedelta

    monkeypatch.setenv("OF_CONSOLIDATED_BALANCE_ENABLED", "0")
    monkeypatch.setenv("OF_CONSOLIDATED_BETA_EMAILS", "ninguem@test.local")
    monkeypatch.setenv("OF_CONSOLIDATED_BETA_USER_IDS", "")
    _funde_cinquenta(uid_pro)

    r = cashflow.project(uid_pro, today_tz() + timedelta(days=1))

    assert r["balance_source"] == "manual", r
    assert r["saldo_atual"] == pytest.approx(100.0), "sem o conserto: 50,00"


# ── 5. adjust_balance_route: os dois campos falam da mesma base ───────────

def test_ajuste_devolve_balance_na_mesma_base_do_delta(uid_pro, ia_fora, sem_autorizacao):
    """Antes: `delta` contra a Carteira exibida e `balance` cru na MESMA
    resposta (medido: json balance=4,0 delta=5,0 | dashboard=5,0)."""
    from frontend.finance_bot_websocket_custom import (
        AdjustBalancePayload, adjust_balance_route,
    )
    import frontend.finance_bot_websocket_custom as dashboard

    _funde_cinquenta(uid_pro)

    r = asyncio.run(adjust_balance_route(
        _Req(), uid_pro, AdjustBalancePayload(target_balance=250)))

    assert r["delta"] == pytest.approx(150.0), r
    snapshot = asyncio.run(dashboard.get_financial_data(uid_pro))
    assert r["balance"] == pytest.approx(snapshot["balance"]), \
        "os dois campos da resposta falavam de bases diferentes"
    assert r["balance"] == pytest.approx(250.0)


# ── 6. mensagem de caixinha (aporte e resgate) ────────────────────────────
#
# Regra aplicada: rótulo `🏦 Conta:` byte a byte, número relido. Copy nova é só
# a da resposta de LANÇAMENTO (`💰 Saldo total:`), e só lá.

def test_mensagem_de_caixinha_fala_o_mesmo_numero_do_dashboard(uid_pro, ia_fora):
    """Medido antes: a mensagem dizia `Conta: 40,00` e o dashboard 90,00 no
    mesmo instante — divergência de 50,00, o valor fundido."""
    import asyncio as _aio
    import frontend.finance_bot_websocket_custom as dashboard
    from core.handlers import pockets as h_pockets

    _funde_cinquenta(uid_pro)
    db.create_pocket(uid_pro, "Viagem")

    # com banco conectado o handler PERGUNTA a origem; o caminho que monta a
    # mensagem é o de depois da resposta "Carteira".
    resp = h_pockets.deposita_com_origem(
        uid_pro, "Viagem", 10, "coloquei 10 na caixinha viagem",
        _fonte_carteira(uid_pro))
    exibido = _aio.run(dashboard.get_financial_data(uid_pro))["balance"]

    assert "🏦 Conta: " in resp, resp
    assert "Saldo total" not in resp, "copy nova é só da resposta de lançamento"
    assert f"🏦 Conta: {_brl(exibido)}" in resp, (resp, exibido)
    assert _brl(exibido) == _brl(90.0), exibido


def test_mensagem_de_resgate_de_caixinha_tambem(uid_pro, ia_fora):
    import asyncio as _aio
    import frontend.finance_bot_websocket_custom as dashboard
    from core.handlers import pockets as h_pockets

    _funde_cinquenta(uid_pro)
    db.create_pocket(uid_pro, "Viagem")
    h_pockets.deposita_com_origem(
        uid_pro, "Viagem", 10, "coloquei 10 na caixinha viagem",
        _fonte_carteira(uid_pro))

    resp = h_pockets.withdraw(uid_pro, "retirei 5 da caixinha viagem",
                              {"pocket_name": "Viagem", "amount": 5})
    exibido = _aio.run(dashboard.get_financial_data(uid_pro))["balance"]

    assert "🏦 Conta: " in resp, resp
    assert f"🏦 Conta: {_brl(exibido)}" in resp, (resp, exibido)
    # e o número é o CORRIGIDO, não o cru: a diferença é o valor fundido
    assert exibido - float(saldo_bruto(uid_pro)) == pytest.approx(50.0)


# ── 7. pagamento de fatura pelo WhatsApp: "Conta agora" ───────────────────

def test_pay_bill_amount_devolve_a_carteira_exibida(uid_pro, ia_fora):
    """`db/cards.py:pay_bill_amount` alimenta `core/handlers/credit.py:133`
    ("Conta agora") e `:2718` ("Saldo da conta") — duas telas, um produtor."""
    from db.cards import pay_bill_amount

    _funde_cinquenta(uid_pro)
    bill_id = _fatura_de(uid_pro, 30.0)
    with db.connection.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select card_id from credit_bills where id=%s", (bill_id,))
            card_id = cur.fetchone()["card_id"]
        conn.commit()

    res = pay_bill_amount(uid_pro, int(card_id), "Nubank", 30.0, bill_id)

    assert float(res["new_balance"]) == pytest.approx(70.0), \
        "sem o conserto: 20,00 (o cru), com o dashboard mostrando 70,00"
    assert float(res["new_balance"]) == pytest.approx(
        float(db.get_consolidated_balance(uid_pro)["manual"]))


# ── 9. rota POST /launches: o TERCEIRO chamador da mesma defasagem ────────

def test_rota_de_lancamento_devolve_a_carteira_depois_da_fusao(uid_pro, ia_fora, sem_autorizacao):
    """`new_balance` é lido antes do `reconcile_manual_launch` da própria rota.
    Contrato de JSON, sem tela hoje (`dashboard.js` só lê `new_balance` em
    `:9889`, que vem da rota de fatura) — mas a mesma severidade do
    `adjust_balance_route`. Medido antes: `new_balance=-1,0 dash=0,0`."""
    import frontend.finance_bot_websocket_custom as mono

    conecta_banco(uid_pro, "113.88",
                  [tx(uid_pro, "-1.00", today_tz(), "PIX ENVIADO BARBARA")])
    db.import_open_finance_launches(uid_pro, _conexao_de(uid_pro))

    r = asyncio.run(mono.create_launch_route(
        _Req(), uid_pro, mono.LaunchCreatePayload(
            tipo="despesa", valor=1.0, nota="Gastei 1 real com a barbara")))

    exibido = asyncio.run(mono.get_financial_data(uid_pro))["balance"]
    assert r["new_balance"] == pytest.approx(exibido), "sem o conserto: -1,0 contra 0,0"
    assert r["new_balance"] == pytest.approx(0.0)


def _conexao_de(uid: int) -> int:
    with db.connection.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select id from open_finance_connections where user_id=%s", (uid,))
            row = cur.fetchone()
        conn.commit()
    return row["id"]
