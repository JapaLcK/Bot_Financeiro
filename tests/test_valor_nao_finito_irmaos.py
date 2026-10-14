"""Irmãos de `test_valor_nao_finito_dinheiro.py`: orçamento, gasto fixo, caixinha,
investimento, pagar conta e a entrada rápida. Mesmo critério (`utils_text.valor_valido`),
pelas portas reais: tool da IA, rota do painel (JSON cru com NaN/Infinity), conversa
do WhatsApp e banco. O que mede é o estado gravado."""
from __future__ import annotations

import os

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

os.environ.setdefault("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())

import db
import frontend.finance_bot_websocket_custom as dashboard
from conftest import promote_to_pro, usuario_pagante

BRUTOS = ["NaN", "Infinity", "1e308"]
HUGE = "1" * 301   # parse_money -> 1e300


def _cliente(uid):
    promote_to_pro(uid)
    c = TestClient(dashboard.app)
    c.cookies.set(dashboard.AUTH_COOKIE_NAME, dashboard._make_jwt(uid, "v@t.com"))
    c.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, dashboard.make_dashboard_token(uid, hours=1))
    c.cookies.set(dashboard.CSRF_COOKIE_NAME, "tok")
    return c, {dashboard.CSRF_HEADER_NAME: "tok", "Content-Type": "application/json"}


def _linhas(sql, uid):
    with db.get_conn() as c, c.cursor() as cur:
        cur.execute(sql, (uid,))
        return cur.fetchall()


def _saldo(uid):
    r = _linhas("select balance from accounts where user_id=%s", uid)
    return float(r[0]["balance"]) if r else 0.0


def _com_saldo(valor=1000.0):
    uid = usuario_pagante()
    db.add_launch_and_update_balance(user_id=uid, tipo="receita", valor=valor, alvo="x", nota="x")
    return uid


# ── orçamento ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("v", ["nan", float("nan"), float("inf"), 1e308, 10**400])
def test_tool_orcamento_recusa_valor_nao_finito(v):
    from core.services.ai_chat.tools.budgets import _set_budget_execute

    uid = usuario_pagante()
    msg = _set_budget_execute(uid, {"categoria": "mercado", "budget": v})

    assert "inválido" in msg and "criado" not in msg, msg
    assert _linhas("select 1 from category_budgets where user_id=%s", uid) == []


def test_tool_orcamento_valor_normal_segue_igual():
    from core.services.ai_chat.tools.budgets import _set_budget_execute

    uid = usuario_pagante()
    msg = _set_budget_execute(uid, {"categoria": "mercado", "budget": 500})

    assert "R$ 500.00" in msg and "criado" in msg, msg
    assert [float(r["b"]) for r in _linhas("select budget b from category_budgets where user_id=%s", uid)] == [500.0]


# ── gasto fixo ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bruto", BRUTOS)
def test_painel_gasto_fixo_recusa_valor_nao_finito(bruto):
    uid = usuario_pagante()
    c, h = _cliente(uid)

    r = c.post(f"/recurring-expenses/{uid}", headers=h,
               content='{"name":"Luz","amount":%s,"category":"moradia","due_day":10,"payment_type":"account"}' % bruto)

    assert r.status_code == 400, r.text
    assert _linhas("select 1 from recurring_expenses where user_id=%s", uid) == []


def test_painel_gasto_fixo_valor_normal_segue_igual():
    uid = usuario_pagante()
    c, h = _cliente(uid)

    r = c.post(f"/recurring-expenses/{uid}", headers=h,
               content='{"name":"Luz","amount":120.5,"category":"moradia","due_day":10,"payment_type":"account"}')

    assert r.status_code == 200, r.text
    assert [float(x["a"]) for x in _linhas("select amount a from recurring_expenses where user_id=%s", uid)] == [120.5]


@pytest.mark.parametrize("v", [float("nan"), float("inf"), 1e308])
def test_banco_gasto_fixo_recusa_valor_nao_finito_na_edicao(v):
    from db.recurring import create_recurring_expense, update_recurring_expense

    uid = usuario_pagante()
    rec = create_recurring_expense(uid, "Luz", 100.0, "moradia", 10, "account")
    with pytest.raises(ValueError, match="VALOR_INVALIDO"):
        update_recurring_expense(uid, rec["id"], amount=v)
    assert [float(x["a"]) for x in _linhas("select amount a from recurring_expenses where user_id=%s", uid)] == [100.0]


# ── caixinha ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("acao", ["deposit", "withdraw"])
@pytest.mark.parametrize("bruto", BRUTOS)
def test_painel_caixinha_recusa_valor_nao_finito(acao, bruto):
    uid = _com_saldo()
    db.create_pocket(uid, "viagem")
    c, h = _cliente(uid)

    r = c.post(f"/pockets/{uid}/viagem/{acao}", headers=h, content='{"amount":%s}' % bruto)

    assert r.status_code == 400, r.text
    assert _saldo(uid) == 1000.0


