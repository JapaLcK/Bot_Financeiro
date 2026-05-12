"""
Cobre as tools Tier 2 da E2 (analítics que respondem perguntas como
'onde gastei mais?', 'gastei mais em abril ou maio?', etc).
"""
from datetime import date, datetime

import db
from core.services.ai_chat.tools.launches import (
    _compare_periods,
    _forecast_month_end,
    _get_largest_expenses,
    _get_spending_trend,
    _get_top_categories,
)


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


# ─── get_largest_expenses ───────────────────────────────────────────────────

def test_largest_expenses_retorna_o_maior_individual(user_id):
    """O caso real do Lucas: tem R$ 150, R$ 22, R$ 349 — maior é o de 349."""
    db.add_launch_and_update_balance(user_id, "despesa", 150, "laranja", "Mandei 150 pra Laranja", categoria="outros")
    db.add_launch_and_update_balance(user_id, "despesa", 22.46, "x", "x", categoria="outros")
    db.add_launch_and_update_balance(user_id, "despesa", 349, "stanley", "stanley presente", categoria="lazer")

    result = _get_largest_expenses(user_id, {"limit": 1})
    assert len(result["expenses"]) == 1
    top = result["expenses"][0]
    assert top["valor"] == 349.0
    assert top["categoria"] == "lazer"


def test_largest_expenses_ordena_desc(user_id):
    db.add_launch_and_update_balance(user_id, "despesa", 10, "a", "a", categoria="outros")
    db.add_launch_and_update_balance(user_id, "despesa", 80, "b", "b", categoria="outros")
    db.add_launch_and_update_balance(user_id, "despesa", 50, "c", "c", categoria="outros")

    result = _get_largest_expenses(user_id, {})
    values = [e["valor"] for e in result["expenses"]]
    assert values == [80.0, 50.0, 10.0]


def test_largest_expenses_inclui_credito(user_id):
    """Compra no cartão deve aparecer junto com despesas normais."""
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.set_default_card(user_id, card_id)
    db.add_launch_and_update_balance(user_id, "despesa", 50, "x", "x", categoria="outros")
    db.add_credit_purchase(user_id, card_id, 200, "lazer", "show", date.today())

    result = _get_largest_expenses(user_id, {"limit": 2})
    fontes = {e["fonte"] for e in result["expenses"]}
    assert "credito" in fontes
    assert "launches" in fontes
    assert result["expenses"][0]["valor"] == 200.0
    assert result["expenses"][0]["fonte"] == "credito"


def test_largest_expenses_exclui_movimentacao_interna_e_refund(user_id):
    """Aporte de investimento e reembolso de cartão não contam."""
    db.add_launch_and_update_balance(
        user_id, "despesa", 1000, "aporte", "aporte",
        categoria="investimento_aporte", is_internal_movement=True,
    )
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.set_default_card(user_id, card_id)
    db.add_credit_refund(
        user_id=user_id, card_id=card_id, valor=500,
        categoria="lazer", nota="estornado", purchased_at=date.today(),
    )
    db.add_launch_and_update_balance(user_id, "despesa", 50, "real", "real", categoria="outros")

    result = _get_largest_expenses(user_id, {})
    assert len(result["expenses"]) == 1
    assert result["expenses"][0]["valor"] == 50.0


def test_largest_expenses_respeita_limit(user_id):
    for i in range(7):
        db.add_launch_and_update_balance(
            user_id, "despesa", 10 + i, f"d{i}", f"d{i}", categoria="outros",
        )
    result = _get_largest_expenses(user_id, {"limit": 3})
    assert len(result["expenses"]) == 3


def test_largest_expenses_end_anterior_a_start(user_id):
    result = _get_largest_expenses(user_id, {"start_date": "2026-05-01", "end_date": "2026-04-01"})
    assert "error" in result


def test_largest_expenses_vazio_sem_gastos(user_id):
    result = _get_largest_expenses(user_id, {})
    assert result["expenses"] == []
    assert result["count"] == 0


# ─── compare_periods ────────────────────────────────────────────────────────

def test_compare_periods_diff_correto(user_id):
    """Período A com R$ 100 despesa, B com R$ 250 → diff_despesa = +150."""
    db.add_launch_and_update_balance(
        user_id, "despesa", 100, "a", "a",
        categoria="outros",
        criado_em=datetime(2026, 4, 10),
    )
    db.add_launch_and_update_balance(
        user_id, "despesa", 250, "b", "b",
        categoria="outros",
        criado_em=datetime(2026, 5, 5),
    )

    result = _compare_periods(user_id, {
        "period_a_start": "2026-04-01", "period_a_end": "2026-04-30",
        "period_b_start": "2026-05-01", "period_b_end": "2026-05-31",
        "period_a_label": "Abril", "period_b_label": "Maio",
    })
    assert result["period_a"]["despesa"] == 100.0
    assert result["period_b"]["despesa"] == 250.0
    assert result["diff_despesa"] == 150.0
    assert result["period_a"]["label"] == "Abril"


def test_compare_periods_falta_arg(user_id):
    result = _compare_periods(user_id, {"period_a_start": "2026-04-01"})
    assert "error" in result


def test_compare_periods_end_anterior_a_start(user_id):
    result = _compare_periods(user_id, {
        "period_a_start": "2026-05-01", "period_a_end": "2026-04-01",
        "period_b_start": "2026-05-01", "period_b_end": "2026-05-31",
    })
    assert "error" in result


# ─── get_spending_trend ─────────────────────────────────────────────────────

