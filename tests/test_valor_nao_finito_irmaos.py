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

    assert r.status_code == 400 and "Valor inválido" in r.json()["detail"], r.text
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

    assert r.status_code == 400 and "Valor inválido" in r.json()["detail"], r.text
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
    import db.bills as B
    from test_bill_amount_pending import _diga, _monta_conta_variavel

    uid = usuario_pagante()
    _monta_conta_variavel(uid)
    resposta = _diga(uid, f"paguei a luz {HUGE}")
    assert "Não entendi o valor" in resposta and "erro interno" not in resposta.lower(), resposta
    assert _saldo(uid) == 0.0
    assert B.list_bills(uid, include_paid=True)[0]["status"] == "pending"


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


# ── segunda rodada: orçamento do painel, receita fixa, meta da caixinha ────

@pytest.mark.parametrize("bruto", BRUTOS)
def test_painel_orcamento_recusa_valor_nao_finito(bruto):
    uid = usuario_pagante()
    c, h = _cliente(uid)

    r = c.post(f"/budgets/{uid}", headers=h, content='{"categoria":"mercado","budget":%s}' % bruto)

    assert r.status_code == 400, r.text
    assert _linhas("select 1 from category_budgets where user_id=%s", uid) == []


def test_painel_orcamento_valor_normal_segue_igual():
    uid = usuario_pagante()
    c, h = _cliente(uid)

    r = c.post(f"/budgets/{uid}", headers=h, content='{"categoria":"mercado","budget":500}')

    assert r.status_code == 200, r.text
    assert [float(x["b"]) for x in _linhas("select budget b from category_budgets where user_id=%s", uid)] == [500.0]


@pytest.mark.parametrize("bruto", BRUTOS)
def test_painel_receita_fixa_recusa_valor_nao_finito(bruto):
    uid = usuario_pagante()
    c, h = _cliente(uid)

    r = c.post(f"/recurring-incomes/{uid}", headers=h,
               content='{"name":"Salario","amount":%s,"category":"salario","pay_day":5}' % bruto)

    assert r.status_code == 400, r.text
    assert _linhas("select 1 from recurring_incomes where user_id=%s", uid) == []


def test_painel_receita_fixa_valor_normal_segue_igual():
    uid = usuario_pagante()
    c, h = _cliente(uid)

    r = c.post(f"/recurring-incomes/{uid}", headers=h,
               content='{"name":"Salario","amount":3000,"category":"salario","pay_day":5}')

    assert r.status_code == 200, r.text
    assert [float(x["a"]) for x in _linhas("select amount a from recurring_incomes where user_id=%s", uid)] == [3000.0]


@pytest.mark.parametrize("v", [float("nan"), float("inf"), 1e308])
def test_banco_receita_fixa_recusa_valor_nao_finito_na_edicao(v):
    from db.recurring_income import create_recurring_income, update_recurring_income

    uid = usuario_pagante()
    rec = create_recurring_income(uid, "Salario", 3000.0, "salario", 5)
    with pytest.raises(ValueError, match="VALOR_INVALIDO"):
        update_recurring_income(uid, rec["id"], amount=v)
    assert [float(x["a"]) for x in _linhas("select amount a from recurring_incomes where user_id=%s", uid)] == [3000.0]


@pytest.mark.parametrize("campo,bruto", [(c, b) for c in ("target_amount", "interest_rate") for b in BRUTOS])
def test_painel_meta_da_caixinha_recusa_valor_nao_finito(campo, bruto):
    uid = usuario_pagante()
    _lid, pid, _nome = db.create_pocket(uid, "viagem")
    c, h = _cliente(uid)

    r = c.patch(f"/pockets/{uid}/{pid}/meta", headers=h, content='{"%s":%s}' % (campo, bruto))

    assert r.status_code == 400, r.text
    assert _linhas("select target_amount, interest_rate from pockets where user_id=%s", uid)[0][campo] in (None, 1)
    # a tela de metas continua de pé (NaN gravado derrubava o GET com 500 permanente)
    assert c.get(f"/goals/{uid}/status").status_code == 200


def test_painel_meta_da_caixinha_valor_normal_segue_igual():
    uid = usuario_pagante()
    _lid, pid, _nome = db.create_pocket(uid, "viagem")
    c, h = _cliente(uid)

    r = c.patch(f"/pockets/{uid}/{pid}/meta", headers=h, content='{"target_amount":2500}')

    assert r.status_code == 200, r.text
    assert float(_linhas("select target_amount t from pockets where user_id=%s", uid)[0]["t"]) == 2500.0


# ── gasto fixo de valor variável: informado e inválido é recusado ──────────

