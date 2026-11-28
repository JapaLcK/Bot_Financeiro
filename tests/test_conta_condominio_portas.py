"""Conta de condomínio só grava categoria do plano em TODA porta de escrita (launches, recorrentes, orçamento,
cartão, boleto, lista do WhatsApp). Banco real. Pessoal fica intacta (controle positivo de cada porta)."""
from __future__ import annotations

import uuid
from unittest import mock

import pytest

import db
from adapters.whatsapp import wa_runtime
from conftest import _cleanup_user, promote_to_pro
from db import budgets, cards
from db.bills import create_boleto, update_boleto
from db.categories import resolve_category_input
from db.recurring import create_recurring_expense, update_recurring_expense
from db.recurring_income import create_recurring_income, update_recurring_income
from scripts.ativar_conta_condominio import ativar

PESSOAIS = {"alimentação", "mercado", "transporte", "lazer", "moradia", "saúde", "educação", "assinaturas"}


def _novo(condominio: bool) -> int:
    uid = 100_000_000 + int(uuid.uuid4().int % 1_900_000_000)
    db.ensure_user(uid)
    promote_to_pro(uid)
    if condominio:
        ativar(uid)
    return uid


@pytest.fixture(params=["condo", "pessoal"])
def conta(request):
    uid = _novo(request.param == "condo")
    yield request.param, uid
    _cleanup_user(uid)


def _esperado(tipo, canonica, condo):
    return condo if tipo == "condo" else canonica


def _cat_launch(uid, lid):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select categoria from launches where user_id=%s and id=%s", (uid, lid))
        return cur.fetchone()["categoria"]


def test_wa_recategorizar_botao_grava_so_o_plano(conta):
    tipo, uid = conta
    for pedido, condo in (("moradia", "outros"), ("energia", "energia elétrica")):
        lid, _, _ = db.add_launch_and_update_balance(uid, "despesa", 10, None, "x", "outros")
        msg = wa_runtime._apply_recategorize(uid, lid, pedido)
        gravada = _cat_launch(uid, lid)
        assert gravada == _esperado(tipo, pedido, condo), (tipo, pedido, gravada)
        assert gravada in msg
    if tipo == "condo":  # a canônica pessoal não vira linha do plano
        assert "moradia" not in {c.lower() for c in db.list_user_categories(uid)}


def test_resolvedor_unico_das_portas_de_api(conta):
    """`lancamentos.editar`, `gastos_fixos.criar`, PATCH do monólito, orçamento e extrato_ia chamam este."""
    tipo, uid = conta
    for create in (True, False):
        assert resolve_category_input(uid, "moradia", create=create) == _esperado(tipo, "moradia", "outros")
    assert resolve_category_input(uid, "energia", create=True) == _esperado(tipo, "energia", "energia elétrica")


def test_recorrentes_quatro_portas(conta):
    tipo, uid = conta
    e = create_recurring_expense(uid, "Luz", 50, "moradia", 5, "account")
    assert e["category"] == _esperado(tipo, "moradia", "outros")
    update_recurring_expense(uid, e["id"], category="energia")
    r = create_recurring_income(uid, "Taxa", 80, "moradia", 5)
    assert r["category"] == _esperado(tipo, "moradia", "outros")
    update_recurring_income(uid, r["id"], category="energia")
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select category from recurring_expenses where id=%s and user_id=%s", (e["id"], uid))
        ce = cur.fetchone()["category"]
        cur.execute("select category from recurring_incomes where id=%s and user_id=%s", (r["id"], uid))
        ci = cur.fetchone()["category"]
    assert ce == ci == _esperado(tipo, "energia", "energia elétrica")


def test_orcamento_boleto_e_cartao(conta):
    tipo, uid = conta
    if tipo == "condo":  # fora do plano não vira "outros": recusa (ver test_orcamento_condominio_*)
        with pytest.raises(ValueError, match="CATEGORIA_INVALIDA"):
            budgets.upsert_budget(uid, "moradia", 100)
    else:
        assert budgets.upsert_budget(uid, "moradia", 100)[0] == "moradia"
    b = create_boleto(uid, "Luz", 10, "2030-01-10", category="moradia")
    assert b["category"] == _esperado(tipo, "moradia", "outros")
    assert update_boleto(uid, b["id"], category="energia")["category"] == _esperado(tipo, "energia", "energia elétrica")
    card = db.create_card(uid, "Cartao", 10, 17)
    from datetime import date
    tx, _due, _b = cards.add_credit_purchase(uid, card["id"] if isinstance(card, dict) else card, 10, "moradia", "x",
                                             date.today())
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select categoria from credit_transactions where id=%s and user_id=%s", (tx, uid))
        assert cur.fetchone()["categoria"] == _esperado(tipo, "moradia", "outros")


def _rows(sections):
    return [r for s in sections for r in s["rows"]]


def test_lista_do_whatsapp(conta):
    tipo, uid = conta
    enviado = {}
    with mock.patch.object(wa_runtime, "send_interactive_list", side_effect=lambda **k: enviado.update(k)):
        wa_runtime._send_recategorize_list("5511", "corpo", 7, uid)
    rows = _rows(enviado["sections"])
    titulos = {r["title"] for r in rows}
    assert len(rows) <= 10 and all(len(r["title"]) <= 24 for r in rows)
    if tipo == "condo":
        nomes = {r["id"].split(":", 2)[2] for r in rows if r["id"].count(":") >= 2}
        assert nomes and not nomes & (PESSOAIS - {"outros"})
        assert nomes <= {n for n, _ in db.categories.plano_da_conta(uid)}
    else:
        assert PESSOAIS <= titulos


def _limite(uid, cat):
    return (budgets.get_budget(uid, cat) or {}).get("budget")


def test_orcamento_condominio_fora_do_plano_nao_sobrescreve_outros():
    uid = _novo(True)
    try:
        budgets.upsert_budget(uid, "outros", 1000)
        for pedido in ("gás", "moradia"):
            with pytest.raises(ValueError, match="CATEGORIA_INVALIDA"):
                budgets.upsert_budget(uid, pedido, 300)
        assert _limite(uid, "outros") == 1000
    finally:
        _cleanup_user(uid)


def test_orcamento_condominio_casa_no_plano_ou_pede_outros():
    uid = _novo(True)
    try:
        assert budgets.upsert_budget(uid, "energia", 200)[0] == "energia elétrica"
        assert budgets.upsert_budget(uid, "Outros", 50)[0] == "outros"
        assert _limite(uid, "outros") == 50
    finally:
        _cleanup_user(uid)


def test_orcamento_pessoal_gas_grava_gas():
    uid = _novo(False)
    try:
        assert budgets.upsert_budget(uid, "gás", 80)[0] == "gás"
    finally:
        _cleanup_user(uid)
