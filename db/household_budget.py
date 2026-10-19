"""
db/household_budget.py — Orçamento Doméstico (método dos potes).

Modelo SEPARADO de `db/budgets.py` de propósito: lá o limite é valor absoluto
por categoria; aqui é PERCENTUAL da renda do mês por pote. Decisões:

- Percentuais globais por usuário; só a renda tem override por mês ('YYYY-MM').
- 6 potes fixos, defaults inspirados no método de T. Harv Eker; mapeamento
  categoria → pote fixo (`CATEGORY_BUCKET`), sem mapeamento cai em 'conforto'.
- Pote de cada linha: `_pote` em `db/household_budget_linhas.py` (tabela lá).
- A leitura é em nível de LINHA (`_month_rows`) e o `spent` de cada pote é a
  soma dessas mesmas linhas que vão em `launches` — card e lista não divergem.
- Gasto do mês = critério de `get_budgets_status_for_month` (launches por
  criado_em + credit_transactions pela fatura do mês, `cat_key_sql`).
- `save_config` é tudo-ou-nada: valida ANTES, upsert das 6 rows numa transação.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from utils_text import TETO_ENTRADA, valor_finito

from .connection import get_conn, TIPO_RECEITA_SQL
from .household_budget_linhas import (  # noqa: F401 — CATEGORY_BUCKET/_month_rows reexportados
    CATEGORY_BUCKET, DEFAULT_BUCKET, _bucket_categories, _month_rows,
)
from .users import ensure_user


# ─── Potes ───────────────────────────────────────────────────────────────────
# Ordem de exibição = ordem da aba; cores da referência visual do produto.
BUCKETS: list[dict[str, Any]] = [
    {"key": "custos_fixos",          "label": "Custos fixos",          "color": "#7dd3fc", "default_pct": 55},
    {"key": "conforto",              "label": "Conforto",              "color": "#6ee7b7", "default_pct": 5},
    {"key": "metas",                 "label": "Metas",                 "color": "#fde047", "default_pct": 10},
    {"key": "prazeres",              "label": "Prazeres",              "color": "#f0abfc", "default_pct": 10},
    {"key": "liberdade_financeira",  "label": "Liberdade financeira",  "color": "#93c5fd", "default_pct": 10},
    {"key": "conhecimento",          "label": "Conhecimento",          "color": "#fdba74", "default_pct": 10},
]
BUCKET_KEYS: set[str] = {b["key"] for b in BUCKETS}


_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _validate_month(month: str) -> str:
    if not isinstance(month, str) or not _MONTH_RE.match(month):
        raise ValueError("MES_INVALIDO")
    return month


def _current_month() -> str:
    today = date.today()
    return f"{today.year:04d}-{today.month:02d}"


def _coerce_pct(value: Any) -> Decimal:
    """pct como Decimal; qualquer coisa não numérica ou fora de 0–100 falha."""
    try:
        pct = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("PCT_INVALIDO")
    if pct.is_nan() or pct < 0 or pct > 100:
        raise ValueError("PCT_INVALIDO")
    return pct


def _seed_defaults(cur, user_id: int) -> None:
    """Seed lazy idempotente: duas primeiras leituras simultâneas não brigam."""
    for b in BUCKETS:
        cur.execute(
            "insert into household_budget_config (user_id, bucket, pct) "
            "values (%s, %s, %s) on conflict (user_id, bucket) do nothing",
            (user_id, b["key"], Decimal(str(b["default_pct"]))),
        )


def get_config(user_id: int) -> dict[str, float]:
    """Percentual por pote. Semeia os defaults na 1ª leitura."""
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select bucket, pct from household_budget_config where user_id=%s",
                (user_id,),
            )
            rows = cur.fetchall()
            if not rows:
                _seed_defaults(cur, user_id)
                conn.commit()
                cur.execute(
                    "select bucket, pct from household_budget_config "
                    "where user_id=%s",
                    (user_id,),
                )
                rows = cur.fetchall()
    return {r["bucket"]: float(r["pct"]) for r in rows}


def save_config(user_id: int, pcts: dict[str, Any]) -> dict[str, float]:
    """Salva os 6 percentuais. Tudo-ou-nada: valida ANTES, upsert numa transação.

    Erros: BUCKET_DESCONHECIDO (chave fora dos 6 potes ou conjunto incompleto),
    PCT_INVALIDO (fora de 0–100 ou não numérico), PCT_SOMA_INVALIDA (soma ≠ 100,
    comparada em centésimos inteiros).
    """
    ensure_user(user_id)
    if not isinstance(pcts, dict) or set(pcts.keys()) != BUCKET_KEYS:
        raise ValueError("BUCKET_DESCONHECIDO")
    parsed = {k: _coerce_pct(v) for k, v in pcts.items()}
    # Centésimos inteiros: evita que 0.1+0.2 vire falso negativo na soma.
    total_cent = sum(int(p * 100) for p in parsed.values())
    if total_cent != 10000:
        raise ValueError("PCT_SOMA_INVALIDA")

    with get_conn() as conn:
        with conn.cursor() as cur:
            for b in BUCKETS:
                cur.execute(
                    "insert into household_budget_config (user_id, bucket, pct) "
                    "values (%s, %s, %s) "
                    "on conflict (user_id, bucket) "
                    "do update set pct = excluded.pct, updated_at = now()",
                    (user_id, b["key"], parsed[b["key"]]),
                )
        conn.commit()
    return {k: float(v) for k, v in parsed.items()}


# ─── Renda do mês ────────────────────────────────────────────────────────────

def set_income_override(user_id: int, month: str, amount: Any) -> float:
    """Override manual da renda do mês. amount < 0/não numérico → RENDA_INVALIDA
    (validado aqui; o CHECK do schema é só a última linha de defesa)."""
    ensure_user(user_id)
    month = _validate_month(month)
    if not valor_finito(amount, TETO_ENTRADA) or float(amount) < 0:  # 0 é renda válida
        raise ValueError("RENDA_INVALIDA")
    value = Decimal(str(amount))
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into household_budget_income (user_id, month, amount) "
                "values (%s, %s, %s) "
                "on conflict (user_id, month) "
                "do update set amount = excluded.amount, updated_at = now()",
                (user_id, month, value),
            )
        conn.commit()
    return float(value)


def clear_income_override(user_id: int, month: str) -> None:
    """Remove o override do mês — a renda volta a ser a computada."""
    ensure_user(user_id)
    month = _validate_month(month)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "delete from household_budget_income "
                "where user_id=%s and month=%s",
                (user_id, month),
            )
        conn.commit()


def get_monthly_income(user_id: int, month: str | None = None) -> tuple[float, str]:
    """Renda do mês → (amount, source): override se existir; senão a computada
    (soma de `launches` de receita do mês sem movimentos internos — mesmo
    critério do `monthly_income` do dashboard, pra os dois números baterem).
    """
    ensure_user(user_id)
    month = _validate_month(month) if month else _current_month()
    year, mon = int(month[:4]), int(month[5:7])
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select amount from household_budget_income "
                "where user_id=%s and month=%s",
                (user_id, month),
            )
            row = cur.fetchone()
            if row:
                return float(row["amount"]), "override"
            cur.execute(
                f"""
                select coalesce(sum(valor), 0)::float as total
                from launches
                where user_id=%s
                  and {TIPO_RECEITA_SQL}
                  and is_internal_movement = false
                  and date_part('year',  criado_em) = %s
                  and date_part('month', criado_em) = %s
                """,
                (user_id, year, mon),
            )
            return float(cur.fetchone()["total"] or 0), "computed"


# ─── Status do mês ───────────────────────────────────────────────────────────
def get_household_budget_status(
    user_id: int, month: str | None = None
) -> dict[str, Any]:
    """Status do mês: por pote, quanto da renda cabe a ele vs quanto já se gastou.

    Retorna {"month", "income": {amount, source}, "buckets": [{key, label,
    color, pct, budget_amount, spent, remaining, used_pct, categories,
    by_category[{categoria,total}], launches[{data,descricao,categoria,valor,
    origem}]}], "totals": {spent,
    budget_amount, remaining, used_pct}} — used_pct=None se budget_amount == 0.
    """
    ensure_user(user_id)
    month = _validate_month(month) if month else _current_month()
    year, mon = int(month[:4]), int(month[5:7])

    config = get_config(user_id)
    income_amount, income_source = get_monthly_income(user_id, month)
    rows = _month_rows(user_id, year, mon)

    buckets_out: list[dict[str, Any]] = []
    total_budget = 0.0
    total_spent = 0.0
    for b in BUCKETS:
        pct = float(config.get(b["key"], b["default_pct"]))
        budget_amount = round(income_amount * pct / 100.0, 2)
        mine = [r for r in rows if r["bucket"] == b["key"]]
        bucket_spent = round(sum(r["valor"] for r in mine), 2)
        by_cat: dict[str, dict[str, Any]] = {}
        for r in mine:
            c = by_cat.setdefault(r["key"], {"categoria": r["categoria"], "total": 0.0})
            c["total"] = round(c["total"] + r["valor"], 2)
        used_pct = (
            round(bucket_spent / budget_amount * 100.0, 2)
            if budget_amount > 0 else None
        )
        total_budget += budget_amount
        total_spent += bucket_spent
        buckets_out.append({
            "key": b["key"],
            "label": b["label"],
            "color": b["color"],
            "pct": pct,
            "budget_amount": budget_amount,
            "spent": bucket_spent,
            "remaining": round(budget_amount - bucket_spent, 2),
            "used_pct": used_pct,
            "categories": _bucket_categories(b["key"]),
            "by_category": sorted(by_cat.values(), key=lambda c: -c["total"]),
            "launches": [
                {"data": r["dia"].isoformat(), "descricao": r["descricao"],
                 "categoria": r["categoria"], "valor": r["valor"], "origem": r["origem"]}
                for r in sorted(mine, key=lambda r: (r["dia"], r["valor"]), reverse=True)
            ],
        })

    total_budget = round(total_budget, 2)
    total_spent = round(total_spent, 2)
    return {
        "month": month,
        "income": {"amount": round(income_amount, 2), "source": income_source},
        "buckets": buckets_out,
        "totals": {
            "spent": total_spent,
            "budget_amount": total_budget,
            "remaining": round(total_budget - total_spent, 2),
            "used_pct": (
                round(total_spent / total_budget * 100.0, 2)
                if total_budget > 0 else None
            ),
        },
    }


__all__ = [
    "BUCKETS",
    "BUCKET_KEYS",
    "CATEGORY_BUCKET",
    "DEFAULT_BUCKET",
    "get_config",
    "save_config",
    "set_income_override",
    "clear_income_override",
    "get_monthly_income",
    "get_household_budget_status",
]
