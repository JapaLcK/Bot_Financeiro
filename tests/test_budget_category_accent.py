"""
tests/test_budget_category_accent.py — orçamento e gasto casam mesmo com
grafias diferentes da MESMA categoria (acento e caixa).

Caso real (medido pelo Manager no Postgres local): orçamento cadastrado como
'cafe da manha', lançamento gravado como 'café da manhã' (grafia do catálogo,
que a leitura de categoria passou a devolver). O orçamento enxergava R$ 0,00,
o gasto real era R$ 50,00, e o alerta de 80% nunca disparava — silencioso.

A comparação passou a usar `cat_norm_sql` (`db/connection.py:93`), que é
insensível a caixa E a acento. Controle positivo obrigatório: categorias
genuinamente diferentes ('cafe' × 'carne') continuam separadas.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone

import db
from core.budget_alerts import evaluate_after_expense
from db.accounts import add_launch_and_update_balance
from db.analytics import compute_categories
from db.budgets import (
    get_budgets_status_for_month,
    sum_spent_in_category_this_month,
    upsert_budget,
)
import frontend.finance_bot_websocket_custom as dashboard


def _gasto(user_id: int, categoria: str, valor: float):
    add_launch_and_update_balance(
        user_id, "despesa", valor, "teste", "teste", categoria=categoria
    )


# ── o cenário do Manager ────────────────────────────────────────────────────

def test_orcamento_sem_acento_conta_gasto_com_acento(user_id):
    """Controle negativo: revertendo `_CAT_EQ` para `lower(categoria)=lower(%s)`
    em `db/budgets.py`, `spent` volta a 0.0 e as três asserções caem."""
    upsert_budget(user_id, "cafe da manha", 200.0)
    _gasto(user_id, "café da manhã", 50.0)

    assert sum_spent_in_category_this_month(user_id, "cafe da manha") == 50.0

    status = get_budgets_status_for_month(user_id)
    linha = [b for b in status["budgets"] if b["categoria"] == "cafe da manha"]
    assert len(linha) == 1, status["budgets"]
    assert linha[0]["spent"] == 50.0
    assert status["totals"]["spent"] == 50.0


def test_alerta_dispara_com_as_duas_grafias(user_id):
    """Threshold de 80% (`core/budget_alerts.THRESHOLDS`) cruzado quando o gasto
    entra na grafia acentuada e o orçamento está na grafia sem acento — e
    vice-versa, na segunda metade."""
    upsert_budget(user_id, "cafe da manha", 100.0)
    _gasto(user_id, "café da manhã", 90.0)
    alerta = evaluate_after_expense(
        user_id, "café da manhã", 90.0, datetime.now(timezone.utc)
    )
    assert alerta is not None and alerta.threshold == 80

    # grafias trocadas de lado: orçamento COM acento, gasto SEM
    upsert_budget(user_id, "pizzaría", 100.0)
    _gasto(user_id, "pizzaria", 90.0)
    alerta2 = evaluate_after_expense(
        user_id, "pizzaria", 90.0, datetime.now(timezone.utc)
    )
    assert alerta2 is not None and alerta2.threshold == 80


def test_donut_nao_duplica_a_mesma_categoria(user_id):
    """As duas grafias viram UMA fatia, com o total somado, e o orçamento
    cadastrado na terceira grafia (caixa alta) gruda nessa fatia."""
    upsert_budget(user_id, "CAFE DA MANHA", 100.0)
    _gasto(user_id, "cafe da manha", 30.0)
    _gasto(user_id, "café da manhã", 60.0)

    data = asyncio.run(dashboard.get_financial_data(user_id))
    fatias = [
        c for c in data["expense_categories"]
        if c["categoria"].lower().startswith(("cafe", "café"))
    ]
    assert len(fatias) == 1, data["expense_categories"]
    assert fatias[0]["total"] == 90.0
    # rótulo = grafia do lançamento MAIS RECENTE
    assert fatias[0]["categoria"] == "café da manhã"
    assert fatias[0]["budget"] == 100.0
    assert fatias[0]["budget_pct"] == 90.0
    assert "cat_key" not in fatias[0]
    # o alerta de 85% da dashboard (mesmo laço) só aparece se o orçamento grudou
    assert [a["type"] for a in data["alerts"]] == ["budget_warning"]

    # o outro donut (aba Análises, `db/analytics.py`) tem que agrupar igual
    hoje = date.today()
    analytics = compute_categories(user_id, hoje.replace(day=1), hoje + timedelta(days=1))
    cafes = [c for c in analytics if c["name"].lower().startswith(("cafe", "café"))]
    assert len(cafes) == 1, analytics
    assert cafes[0]["total"] == 90.0 and cafes[0]["name"] == "café da manhã"


# ── controle positivo: o normalizador não pode colapsar o que não é gêmeo ────

def test_categorias_diferentes_continuam_separadas(user_id):
    upsert_budget(user_id, "cafe", 100.0)
    upsert_budget(user_id, "carne", 100.0)
    _gasto(user_id, "cafe", 10.0)
    _gasto(user_id, "carne", 70.0)

    por_cat = {b["categoria"]: b["spent"] for b in get_budgets_status_for_month(user_id)["budgets"]}
    assert por_cat == {"cafe": 10.0, "carne": 70.0}
    assert sum_spent_in_category_this_month(user_id, "cafe") == 10.0

    data = asyncio.run(dashboard.get_financial_data(user_id))
    fatias = {c["categoria"]: c["total"] for c in data["expense_categories"]}
    assert fatias == {"cafe": 10.0, "carne": 70.0}
    hoje = date.today()
    analytics = compute_categories(user_id, hoje.replace(day=1), hoje + timedelta(days=1))
    assert {c["name"]: c["total"] for c in analytics} == {"cafe": 10.0, "carne": 70.0}

    # e o alerta de uma não pode ser disparado pelo gasto da outra
    assert evaluate_after_expense(
        user_id, "cafe", 10.0, datetime.now(timezone.utc)
    ) is None


def test_crud_de_orcamento_acha_a_linha_com_a_outra_grafia(user_id):
    """`upsert_budget` não pode criar orçamento gêmeo: 'café' cai na linha
    'cafe' que já existe, mantendo a grafia original."""
    upsert_budget(user_id, "cafe", 100.0)
    canon, created = upsert_budget(user_id, "Café", 300.0)
    assert (canon, created) == ("cafe", False)
    assert db.budgets.get_budget(user_id, "CAFÉ") == {"categoria": "cafe", "budget": 300.0}
    assert len(db.budgets.list_budgets(user_id)) == 1
    assert db.budgets.delete_budget(user_id, "café") is True
    assert db.budgets.list_budgets(user_id) == []