@pytest.mark.parametrize("bruto", BRUTOS)
def test_painel_gasto_fixo_variavel_com_valor_informado_invalido_e_recusado(bruto):
    uid = usuario_pagante()
    c, h = _cliente(uid)

    r = c.post(f"/recurring-expenses/{uid}", headers=h,
               content='{"name":"Luz","amount":%s,"category":"moradia","due_day":10,"payment_type":"account","variable_amount":true}' % bruto)

    assert r.status_code == 400, r.text
    assert _linhas("select 1 from recurring_expenses where user_id=%s", uid) == []


def test_painel_gasto_fixo_variavel_sem_estimativa_segue_igual():
    uid = usuario_pagante()
    c, h = _cliente(uid)

    r = c.post(f"/recurring-expenses/{uid}", headers=h,
               content='{"name":"Luz","category":"moradia","due_day":10,"payment_type":"account","variable_amount":true}')

    assert r.status_code == 200, r.text
    assert [float(x["a"]) for x in _linhas("select amount a from recurring_expenses where user_id=%s", uid)] == [0.0]


def test_painel_gasto_fixo_edicao_normal_segue_igual():
    from db.recurring import create_recurring_expense

    uid = usuario_pagante()
    rec = create_recurring_expense(uid, "Luz", 100.0, "moradia", 10, "account")
    c, h = _cliente(uid)

    r = c.patch(f"/recurring-expenses/{uid}/{rec['id']}", headers=h, content='{"amount":130}')

    assert r.status_code == 200, r.text
    assert [float(x["a"]) for x in _linhas("select amount a from recurring_expenses where user_id=%s", uid)] == [130.0]


# ── controles positivos de investimento e saque ────────────────────────────

def test_tool_e_painel_de_investimento_com_valor_normal_seguem_iguais():
    from core.services.ai_chat.tools.investments import _investment_deposit_execute

    uid = _com_saldo()
    db.create_investment(uid, "cdb", 1.0, "monthly")
    c, h = _cliente(uid)

    assert "Aportado" in _investment_deposit_execute(uid, {"name": "cdb", "amount": 100}) or _saldo(uid) == 900.0
    assert _saldo(uid) == 900.0
    r = c.post(f"/investments/{uid}/deposit", headers=h, content='{"name":"cdb","amount":50}')
    assert r.status_code == 200, r.text
    assert _saldo(uid) == 850.0
    r = c.post(f"/investments/{uid}/withdraw", headers=h, content='{"name":"cdb","withdraw_all":true}')
    assert r.status_code == 200, r.text
    assert _saldo(uid) > 899.0


def test_painel_caixinha_saque_normal_e_sacar_tudo_seguem_iguais():
    uid = _com_saldo()
    db.create_pocket(uid, "viagem")
    c, h = _cliente(uid)
    assert c.post(f"/pockets/{uid}/viagem/deposit", headers=h, content='{"amount":200}').status_code == 200

    assert c.post(f"/pockets/{uid}/viagem/withdraw", headers=h, content='{"amount":50}').status_code == 200
    assert _saldo(uid) == 850.0
    assert c.post(f"/pockets/{uid}/viagem/withdraw", headers=h, content='{"withdraw_all":true}').status_code == 200
    assert _saldo(uid) == 1000.0


def test_valor_perigoso_acima_do_limite_pergunta_de_novo_e_inf_segue_nao_positivo():
    from utils_text import valor_perigoso

    assert valor_perigoso("1" * 14, 1.1e13) == "nao_entendi"      # código colado
    assert valor_perigoso("1" * 400, float("inf")) == "nao_positivo"
    assert valor_perigoso("2000000000", 2e9) is None             # R$ 2 bi segue valendo


# ── terceira rodada: banco de orçamento, conta com banco conectado, saldo, WhatsApp ──

@pytest.mark.parametrize("v", [float("nan"), float("inf"), 1e308])
def test_banco_orcamento_recusa_valor_nao_finito(v):
    uid = usuario_pagante()
    with pytest.raises(ValueError, match="BUDGET_INVALIDO"):
        db.upsert_budget(uid, "mercado", v)
    assert _linhas("select 1 from category_budgets where user_id=%s", uid) == []


def test_whatsapp_pagar_conta_com_banco_conectado_e_numero_absurdo_recusa_sem_pendencia():
    """Com banco conectado a forma é perguntada ANTES de pagar: o valor absurdo ficava
    guardado na pendência e o "dinheiro" seguinte dava "erro interno" em loop."""
    import db.bills as B
    from test_bill_amount_pending import _diga, _monta_conta_variavel
    from test_manual_launches_carteira_piggy import _connect_fake_bank

    uid = usuario_pagante()
    _connect_fake_bank(uid)
    _monta_conta_variavel(uid)

    for numero, trecho in ((HUGE, "Não entendi o valor"), ("9" * 14, "Não entendi o valor"),
                           ("1" * 400, "maior que zero"), ("0,001", "maior que zero")):
        resposta = _diga(uid, f"paguei a luz {numero}")
        assert trecho in resposta and "erro interno" not in resposta.lower(), (numero, resposta)
        assert not (db.get_pending_action(uid) or {}).get("action_type"), numero
    assert B.list_bills(uid, include_paid=True)[0]["status"] == "pending"


