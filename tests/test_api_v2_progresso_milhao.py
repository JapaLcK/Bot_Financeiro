"""`/api/v2/progresso-milhao` pelo monólito real: isolamento entre usuários, gate Plus/Pro ANTES
da leitura (v2 e v1), `sem_banco_conectado`, `banco_desatualizado` e o arredondamento em centavos.
Banco real; os valores esperados são escritos à mão."""
import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

import api.v2.progresso_milhao as rota
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import sessao_de as sessao, sql
from _patrimonio_helpers import conexao, conta, horas_atras, posicao
from conftest import promote_to_pro
from db import ensure_user
from db.accounts import add_launch_and_update_balance

GET = "/api/v2/progresso-milhao"
SEM_BANCO = "sem_banco_conectado"


@pytest.fixture
def a_e_b(monkeypatch, user_id):
    a = user_id
    b = int(uuid.uuid4().int % 10_000_000_000)  # o autouse do conftest apaga depois
    ensure_user(b)
    promote_to_pro(a)  # 'pro' gravado = Plus no v2
    promote_to_pro(b)
    monkeypatch.setenv("DASHBOARD_V2_BETA_EMAILS", "")
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", f"{a},{b}")
    return a, b


def _get(quem, **q):  # `quem` e não `uid`: o teste de isolamento manda `?uid=` na query
    client = TestClient(dashboard.app)
    client.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, sessao(quem)["dashboard"])
    return client.get(GET, params=q)


def _viva(uid, saldo, **kw):
    conta(conexao(uid, f"item-{uid}-{saldo}", **kw), f"acc-{uid}-{saldo}", saldo)


# ── isolamento ───────────────────────────────────────────────────────────────

def test_b_nao_ve_o_patrimonio_de_a(a_e_b):
    a, b = a_e_b
    _viva(a, 5000)
    ra = _get(a)  # controle positivo: A vê o que tem
    assert ra.status_code == 200 and ra.json()["patrimonio"] == "5000.00" and SEM_BANCO not in ra.json()["motivos"], ra.text
    rb = _get(b, uid=a, user_id=a)  # o uid vindo do cliente não conta
    assert (rb.status_code, rb.json()) == (200, {"patrimonio": "0.00", "motivos": [SEM_BANCO]}), rb.text


def _get_app(quem, **q):  # o app: Bearer, /api/app (`usuario_do_app`), sem cookie
    client = TestClient(dashboard.app)
    client.headers["Authorization"] = f"Bearer {sessao(quem)['access']}"
    return client.get("/api/app/progresso-milhao", params=q)


def test_app_b_nao_ve_o_patrimonio_de_a(a_e_b):
    a, b = a_e_b
    for u in (a, b):
        sql("update auth_accounts set open_finance_onboarding_completed_at=now() where user_id=%s", u)
    _viva(a, 5000)
    ra = _get_app(a)  # controle positivo: A vê o que tem
    assert ra.status_code == 200 and ra.json()["patrimonio"] == "5000.00" and SEM_BANCO not in ra.json()["motivos"], ra.text
    rb = _get_app(b, uid=a, user_id=a)  # o uid vindo do cliente não conta
    assert (rb.status_code, rb.json()) == (200, {"patrimonio": "0.00", "motivos": [SEM_BANCO]}), rb.text


# ── gate: antes de qualquer leitura ──────────────────────────────────────────

def _boom(*a, **k):
    raise AssertionError("calcular rodou antes do gate")


def test_essencial_leva_403_pro_required_e_nao_le(monkeypatch, user_id):
    promote_to_pro(user_id, plan="essencial")
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", str(user_id))
    monkeypatch.setattr(rota, "calcular", _boom)
    r = _get(user_id)
    assert r.status_code == 403 and r.json()["error"]["code"] == "pro_required", r.text


@pytest.mark.parametrize("plano", ["pro", "pro_max"])  # Plus e Pro no v2
def test_plus_e_pro_entram(monkeypatch, user_id, plano):
    promote_to_pro(user_id, plan=plano)
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", str(user_id))
    assert _get(user_id).status_code == 200


def test_v2_desligado_pro_entra_essencial_nao(monkeypatch, user_id):
    monkeypatch.setenv("PLANS_V2_ENABLED", "0")
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", str(user_id))
    promote_to_pro(user_id)
    assert _get(user_id).status_code == 200
    promote_to_pro(user_id, plan="essencial")
    monkeypatch.setattr(rota, "calcular", _boom)
    r = _get(user_id)
    assert r.status_code == 403 and r.json()["error"]["code"] == "pro_required", r.text


# ── sem banco conectado ──────────────────────────────────────────────────────

def test_sem_conexao_so_carteira_e_so_pausada_sao_sem_banco(a_e_b):
    a, b = a_e_b
    assert _get(a).json() == {"patrimonio": "0.00", "motivos": [SEM_BANCO]}
    add_launch_and_update_balance(a, "receita", 300.0, "t", "t")
    assert _get(a).json()["patrimonio"] == "300.00" and SEM_BANCO in _get(a).json()["motivos"]
    _viva(b, 700, status="PAUSED")  # conexão pausada não entra no número nem tira o convite
    assert _get(b).json() == {"patrimonio": "0.00", "motivos": [SEM_BANCO]}


def test_conta_viva_tira_o_motivo_e_sync_velho_avisa(a_e_b):
    a, _ = a_e_b
    _viva(a, 1000, sync=horas_atras(72))
    r = _get(a).json()
    assert r["patrimonio"] == "1000.00"
    assert SEM_BANCO not in r["motivos"] and "banco_desatualizado" in r["motivos"]


def test_posicao_viva_sem_conta_brl_tira_o_motivo(a_e_b):
    a, _ = a_e_b
    posicao(conexao(a, f"item-{a}-pos"), f"inv-{a}", 2500)
    r = _get(a).json()
    assert SEM_BANCO not in r["motivos"] and r["patrimonio"] == "2500.00", r


# ── centavos ─────────────────────────────────────────────────────────────────

def test_total_sai_texto_com_duas_casas_half_up(a_e_b, monkeypatch):
    a, _ = a_e_b
    monkeypatch.setattr(rota, "calcular", lambda cur, uid: {
        "total": Decimal("1234.565"), "motivos": [], "base": {"contas": ["x"], "posicoes": []}})
    r = _get(a)
    assert r.json()["patrimonio"] == "1234.57" and isinstance(r.json()["patrimonio"], str)
