"""Mesma classe de `test_valor_nao_finito_dinheiro.py`, pelas portas do painel
(JSON cru com NaN/Infinity, que o `json=` do TestClient recusaria), pelo banco e
pelas mensagens das tools da IA."""
from __future__ import annotations

import os

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

os.environ.setdefault("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())

import db
import frontend.finance_bot_websocket_custom as dashboard
from conftest import promote_to_pro, usuario_pagante
from utils_text import valor_valido

BRUTOS = ["NaN", "Infinity", "1e308"]


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


def test_valor_valido_int_gigante_nao_estoura():
    assert valor_valido(10**400) is False


@pytest.mark.parametrize("tipo", ["despesa", "receita"])
@pytest.mark.parametrize("bruto", BRUTOS)
def test_painel_lancamento_recusa_valor_nao_finito(tipo, bruto):
    uid = usuario_pagante()
    c, h = _cliente(uid)

    r = c.post(f"/launches/{uid}", headers=h,
               content=f'{{"tipo":"{tipo}","valor":{bruto},"alvo":"x","nota":"x"}}')

    assert r.status_code == 400, r.text
    assert _linhas("select 1 from launches where user_id=%s", uid) == []
    assert not [x for x in _linhas("select balance from accounts where user_id=%s", uid) if x["balance"]]


def test_painel_lancamento_normal_segue_igual():
    uid = usuario_pagante()
    c, h = _cliente(uid)

    r = c.post(f"/launches/{uid}", headers=h,
               content='{"tipo":"despesa","valor":50,"alvo":"mercado","nota":"x"}')

    assert r.status_code == 200, r.text
    assert len(_linhas("select 1 from launches where user_id=%s", uid)) == 1


@pytest.mark.parametrize("v", [float("nan"), float("inf"), float("-inf")])
def test_banco_recusa_lancamento_nao_finito(v):
    uid = usuario_pagante()
    with pytest.raises(ValueError, match="VALOR_INVALIDO"):
        db.add_launch_and_update_balance(user_id=uid, tipo="receita", valor=v, alvo="x", nota="x")
    assert _linhas("select 1 from launches where user_id=%s", uid) == []


@pytest.mark.parametrize("bruto", BRUTOS)
def test_painel_cartao_recusa_limite_nao_finito(bruto):
    uid = usuario_pagante()
    c, h = _cliente(uid)

    r = c.post(f"/cards/{uid}", headers=h,
               content=f'{{"name":"Card","closing_day":10,"due_day":17,"credit_limit":{bruto}}}')

    assert r.status_code == 400, r.text
    assert _linhas("select 1 from credit_cards where user_id=%s", uid) == []


@pytest.mark.parametrize("bruto", BRUTOS)
def test_painel_cartao_patch_recusa_limite_nao_finito(bruto):
    uid = usuario_pagante()
    cid = db.create_card(uid, "Nubank", closing_day=10, due_day=17)
    c, h = _cliente(uid)

    r = c.patch(f"/cards/{uid}/{cid}", headers=h, content=f'{{"credit_limit":{bruto}}}')

    assert r.status_code == 400, r.text
    assert db.get_card_by_id(uid, cid)["credit_limit"] is None


@pytest.mark.parametrize("limite", [5000, 0])
def test_painel_cartao_limite_legitimo_segue_igual(limite):
    uid = usuario_pagante()
    c, h = _cliente(uid)

    r = c.post(f"/cards/{uid}", headers=h,
               content=f'{{"name":"Card","closing_day":10,"due_day":17,"credit_limit":{limite}}}')

    assert r.status_code == 200, r.text
    cartao = _linhas("select credit_limit from credit_cards where user_id=%s", uid)[0]
    assert float(cartao["credit_limit"] or 0) == float(limite)


@pytest.mark.parametrize("v", [float("nan"), float("inf"), 1e308])
def test_banco_boleto_recusa_valor_nao_finito_na_criacao_e_na_edicao(v):
    from datetime import date
    from db.bills import create_boleto, update_boleto

    uid = usuario_pagante()
    with pytest.raises(ValueError, match="VALOR_INVALIDO"):
        create_boleto(uid, "IPTU", v, date(2026, 12, 15))
    b = create_boleto(uid, "IPTU", 100.0, date(2026, 12, 15))
    with pytest.raises(ValueError, match="VALOR_INVALIDO"):
        update_boleto(uid, b["id"], amount=v)
    assert [float(x["amount"]) for x in _linhas("select amount from bill_instances where user_id=%s", uid)] == [100.0]


def test_banco_boleto_edicao_normal_segue_igual():
    from datetime import date
    from db.bills import create_boleto, update_boleto

    uid = usuario_pagante()
    b = create_boleto(uid, "IPTU", 100.0, date(2026, 12, 15))
    update_boleto(uid, b["id"], amount=250.5)
    assert [float(x["amount"]) for x in _linhas("select amount from bill_instances where user_id=%s", uid)] == [250.5]


# ── mensagens das tools da IA (o usuário não vê "tenta de novo") ───────────

@pytest.mark.parametrize("v", [float("nan"), 1e308, 10**400, 10**14])
def test_tools_dizem_valor_invalido_e_nao_convidam_a_repetir(v):
    from core.services.ai_chat.tools.bills import _add_boleto_execute
    from core.services.ai_chat.tools.cards import _add_credit_purchase_execute
    from core.services.ai_chat.tools.launches import _add_launch_execute

    uid = usuario_pagante()
    cid = db.create_card(uid, "Nubank", closing_day=10, due_day=17)
    db.set_default_card(uid, cid)

    assert _add_credit_purchase_execute(uid, {"valor": v, "descricao": "x"}) == "🐷 Valor inválido."
    assert _add_launch_execute(uid, {"tipo": "despesa", "valor": v, "descricao": "x"}) == "🐷 Valor inválido."
    # `10**400` estoura em `float()` e o boleto trata como "sem valor"; o resto é valor lido e recusado
    esperado = "🐷 Qual o valor do boleto?" if v == 10**400 else "🐷 Valor inválido."
    assert _add_boleto_execute(uid, {"name": "IPTU", "amount": v, "due_date": "2026-12-15"}) == esperado