@pytest.mark.parametrize("bruto", BRUTOS)
def test_painel_saldo_inicial_e_ajuste_recusam_valor_nao_finito(bruto):
    uid = usuario_pagante()
    c, h = _cliente(uid)

    r1 = c.post(f"/account/{uid}/initial-balance", headers=h, content='{"amount":%s}' % bruto)
    r2 = c.post(f"/account/{uid}/adjust-balance", headers=h, content='{"target_balance":%s}' % bruto)

    assert (r1.status_code, r2.status_code) == (400, 400), (r1.text, r2.text)
    assert _saldo(uid) == 0.0 and _linhas("select 1 from launches where user_id=%s", uid) == []


def test_painel_saldo_inicial_e_ajuste_normais_seguem_iguais():
    uid = usuario_pagante()
    c, h = _cliente(uid)

    assert c.post(f"/account/{uid}/initial-balance", headers=h, content='{"amount":1000}').status_code == 200
    assert _saldo(uid) == 1000.0
    assert c.post(f"/account/{uid}/adjust-balance", headers=h, content='{"target_balance":-50}').status_code == 200
    assert _saldo(uid) == -50.0


@pytest.mark.parametrize("valor", [float("nan"), float("inf"), 1e300])
@pytest.mark.parametrize("variavel", [False, True])
def test_whatsapp_gasto_fixo_e_receita_fixa_recusam_valor_nao_finito(valor, variavel):
    from core.handlers import recurring

    uid = usuario_pagante()
    promote_to_pro(uid)
    gasto = recurring.add(uid, "gasto fixo aluguel", {"tipo": "despesa", "valor": valor, "nome": "Aluguel",
                                                       "dia": 10, "variavel": variavel})
    receita = recurring.add(uid, "receita fixa salario", {"tipo": "receita", "valor": valor, "nome": "Salario", "dia": 5})

    assert "inválido" in gasto and "inválido" in receita, (gasto, receita)
    assert _linhas("select 1 from recurring_expenses where user_id=%s", uid) == []
    assert _linhas("select 1 from recurring_incomes where user_id=%s", uid) == []


# ── quarta rodada: investimento (taxa, aporte inicial), renda do orçamento da casa ──

@pytest.mark.parametrize("bruto", BRUTOS)
def test_painel_investimento_recusa_taxa_nao_finita_ao_criar_e_ao_aportar(bruto):
    uid = _com_saldo()
    db.create_investment(uid, "cdb", 1.0, "monthly")
    c, h = _cliente(uid)

    criar = c.post(f"/investments/{uid}", headers=h, content='{"name":"lci","rate":%s,"period":"monthly"}' % bruto)
    aportar = c.post(f"/investments/{uid}/deposit", headers=h, content='{"name":"cdb","amount":100,"rate":%s}' % bruto)

    assert criar.status_code == 400 and aportar.status_code == 400, (criar.text, aportar.text)
    assert len(_linhas("select 1 from investments where user_id=%s", uid)) == 1  # só o cdb
    assert _saldo(uid) == 1000.0


@pytest.mark.parametrize("v", [float("nan"), float("inf"), 1e308])
def test_banco_investimento_recusa_taxa_e_aporte_inicial_nao_finitos(v):
    uid = _com_saldo()
    with pytest.raises(ValueError, match="BAD_RATE"):
        db.create_investment(uid, "cdb", v, "monthly")
    with pytest.raises(ValueError, match="INITIAL_AMOUNT_INVALID"):
        db.create_investment_db(uid, "lci", 1.0, "monthly", initial_amount=v)
    assert _linhas("select 1 from investments where user_id=%s", uid) == []


def test_painel_investimento_taxa_normal_e_selic_spread_negativo_seguem_iguais():
    uid = _com_saldo()
    c, h = _cliente(uid)

    ok = c.post(f"/investments/{uid}", headers=h, content='{"name":"lci","rate":0.9,"period":"monthly"}')
    selic = c.post(f"/investments/{uid}", headers=h, content='{"name":"tesouro","rate":-0.1,"period":"selic_spread"}')

    assert ok.status_code == 200 and selic.status_code == 200, (ok.text, selic.text)


@pytest.mark.parametrize("bruto", BRUTOS)
def test_painel_renda_do_orcamento_da_casa_recusa_valor_nao_finito(bruto):
    uid = usuario_pagante()
    c, h = _cliente(uid)

    r = c.put(f"/household-budget/{uid}/income", headers=h, content='{"amount":%s,"month":"2026-10"}' % bruto)

    assert r.status_code == 400, r.text
    assert _linhas("select 1 from household_budget_income where user_id=%s", uid) == []
    assert c.get(f"/household-budget/{uid}/status?month=2026-10").status_code == 200
