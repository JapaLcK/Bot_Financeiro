"""`POST /api/v2/gastos-fixos` pelo monólito real: grava via `create_recurring_expense`, validação 422,
isolamento do cartão, gate de plano e CSRF do pai. Banco real."""
import uuid

import pytest
from fastapi.testclient import TestClient

from api.v2 import gastos_fixos
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import csrf, sessao_de as sessao
from conftest import promote_to_pro
from db import ensure_user
from db.cards import create_card
from db.recurring import list_recurring_expenses

URL = "/api/v2/gastos-fixos"
OK = {"nome": "Aluguel", "valor": "1500.50", "dia": 5, "categoria": "moradia", "forma": "conta"}


@pytest.fixture
def a_e_b(monkeypatch, user_id):
    a = user_id
    b = int(uuid.uuid4().int % 10_000_000_000)  # o autouse do conftest apaga depois
    ensure_user(b)
    promote_to_pro(a, plan="essencial")
    promote_to_pro(b, plan="essencial")
    monkeypatch.setenv("DASHBOARD_V2_BETA_EMAILS", "")
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", f"{a},{b}")
    return a, b


def _post(uid, corpo, com_csrf=True):
    client = TestClient(dashboard.app)
    client.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, sessao(uid)["dashboard"])
    return client.post(URL, json=corpo, headers=csrf(client) if com_csrf else {})


def test_cria_na_conta_e_le_de_volta(a_e_b):
    a, _ = a_e_b
    r = _post(a, OK)
    assert r.status_code == 200, r.text
    j = r.json()
    (g,) = list_recurring_expenses(a)
    assert j == {"id": g["id"], "nome": "Aluguel", "valor": "1500.50", "dia": 5,
                 "categoria": g["category"], "forma": "conta"}
    assert (g["amount"], g["due_day"], g["payment_type"], g["card_id"], g["frequency"], g["payment_mode"]) == \
        (1500.5, 5, "account", None, "monthly", "autopay")


def test_cria_no_cartao_do_proprio_usuario(a_e_b):
    a, _ = a_e_b
    cid = create_card(a, "Nubank", 10, 17)
    r = _post(a, {**OK, "forma": "cartao", "cartao_id": cid})
    assert r.status_code == 200 and r.json()["forma"] == "cartao", r.text
    assert list_recurring_expenses(a)[0]["card_id"] == cid


@pytest.mark.parametrize("troca", [
    {"nome": ""}, {"nome": "   "}, {"nome": "x" * 81},
    {"valor": "abc"}, {"valor": "0"}, {"valor": "-5"}, {"valor": "1,5"},
    {"dia": 0}, {"dia": 32},
    {"categoria": "categoria-que-nao-existe-xyz"},
    {"forma": "cartao"},  # cartão sem id
    {"forma": "pix"},
])
def test_422_e_nada_gravado(a_e_b, troca):
    a, _ = a_e_b
    r = _post(a, {**OK, **troca})
    assert r.status_code == 422, r.text
    assert list_recurring_expenses(a) == []


def test_cartao_de_outro_usuario_recusa_e_nada_gravado(a_e_b):
    a, b = a_e_b
    cid_b = create_card(b, "Do B", 10, 17)
    r = _post(a, {**OK, "forma": "cartao", "cartao_id": cid_b})
    assert r.status_code == 422, r.text
    assert list_recurring_expenses(a) == [] and list_recurring_expenses(b) == []
    # controle: o dono do cartão grava com o mesmo corpo
    assert _post(b, {**OK, "forma": "cartao", "cartao_id": cid_b}).status_code == 200


def test_plano_sem_a_feature_403_pro_required(monkeypatch, user_id):
    # O plano sem app não chega à rota (o portão do app barra antes); o corte da feature é provado
    # com a conta aberta e o gate dizendo não. O controle negativo (gate aberto = 200) é o teste de criação.
    promote_to_pro(user_id, plan="essencial")
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", str(user_id))
    monkeypatch.setattr(gastos_fixos, "plan_gate_ok", lambda uid, f: f != "recurring_expenses")
    r = _post(user_id, OK)
    assert (r.status_code, r.json()["error"]["code"]) == (403, "pro_required"), r.text
    assert list_recurring_expenses(user_id) == []


def test_sem_csrf_403_e_nada_gravado(a_e_b):
    a, _ = a_e_b
    r = _post(a, OK, com_csrf=False)
    assert r.status_code == 403, r.text
    assert list_recurring_expenses(a) == []