def test_spending_trend_retorna_dados_dos_meses(user_id):
    """Cria gasto em meses diferentes, confere que aparecem na lista."""
    today = date.today()
    # Gasto no mês corrente
    db.add_launch_and_update_balance(user_id, "despesa", 50, "x", "x", categoria="outros")

    result = _get_spending_trend(user_id, {"months": 3})
    assert result["months"] == 3
    assert isinstance(result["data"], list)
    # Pelo menos o mês corrente deve aparecer (com o gasto que criamos)
    current_month = [r for r in result["data"]
                     if r["year"] == today.year and r["month"] == today.month]
    assert len(current_month) == 1
    assert current_month[0]["despesa"] >= 50.0


def test_spending_trend_clamp_limite(user_id):
    result = _get_spending_trend(user_id, {"months": 100})
    assert result["months"] == 24  # clamp em 24


def test_spending_trend_inclui_credito(user_id):
    """Compra no cartão deve entrar como despesa do mês."""
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.set_default_card(user_id, card_id)
    db.add_credit_purchase(user_id, card_id, 200, "outros", "x", date.today())

    result = _get_spending_trend(user_id, {"months": 1})
    today = date.today()
    cur = next((r for r in result["data"]
                if r["year"] == today.year and r["month"] == today.month), None)
    assert cur is not None
    assert cur["despesa"] >= 200.0


# ─── forecast_month_end ─────────────────────────────────────────────────────

def test_forecast_month_end_sem_gastos_projeta_zero(user_id):
    result = _forecast_month_end(user_id, {})
    assert result["despesa_real_atual"] == 0.0
    assert result["aportes_atual"] == 0.0
    assert result["saidas_atual"] == 0.0
    assert result["saidas_projetadas_fim_do_mes"] == 0.0


def test_forecast_month_end_projeta_baseado_em_ritmo(user_id):
    """Despesa atual = R$ 100, gastei só hoje → projeção considera ritmo
    diário e extrapola pro mês."""
    db.add_launch_and_update_balance(user_id, "despesa", 100, "x", "x", categoria="outros")

    result = _forecast_month_end(user_id, {})
    assert result["despesa_real_atual"] == 100.0
    assert result["saidas_atual"] == 100.0  # sem aportes
    # Projeção deve ser >= valor atual (mês continua)
    assert result["saidas_projetadas_fim_do_mes"] >= 100.0
    for key in ("days_elapsed", "days_in_month", "saldo_parcial", "vai_fechar_negativo"):
        assert key in result


def test_forecast_month_end_vai_fechar_negativo(user_id):
    """Despesa real > receita → vai_fechar_negativo."""
    db.add_launch_and_update_balance(user_id, "receita", 50, None, "seed")
    db.add_launch_and_update_balance(user_id, "despesa", 200, "x", "x", categoria="outros")

    result = _forecast_month_end(user_id, {})
    assert result["vai_fechar_negativo"] is True


def test_forecast_month_end_conta_aportes_como_saida(user_id):
    """Aporte de investimento é movimentação interna mas SAI do caixa —
    forecast tem que contar pra refletir o saldo real."""
    db.add_launch_and_update_balance(user_id, "receita", 1000, None, "salario")
    # Aporte de R$ 500 — não é despesa real, mas é alocação que tira do caixa
    db.add_launch_and_update_balance(
        user_id, "despesa", 500, "carteira", "aporte",
        categoria="investimento_aporte",
        is_internal_movement=True,
    )

    result = _forecast_month_end(user_id, {})
    assert result["despesa_real_atual"] == 0.0  # nenhuma despesa real
    assert result["aportes_atual"] == 500.0
    assert result["saidas_atual"] == 500.0
    assert result["receita_atual"] == 1000.0
    # Saldo parcial = 1000 - 500 = 500 (positivo)
    assert result["saldo_parcial"] == 500.0


# ─── get_spending_trend summary (média, tendência) ──────────────────────────

def test_spending_trend_calcula_medias(user_id):
    """Cria gasto só no mês corrente → média = valor / N meses pedidos."""
    db.add_launch_and_update_balance(user_id, "despesa", 300, "x", "x", categoria="outros")
    db.add_launch_and_update_balance(user_id, "receita", 600, None, "seed")

    result = _get_spending_trend(user_id, {"months": 3})
    summary = result["summary"]
    # 300 / 1 mes com dado retornado
    # Mas só temos 1 row de dado (mês corrente) — média conta sobre os rows
    n_rows = len(result["data"])
    assert summary["media_despesa"] == round(300.0 / n_rows, 2)
    assert summary["media_receita"] == round(600.0 / n_rows, 2)


def test_spending_trend_tendencia_subindo(user_id):
    """Mês anterior com R$ 100, mês atual com R$ 300 → tendência 'subindo'."""
    today = date.today()
    # Mês anterior (calcular data)
    if today.month == 1:
        prev_y, prev_m = today.year - 1, 12
    else:
        prev_y, prev_m = today.year, today.month - 1
    prev_dt = datetime(prev_y, prev_m, 15)

    db.add_launch_and_update_balance(
        user_id, "despesa", 100, "x", "x", categoria="outros", criado_em=prev_dt,
    )
    db.add_launch_and_update_balance(
        user_id, "despesa", 300, "y", "y", categoria="outros",
    )

    result = _get_spending_trend(user_id, {"months": 2})
    assert result["summary"]["tendencia_despesa"] == "subindo"
    # 300 vs 100 = +200% de variação
    assert result["summary"]["variacao_pct"] > 10


def test_spending_trend_tendencia_estavel_com_1_mes(user_id):
    """1 mês só → não dá pra ter tendência."""
    db.add_launch_and_update_balance(user_id, "despesa", 100, "x", "x", categoria="outros")

    result = _get_spending_trend(user_id, {"months": 1})
    assert result["summary"]["tendencia_despesa"] == "estavel"
    assert result["summary"]["variacao_pct"] == 0.0


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