def test_painel_caixinha_deposito_normal_segue_igual():
    uid = _com_saldo()
    db.create_pocket(uid, "viagem")
    c, h = _cliente(uid)

    r = c.post(f"/pockets/{uid}/viagem/deposit", headers=h, content='{"amount":100}')

    assert r.status_code == 200, r.text
    assert _saldo(uid) == 900.0


@pytest.mark.parametrize("v", ["nan", float("nan"), float("inf"), 1e308, 10**400])
def test_tools_caixinha_e_investimento_dizem_valor_invalido(v):
    from core.services.ai_chat.tools.investments import _investment_deposit_execute, _investment_withdraw_execute
    from core.services.ai_chat.tools.pockets import _pocket_deposit_execute, _pocket_withdraw_execute

    uid = _com_saldo()
    db.create_pocket(uid, "viagem")
    db.create_investment(uid, "cdb", 1.0, "monthly")

    for f, args in ((_pocket_deposit_execute, {"pocket_name": "viagem", "amount": v}),
                    (_pocket_withdraw_execute, {"pocket_name": "viagem", "amount": v}),
                    (_investment_deposit_execute, {"name": "cdb", "amount": v}),
                    (_investment_withdraw_execute, {"name": "cdb", "amount": v})):
        msg = f(uid, args)
        # sem "tenta de novo": o valor é que está errado, repetir não adianta
        assert "inválido" in msg and "novo" not in msg, (f.__name__, v, msg)
    assert _saldo(uid) == 1000.0


def test_tool_caixinha_deposito_normal_segue_igual():
    from core.services.ai_chat.tools.pockets import _pocket_deposit_execute

    uid = _com_saldo()
    db.create_pocket(uid, "viagem")

    assert "Depositado" in _pocket_deposit_execute(uid, {"pocket_name": "viagem", "amount": 100})
    assert _saldo(uid) == 900.0


@pytest.mark.parametrize("v", [float("nan"), float("inf"), 1e308])
def test_banco_caixinha_e_investimento_recusam_valor_nao_finito(v):
    uid = _com_saldo()
    db.create_pocket(uid, "viagem")
    db.create_investment(uid, "cdb", 1.0, "monthly")
    with pytest.raises(ValueError, match="AMOUNT_INVALID"):
        db.pocket_deposit_from_account(uid, "viagem", v)
    with pytest.raises(ValueError, match="AMOUNT_INVALID"):
        db.pocket_withdraw_to_account(uid, "viagem", v)
    with pytest.raises(ValueError, match="AMOUNT_INVALID"):
        db.investment_deposit_from_account(uid, "cdb", v, "x")
    with pytest.raises(ValueError, match="AMOUNT_INVALID"):
        db.investment_withdraw_to_account(uid, "cdb", v)


@pytest.mark.parametrize("acao", ["deposit", "withdraw"])
@pytest.mark.parametrize("bruto", BRUTOS)
def test_painel_investimento_recusa_valor_nao_finito(acao, bruto):
    uid = _com_saldo()
    db.create_investment(uid, "cdb", 1.0, "monthly")
    c, h = _cliente(uid)

    r = c.post(f"/investments/{uid}/{acao}", headers=h, content='{"name":"cdb","amount":%s}' % bruto)

    assert r.status_code == 400, r.text
    assert _saldo(uid) == 1000.0


# ── pagar conta e entrada rápida ───────────────────────────────────────────

def test_banco_pagar_conta_recusa_valor_acima_do_limite_tecnico():
    from test_bill_amount_pending import _monta_conta_variavel
    import db.bills as B

    uid = usuario_pagante()
    conta = _monta_conta_variavel(uid)
    with pytest.raises(ValueError, match="VALOR_INVALIDO"):
        B.mark_bill_paid(uid, int(conta["id"]), 1e300, metodo="carteira")

    assert B.list_bills(uid, include_paid=True)[0]["status"] == "pending"
    assert _saldo(uid) == 0.0


def test_whatsapp_pagar_conta_com_numero_absurdo_nao_da_erro_interno():
    from test_bill_amount_pending import _diga, _monta_conta_variavel

    uid = usuario_pagante()
    _monta_conta_variavel(uid)
    resposta = _diga(uid, f"paguei a luz {HUGE}")
    assert "erro interno" not in resposta.lower(), resposta
    assert _saldo(uid) == 0.0


def test_entrada_rapida_recusa_valor_absurdo_e_aceita_o_normal():
    from core.services.quick_entry import handle_quick_entry

    uid = usuario_pagante()
    r = handle_quick_entry(uid, f"gastei {HUGE} no mercado")
    assert r is not None and "inválido" in r.text.lower(), r
    assert _linhas("select 1 from launches where user_id=%s", uid) == []
    assert _saldo(uid) == 0.0

    ok = handle_quick_entry(uid, "gastei 50 no mercado")
    assert ok is not None and "registrada" in ok.text.lower(), ok
    assert _saldo(uid) == -50.0
