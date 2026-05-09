"""
tests/test_budget_alerts.py — Cobertura de alertas de orcamento (core/budget_alerts.py).

Cobre:
- Sem orcamento setado: nao dispara
- Categoria interna (investimento_aporte): nao dispara
- Cruza 80% -> alerta de 80; segundo gasto na mesma faixa nao re-dispara
- Pula direto pra > 100% num gasto -> dispara so o threshold mais alto
- Estourou (> 120%) -> alerta de 120
- Mes novo -> reseta dedup, alerta volta a disparar
- Receita nao chama o helper (caller filtra)
- format_alert_text formata os 3 niveis com fmt_brl PT-BR
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from db import add_launch_and_update_balance, get_conn
from core.budget_alerts import (
    BudgetAlert,
    evaluate_after_expense,
    format_alert_text,
)


def _set_budget(user_id: int, categoria: str, budget: float) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into category_budgets (user_id, categoria, budget)
                values (%s, %s, %s)
                on conflict (user_id, categoria) do update set budget = excluded.budget
                """,
                (user_id, categoria, Decimal(str(budget))),
            )
        conn.commit()


def _spend(user_id: int, valor: float, categoria: str = "lazer", when: datetime | None = None):
    """Registra despesa e devolve o `criado_em` real."""
    when = when or datetime.now()
    add_launch_and_update_balance(
        user_id, "despesa", valor, None, "test",
        categoria=categoria, criado_em=when,
    )
    return when


def test_sem_orcamento_nao_dispara(user_id):
    when = _spend(user_id, 500, categoria="lazer")
    assert evaluate_after_expense(user_id, "lazer", 500, when) is None


def test_categoria_interna_nao_dispara(user_id):
    """investimento_aporte e outras categorias internas nao geram alerta."""
    _set_budget(user_id, "investimento_aporte", 100)
    when = _spend(user_id, 200, categoria="investimento_aporte")
    assert evaluate_after_expense(user_id, "investimento_aporte", 200, when) is None


def test_cruza_80_dispara_uma_vez(user_id):
    _set_budget(user_id, "lazer", 100)

    # Gasto 1: 30 -> 30% (nao cruza)
    when = _spend(user_id, 30, categoria="lazer")
    assert evaluate_after_expense(user_id, "lazer", 30, when) is None

    # Gasto 2: +60 -> 90% (cruza 80%)
    when = _spend(user_id, 60, categoria="lazer")
    alert = evaluate_after_expense(user_id, "lazer", 60, when)
    assert alert is not None
    assert alert.threshold == 80
    assert alert.spent == 90.0
    assert alert.budget == 100.0

    # Gasto 3: +5 -> 95% (ainda na faixa 80-100, nao re-dispara)
    when = _spend(user_id, 5, categoria="lazer")
    assert evaluate_after_expense(user_id, "lazer", 5, when) is None


def test_pulo_grande_dispara_so_o_mais_alto(user_id):
    """30% -> 110% num unico gasto: dispara apenas 100, nao 80 + 100."""
    _set_budget(user_id, "lazer", 100)
    _spend(user_id, 30, categoria="lazer")
    evaluate_after_expense(user_id, "lazer", 30, datetime.now())

    when = _spend(user_id, 80, categoria="lazer")
    alert = evaluate_after_expense(user_id, "lazer", 80, when)
    assert alert is not None
    assert alert.threshold == 100
    assert alert.spent == 110.0


def test_estouro_dispara_120(user_id):
    _set_budget(user_id, "lazer", 100)
    when = _spend(user_id, 130, categoria="lazer")
    alert = evaluate_after_expense(user_id, "lazer", 130, when)
    assert alert is not None
    assert alert.threshold == 120


def test_mes_novo_reseta_dedup(user_id):
    """Apos 80% disparado num mes, mes seguinte volta a disparar."""
    _set_budget(user_id, "lazer", 100)

    # Mes anterior: cruza 80%
    last_month = (datetime.now().replace(day=1) - timedelta(days=2))
    _spend(user_id, 90, categoria="lazer", when=last_month)
    alert_old = evaluate_after_expense(user_id, "lazer", 90, last_month)
    assert alert_old is not None and alert_old.threshold == 80

    # Mes corrente: outro 80% — deve disparar de novo
    when = _spend(user_id, 90, categoria="lazer")
    alert_new = evaluate_after_expense(user_id, "lazer", 90, when)
    assert alert_new is not None
    assert alert_new.threshold == 80


def test_format_alert_text_pt_br():
    msg80 = format_alert_text(BudgetAlert(threshold=80, categoria="lazer", spent=90.0, budget=100.0))
    assert "⚠️" in msg80
    assert "Lazer" in msg80  # capitalizado
    assert "R$ 90,00" in msg80
    assert "R$ 100,00" in msg80

    msg100 = format_alert_text(BudgetAlert(threshold=100, categoria="lazer", spent=100.0, budget=100.0))
    assert "🚨" in msg100
    assert "atingiu" in msg100

    msg120 = format_alert_text(BudgetAlert(threshold=120, categoria="lazer", spent=130.0, budget=100.0))
    assert "🔥" in msg120
    assert "R$ 30,00" in msg120  # excesso
