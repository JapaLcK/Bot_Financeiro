"""
core/budget_alerts.py — Alertas de orcamento por categoria.

Disparados ao registrar uma despesa em categoria com orcamento setado em
`category_budgets`. Thresholds 80% / 100% / 120%; cada um dispara no
maximo uma vez por mes por categoria por usuario (dedup em
`budget_alert_sent`).

Cross-threshold jumps (ex.: gasto pula 30% -> 110% num lancamento):
dispara apenas o threshold mais alto cruzado, mas marca todos os
inferiores como enviados — evita o 80% disparar depois para a mesma
categoria/mes quando o user ja passou de 100%.

Falha sempre silenciosa: alerta nao deve quebrar o fluxo de registrar
o gasto. Erros de DB sao logados em stderr.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterable

from db.connection import get_conn
from utils_text import fmt_brl


THRESHOLDS = (80, 100, 120)
INTERNAL_CATEGORIES = {
    "investimento_aporte", "criptomoedas", "rendimentos",
}


@dataclass(frozen=True)
class BudgetAlert:
    threshold: int     # 80 | 100 | 120
    categoria: str     # nome da categoria com case original do orcamento
    spent: float       # gasto total no mes apos este lancamento
    budget: float      # limite mensal


def _ym(when: datetime) -> str:
    return when.strftime("%Y-%m")


def _crossed_thresholds(spent_before: float, spent_after: float, budget: float) -> list[int]:
    """Lista os thresholds (em %) que foram cruzados — `before < t <= after`."""
    if budget <= 0:
        return []
    crossed = []
    for t in THRESHOLDS:
        cutoff = budget * (t / 100.0)
        if spent_before < cutoff <= spent_after:
            crossed.append(t)
    return crossed


def _format_alert(threshold: int, categoria: str, spent: float, budget: float) -> str:
    pct = int(round(spent / budget * 100)) if budget > 0 else 0
    cat = categoria.capitalize()
    if threshold == 80:
        return (
            f"\n\n⚠️ {cat}: {pct}% do orçamento mensal usado "
            f"({fmt_brl(spent)} de {fmt_brl(budget)})."
        )
    if threshold == 100:
        return (
            f"\n\n🚨 {cat}: você atingiu o orçamento mensal "
            f"({fmt_brl(spent)} de {fmt_brl(budget)})."
        )
    # 120+
    excess = spent - budget
    return (
        f"\n\n🔥 {cat}: você passou em {fmt_brl(excess)} do orçamento mensal "
        f"({fmt_brl(spent)} de {fmt_brl(budget)})."
    )


def evaluate_after_expense(
    user_id: int,
    categoria: str | None,
    valor: float,
    criado_em: datetime,
) -> BudgetAlert | None:
    """
    Avalia se o gasto recem-registrado cruzou algum threshold de orcamento.

    Retorna o alerta a ser anexado a resposta de confirmacao, ou None.
    Marca os thresholds disparados/inferiores como enviados (dedup mensal).

    Pre-condicao do caller: `valor` e o valor BRUTO do gasto desta operacao,
    e `criado_em` ja e o timestamp registrado em launches. A query soma
    todos os gastos do mes (incluindo o atual) — `gasto_antes` e calculado
    subtraindo `valor`.
    """
    if not categoria:
        return None
    cat = (categoria or "").strip()
    if not cat or cat in INTERNAL_CATEGORIES:
        return None
    if valor is None or float(valor) <= 0:
        return None

    ym = _ym(criado_em)
    year, month = criado_em.year, criado_em.month

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "select categoria, budget from category_budgets "
                    "where user_id = %s and lower(categoria) = lower(%s)",
                    (user_id, cat),
                )
                bgt_row = cur.fetchone()
                if not bgt_row:
                    return None
                budget = float(bgt_row["budget"] or 0)
                cat_canon = bgt_row["categoria"]
                if budget <= 0:
                    return None

                cur.execute(
                    """
                    select coalesce(sum(valor), 0) as total
                    from launches
                    where user_id = %s
                      and tipo in ('despesa', 'saida')
                      and lower(categoria) = lower(%s)
                      and is_internal_movement = false
                      and date_part('year',  criado_em) = %s
                      and date_part('month', criado_em) = %s
                    """,
                    (user_id, cat_canon, year, month),
                )
                spent_after = float(cur.fetchone()["total"] or 0)
                spent_before = max(0.0, spent_after - float(valor))

                crossed = _crossed_thresholds(spent_before, spent_after, budget)
                if not crossed:
                    return None

                cur.execute(
                    "select threshold from budget_alert_sent "
                    "where user_id = %s and lower(categoria) = lower(%s) and ym = %s",
                    (user_id, cat_canon, ym),
                )
                already = {int(r["threshold"]) for r in cur.fetchall()}
                pending = [t for t in crossed if t not in already]
                if not pending:
                    return None

                top = max(pending)
                # Marca TODOS os thresholds <= top como enviados, ate
                # mesmo os que nao foram cruzados nesta operacao mas
                # ja foram superados (idempotencia entre meses).
                to_mark = [t for t in THRESHOLDS if t <= top and t not in already]
                for t in to_mark:
                    cur.execute(
                        "insert into budget_alert_sent (user_id, categoria, ym, threshold) "
                        "values (%s, %s, %s, %s) "
                        "on conflict do nothing",
                        (user_id, cat_canon, ym, t),
                    )
            conn.commit()
    except Exception as exc:
        print(f"[budget_alerts] eval failed for user {user_id} cat {cat}: {exc}", file=sys.stderr)
        return None

    return BudgetAlert(threshold=top, categoria=cat_canon, spent=spent_after, budget=budget)


def format_alert_text(alert: BudgetAlert) -> str:
    """Texto a ser ANEXADO a resposta de confirmacao do gasto."""
    return _format_alert(alert.threshold, alert.categoria, alert.spent, alert.budget)
