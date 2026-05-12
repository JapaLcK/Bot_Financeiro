"""
db/budgets.py — Orçamentos por categoria (`category_budgets`).

A tabela já existe (criada em `db/schema.py`) e era manipulada inline pelas
rotas da dashboard (`frontend/finance_bot_websocket_custom.py`) e pelo
`core/budget_alerts.py`. Os helpers aqui foram extraídos pra dar suporte às
tools da IA, mantendo o comportamento:

- `categoria` armazenada com case original; comparação case-insensitive.
- `unique (user_id, categoria)` no schema garante uma row por categoria.
- `budget > 0` (CHECK constraint no schema).

Lê também `list_user_categories` — categorias que o user JÁ USOU em
`launches` ou `credit_transactions`. Usado pelas tools pra detectar typo
(user pede orçamento de 'alimemtacao' quando os lançamentos usam
'alimentação'). Categorias internas (movimentação de investimento) são
filtradas — não fazem sentido como orçamento.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from .connection import get_conn
from .users import ensure_user


# Mesma lista que `core/budget_alerts.py` filtra como interna.
_INTERNAL_CATEGORIES = {
    "investimento_aporte",
    "investimento_resgate",
    "criptomoedas",
    "rendimentos",
}


def list_budgets(user_id: int) -> list[dict[str, Any]]:
    """Lista os orçamentos cadastrados (sem cruzar com gastos)."""
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select categoria, budget from category_budgets "
                "where user_id=%s order by lower(categoria)",
                (user_id,),
            )
            return [
                {"categoria": r["categoria"], "budget": float(r["budget"])}
                for r in cur.fetchall()
            ]


def get_budget(user_id: int, categoria: str) -> dict[str, Any] | None:
    """Busca orçamento de UMA categoria (case-insensitive). Retorna a row canônica."""
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select categoria, budget from category_budgets "
                "where user_id=%s and lower(categoria) = lower(%s)",
                (user_id, categoria),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {"categoria": row["categoria"], "budget": float(row["budget"])}


def upsert_budget(user_id: int, categoria: str, budget: float) -> tuple[str, bool]:
    """Cria ou atualiza orçamento. Retorna `(categoria_canonical, created)`.

    `created=True` se foi INSERT, False se foi UPDATE.
    Se já existe uma row com a mesma categoria (case-insensitive), mantém o
    case original do INSERT — não reescreve. Isso evita "alimentação" virar
    "Alimentação" só porque o user digitou diferente.
    """
    ensure_user(user_id)
    cat = (categoria or "").strip()
    if not cat:
        raise ValueError("CATEGORIA_INVALIDA")
    if budget is None or float(budget) <= 0:
        raise ValueError("BUDGET_INVALIDO")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select categoria from category_budgets "
                "where user_id=%s and lower(categoria) = lower(%s)",
                (user_id, cat),
            )
            existing = cur.fetchone()
            if existing:
                canon = existing["categoria"]
                cur.execute(
                    "update category_budgets set budget=%s "
                    "where user_id=%s and lower(categoria)=lower(%s)",
                    (Decimal(str(budget)), user_id, cat),
                )
                conn.commit()
                return canon, False

            cur.execute(
                "insert into category_budgets (user_id, categoria, budget) "
                "values (%s, %s, %s)",
                (user_id, cat, Decimal(str(budget))),
            )
            conn.commit()
            return cat, True


def delete_budget(user_id: int, categoria: str) -> bool:
    """Remove orçamento. Retorna True se removeu, False se não existia."""
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "delete from category_budgets "
                "where user_id=%s and lower(categoria) = lower(%s)",
                (user_id, categoria),
            )
            n = cur.rowcount
            conn.commit()
    return n > 0


def list_user_categories(user_id: int) -> list[str]:
    """Categorias distintas que o user JÁ USOU em launches/credit_transactions.

    Usado pelas tools pra detectar typo no `set_budget`. Filtra categorias
    internas (aportes de investimento etc) — não cabem como orçamento.

    Retorna o case canônico (lowercase do que tá no DB). Se a categoria
    apareceu com cases diferentes, ganha o mais recente.
    """
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select categoria from (
                    select lower(categoria) as cat_lower, categoria,
                           row_number() over (
                               partition by lower(categoria)
                               order by criado_em desc
                           ) as rn
                    from launches
                    where user_id=%s and categoria is not null
                      and is_internal_movement=false
                    union all
                    select lower(categoria) as cat_lower, categoria,
                           row_number() over (
                               partition by lower(categoria)
                               order by purchased_at desc
                           ) as rn
                    from credit_transactions
                    where user_id=%s and categoria is not null
                      and is_refund=false
                ) src
                where rn = 1
                """,
                (user_id, user_id),
            )
            seen: dict[str, str] = {}
            for r in cur.fetchall():
                cat = (r["categoria"] or "").strip()
                if not cat:
                    continue
                key = cat.lower()
                if key in _INTERNAL_CATEGORIES:
                    continue
                # Primeira ocorrência ganha (já estamos ordenando por data desc)
                seen.setdefault(key, cat)
            return list(seen.values())


def sum_spent_in_category_this_month(user_id: int, categoria: str) -> float:
    """Soma gasto da categoria no mês corrente (launches + credit_transactions).

    Espelha a query do `core/budget_alerts.py` mas inclui também
    `credit_transactions` (compras no cartão) — orçamento deve contar
    tudo, não só conta corrente.
    """
    ensure_user(user_id)
    today = date.today()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select
                  coalesce((
                    select sum(valor) from launches
                    where user_id=%s
                      and tipo in ('despesa', 'saida')
                      and lower(categoria) = lower(%s)
                      and is_internal_movement = false
                      and date_part('year',  criado_em) = %s
                      and date_part('month', criado_em) = %s
                  ), 0) +
                  coalesce((
                    select sum(valor) from credit_transactions
                    where user_id=%s
                      and lower(categoria) = lower(%s)
                      and is_refund = false
                      and date_part('year',  purchased_at) = %s
                      and date_part('month', purchased_at) = %s
                  ), 0) as total
                """,
                (
                    user_id, categoria, today.year, today.month,
                    user_id, categoria, today.year, today.month,
                ),
            )
            row = cur.fetchone()
            return float(row["total"] or 0)


__all__ = [
    "list_budgets",
    "get_budget",
    "upsert_budget",
    "delete_budget",
    "list_user_categories",
    "sum_spent_in_category_this_month",
]
