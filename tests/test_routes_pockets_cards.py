"""Rotas de frontend/routes/{pockets,cards}.py (refactor Fase 1, Etapa 5).

Smoke da extração: as 21 rotas movidas continuam registradas e protegidas
pela cadeia de auth do dashboard. Não toca dados — 401 sem token.
(test_pockets_endpoints.py cobre o comportamento de pockets com HTTP real.)
"""

from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard

client = TestClient(dashboard.app)

UID = 832398038

PROTECTED_GETS = [
    f"/goals/{UID}/status",
    f"/pockets/{UID}/teste/history",
    f"/cards/{UID}/summary",
    f"/installments/{UID}/list",
    f"/bills/{UID}",
]


def test_rotas_movidas_exigem_token():
    for path in PROTECTED_GETS:
        resp = client.get(path)
        assert resp.status_code == 401, f"{path}: {resp.status_code}"


def test_paths_registrados_no_app():
    # FastAPI 0.116+ (Starlette 1.0) não achata mais as rotas incluídas via
    # include_router em app.routes — elas passam a viver num _IncludedRouter
    # opaco (sem .path). A via estável de introspecção é o schema OpenAPI, que
    # também normaliza os conversores ({pocket_name:path} -> {pocket_name}).
    paths = set(dashboard.app.openapi()["paths"].keys())
    expected = {
        "/pockets/{user_id}",
        "/pockets/{user_id}/{pocket_id}/meta",
        "/goals/{user_id}/status",
        "/pockets/{user_id}/{pocket_name}",
        "/pockets/{user_id}/{pocket_name}/deposit",
        "/pockets/{user_id}/{pocket_name}/withdraw",
        "/pockets/{user_id}/{pocket_name}/history",
        "/cards/{user_id}",
        "/cards/{user_id}/summary",
        "/cards/{user_id}/reorder",
        "/cards/{user_id}/{card_id}",
        "/cards/{user_id}/{card_id}/delete-impact",
        "/installments/{user_id}/list",
        "/installments/{user_id}/{group_id}/delete-impact",
        "/installments/{user_id}/{group_id}/anticipate",
        "/installments/{user_id}/{group_id}",
        "/bills/{user_id}",
        "/bills/{user_id}/{bill_id}",
        "/bills/{user_id}/{bill_id}/pay",
    }
    missing = expected - paths
    assert not missing, f"rotas sumiram do app: {missing}"


# --- B0: valores exatos das rotas /goals/status e /cards/summary, e as funções
# extraídas (status_metas / resumo_cartoes) que o show_card vai chamar. ---
import uuid
from datetime import date, timedelta

import pytest

import db
from conftest import _cleanup_user, promote_to_pro
from db.connection import get_conn
from db.pockets import update_pocket_meta


@pytest.fixture
def outro_id():
    uid = int(uuid.uuid4().int % 10_000_000_000)
    db.ensure_user(uid)
    yield uid
    _cleanup_user(uid)


def _auth(c, uid):
    promote_to_pro(uid)
    c.cookies.set(dashboard.AUTH_COOKIE_NAME, dashboard._make_jwt(uid, "b0@t.com"))
    c.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, dashboard.make_dashboard_token(uid, hours=1))


def _seed(uid):
    """Meta com prazo (aporte 300 de 1000, 60 dias), caixinha sem meta, cartão
    com fatura aberta (800, 100 pagos) e outra paga (400)."""
    promote_to_pro(uid)  # plano grátis só cria 1 caixinha
    db.add_launch_and_update_balance(uid, "receita", 1000.0, None, "seed")
    _, pid, _ = db.create_pocket(uid, "viagem")
    db.create_pocket(uid, "livre")
    update_pocket_meta(
        uid, pid, target_amount=1000.0,
        target_date=(date.today() + timedelta(days=60)).isoformat(),
    )
    db.pocket_deposit_from_account(uid, "viagem", 300.0, "aporte")
    card_id = db.create_card(uid, "Nubank", 5, 12, credit_limit=5000.0)
    with get_conn() as conn, conn.cursor() as cur:
        for ps, pe, st, tot, paid in (
            ("2026-09-06", "2026-10-05", "paid", 400, 400),
            ("2026-10-06", "2026-11-05", "open", 800, 100),
        ):
            cur.execute(
                "insert into credit_bills (user_id, card_id, period_start, period_end,"
                " status, total, paid_amount) values (%s,%s,%s,%s,%s,%s,%s)",
                (uid, card_id, ps, pe, st, tot, paid),
            )
        conn.commit()
    return pid, card_id


def _esperado_metas(uid, pid):
    base = {
        "interest_enabled": False, "interest_rate": 1.0, "interest_period": "cdi",
        "source": "manual", "of_investment_id": None, "of_plan_active": True,
        "emoji": None, "color": None, "status": "active", "description": None,
    }
    return [
        {**base, "id": pid, "name": "viagem", "balance": 300.0, "target_amount": 1000.0,
         "target_date": (date.today() + timedelta(days=60)).isoformat(),
         "is_goal": True, "pct_complete": 30.0, "remaining": 700.0, "days_left": 60,
         "monthly_pace_needed": 350.0, "monthly_pace_current": 100.0,
         "projected_months": 7.0, "indicator": "behind"},
    ]


def _esperado_cartoes(card_id):
    return [{
        "id": card_id, "name": "Nubank", "color": None, "flag": None, "last4": None,
        "closing_day": 5, "due_day": 12, "credit_limit": 5000.0, "is_default": False,
        "open_bill": {"id": None, "total": 800.0, "paid_amount": 100.0,
                      "due_amount": 700.0, "period_end": "2026-11-05"},
        "credit_used": 700.0, "credit_available": 4300.0,
    }]


def test_rotas_metas_e_cartoes_valores_exatos(user_id, outro_id):
    pid, card_id = _seed(user_id)
    _seed(outro_id)  # dados do outro usuário não podem aparecer
    c = TestClient(dashboard.app)
    _auth(c, user_id)

    goals = c.get(f"/goals/{user_id}/status").json()["goals"]
    nomes = {g["name"] for g in goals}
    assert nomes == {"viagem", "livre"}
    assert [g for g in goals if g["name"] == "viagem"] == _esperado_metas(user_id, pid)
    livre = next(g for g in goals if g["name"] == "livre")
    assert livre["is_goal"] is False and livre["indicator"] == "no_target"

    cards = c.get(f"/cards/{user_id}/summary").json()["cards"]
    assert len(cards) == 1
    esperado = _esperado_cartoes(card_id)[0]
    esperado["open_bill"]["id"] = cards[0]["open_bill"]["id"]  # id da fatura é serial
    assert cards[0]["open_bill"]["id"] is not None
    assert cards == [esperado]


def test_funcoes_extraidas_iguais_as_rotas(user_id, outro_id):
    from core.services.metas_status import status_metas
    from db.cartoes_resumo import resumo_cartoes

    _seed(user_id)
    _seed(outro_id)
    c = TestClient(dashboard.app)
    _auth(c, user_id)
    assert status_metas(user_id) == c.get(f"/goals/{user_id}/status").json()["goals"]
    assert resumo_cartoes(user_id) == c.get(f"/cards/{user_id}/summary").json()["cards"]
    assert all(x["id"] != y["id"] for x in status_metas(user_id) for y in status_metas(outro_id))
