"""
Cobre as tools Tier 2 da E2 (analítics que respondem perguntas como
'onde gastei mais?', 'gastei mais em abril ou maio?', etc).
"""
from datetime import date, datetime

import db
from core.services.ai_chat.tools.launches import _get_top_categories


# ─── get_top_categories ─────────────────────────────────────────────────────

def test_top_categories_agrega_despesas_e_credito(user_id):
    """Despesa em launches + compra em cartão na mesma categoria somam juntos."""
    # Despesa de R$ 50 em alimentação
    db.add_launch_and_update_balance(
        user_id, "despesa", 50, "mercado", "compra", categoria="alimentação",
    )

    # Compra no cartão de R$ 80 em alimentação
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.set_default_card(user_id, card_id)
    db.add_credit_purchase(
        user_id, card_id, 80, "alimentação", "ifood", date.today(),
    )

    result = _get_top_categories(user_id, {})
    cats = {c["categoria"]: c["total"] for c in result["categories"]}
    assert cats["alimentação"] == 130.0


def test_top_categories_exclui_movimentacao_interna(user_id):
    """Aportes/resgates de investimento NÃO contam como gasto."""
    # Despesa real
    db.add_launch_and_update_balance(
        user_id, "despesa", 100, "uber", "uber", categoria="transporte",
    )
    # Aporte de investimento (interno)
    db.add_launch_and_update_balance(
        user_id, "despesa", 500, "aporte", "aporte",
        categoria="investimento_aporte", is_internal_movement=True,
    )

    result = _get_top_categories(user_id, {})
    cats = {c["categoria"] for c in result["categories"]}
    assert "transporte" in cats
    assert "investimento_aporte" not in cats


def test_top_categories_ordena_por_total_desc(user_id):
    db.add_launch_and_update_balance(user_id, "despesa", 10, "x", "x", categoria="lazer")
    db.add_launch_and_update_balance(user_id, "despesa", 50, "y", "y", categoria="alimentação")
    db.add_launch_and_update_balance(user_id, "despesa", 30, "z", "z", categoria="transporte")

    result = _get_top_categories(user_id, {})
    cats_in_order = [c["categoria"] for c in result["categories"]]
    assert cats_in_order[:3] == ["alimentação", "transporte", "lazer"]


def test_top_categories_respeita_limit(user_id):
    for i, cat in enumerate(["a", "b", "c", "d", "e", "f", "g"]):
        db.add_launch_and_update_balance(user_id, "despesa", 10 + i, cat, cat, categoria=cat)

    result = _get_top_categories(user_id, {"limit": 3})
    assert len(result["categories"]) == 3


def test_top_categories_filtra_por_periodo(user_id):
    """Despesa fora do range não aparece."""
    db.add_launch_and_update_balance(
        user_id, "despesa", 100, "antiga", "antiga",
        categoria="lazer",
        criado_em=datetime(2026, 3, 15),  # março
    )
    db.add_launch_and_update_balance(
        user_id, "despesa", 50, "recente", "recente",
        categoria="alimentação",
        criado_em=datetime(2026, 5, 5),  # maio
    )

    # Filtra abril em diante — só a recente
    result = _get_top_categories(user_id, {"start_date": "2026-04-01", "end_date": "2026-12-31"})
    cats = {c["categoria"] for c in result["categories"]}
    assert "alimentação" in cats
    assert "lazer" not in cats


def test_top_categories_end_anterior_a_start(user_id):
    result = _get_top_categories(user_id, {"start_date": "2026-05-01", "end_date": "2026-04-01"})
    assert "error" in result


def test_top_categories_vazio_quando_sem_gastos(user_id):
    result = _get_top_categories(user_id, {})
    assert result["categories"] == []
    assert result["count"] == 0


def test_top_categories_exclui_reembolso_cartao(user_id):
    """credit_transactions com is_refund=true não conta como gasto."""
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.set_default_card(user_id, card_id)
    db.add_credit_purchase(user_id, card_id, 100, "lazer", "show", date.today())
    # Reembolso de R$ 100 — mesma categoria
    db.add_credit_refund(
        user_id=user_id, card_id=card_id, valor=100,
        categoria="lazer", nota="cancelado", purchased_at=date.today(),
    )

    result = _get_top_categories(user_id, {})
    cats = {c["categoria"]: c["total"] for c in result["categories"]}
    # Compra (100) sem subtração do reembolso — porque a tool agrega APENAS
    # gastos, e o reembolso é ignorado. Total = 100.
    assert cats.get("lazer") == 100.0
