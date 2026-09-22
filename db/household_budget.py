"""
db/household_budget.py — Orçamento Doméstico (método dos potes).

Modelo SEPARADO de `db/budgets.py` (category_budgets) de propósito: lá o limite
é um valor absoluto por categoria; aqui é um PERCENTUAL da renda do mês por pote
(Custos fixos, Conforto, Metas, Prazeres, Liberdade financeira, Conhecimento) —
a aba "Orçamento Doméstico" do dashboard. Os dois conceitos convivem sem se
tocar.

Decisões registradas:

- Percentuais são GLOBAIS por usuário (`household_budget_config`); só a renda
  tem override por mês (`household_budget_income`, chave 'YYYY-MM').
- Os 6 potes são fixos; o usuário edita só os percentuais. Defaults inspirados
  no método dos potes de T. Harv Eker, adaptados aos 6 potes do PigBank.
- Mapeamento categoria → pote é fixo (`CATEGORY_BUCKET`). Categoria custom/sem
  mapeamento cai em `DEFAULT_BUCKET = 'conforto'` — consequência conhecida e
  aceita na V1 (mapeamento editável é melhoria futura, fora de escopo).
- EXCEÇÃO DE MOVIMENTO INTERNO: aportes (`investimento_aporte`) são gravados
  como `is_internal_movement = true` (ver comentário em db/accounts.py sobre
  "categoria de movimento interno (pagamento_fatura, aporte)"). Na query de
  gasto daqui eles ENTRAM mesmo assim — no método dos potes o aporte É a
  alocação do pote Liberdade financeira; sem a exceção o pote ficaria sempre
  em R$ 0. `investimento_resgate` continua fora (é retorno, não alocação).
- Gasto do mês segue o mesmo critério de `get_budgets_status_for_month`
  (db/budgets.py): launches despesa por `criado_em` + credit_transactions pela
  fatura cujo `period_end` cai no mês, categoria casada via `cat_key_sql`.
- `save_config` é tudo-ou-nada: valida as 6 chaves, a faixa 0–100 e a soma 100
  ANTES de escrever, e faz o upsert das 6 rows numa única transação.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from .connection import (
    get_conn, cat_key_sql, TIPO_DESPESA_SQL, TIPO_RECEITA_SQL,
)
from .users import ensure_user


# ─── Potes ───────────────────────────────────────────────────────────────────
# Ordem de exibição = ordem da aba. Cores da referência visual (print do
# Orçamento Doméstico): azul claro, menta, amarelo, rosa, azul, laranja.
BUCKETS: list[dict[str, Any]] = [
    {"key": "custos_fixos",          "label": "Custos fixos",          "color": "#7dd3fc", "default_pct": 55},
    {"key": "conforto",              "label": "Conforto",              "color": "#6ee7b7", "default_pct": 5},
    {"key": "metas",                 "label": "Metas",                 "color": "#fde047", "default_pct": 10},
    {"key": "prazeres",              "label": "Prazeres",              "color": "#f0abfc", "default_pct": 10},
    {"key": "liberdade_financeira",  "label": "Liberdade financeira",  "color": "#93c5fd", "default_pct": 10},
    {"key": "conhecimento",          "label": "Conhecimento",          "color": "#fdba74", "default_pct": 10},
]
BUCKET_KEYS: set[str] = {b["key"] for b in BUCKETS}

# Mapeamento fixo categoria canônica → pote (semente: db/categories.py
# SYSTEM_CATEGORIES_SEED). Categoria fora deste mapa (custom, 'sem categoria')
# cai em DEFAULT_BUCKET. As chaves são NORMALIZADAS na montagem do dict (lower
# + sem acento, mesma tabela do `cat_key_sql` em db/connection.py) porque o
# gasto chega agregado por `cat_key_sql` — sem isso 'educação' nunca casava com
# a chave 'educacao' devolvida pela query e caía no fallback (medido).
_CATEGORY_BUCKET_DISPLAY: dict[str, str] = {
    "alimentação":         "custos_fixos",
    "mercado":             "custos_fixos",
    "moradia":             "custos_fixos",
    "transporte":          "custos_fixos",
    "saúde":               "custos_fixos",
    "assinaturas":         "custos_fixos",
    "compras online":      "conforto",
    "beleza":              "conforto",
    "pets":                "conforto",
    "outros":              "conforto",
    "lazer":               "prazeres",
    "educação":            "conhecimento",
    "investimento_aporte": "liberdade_financeira",
}
_CAT_ACCENTS_TR = str.maketrans(
    "áàâãäéèêëíìîïóòôõöúùûüç", "aaaaaeeeeiiiiooooouuuuc"
)
CATEGORY_BUCKET: dict[str, str] = {
    k.lower().translate(_CAT_ACCENTS_TR): v
    for k, v in _CATEGORY_BUCKET_DISPLAY.items()
}
DEFAULT_BUCKET = "conforto"

# Aporte é a ÚNICA categoria de movimento interno que conta como gasto aqui
# (ver docstring do módulo). Comparada pela mesma chave normalizada do resto.
_APORTE_KEY = "investimento_aporte"

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
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("RENDA_INVALIDA")
    if value.is_nan() or value < 0:
        raise ValueError("RENDA_INVALIDA")
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
    """Renda do mês → (amount, source): override se existir; senão a computada.

    A computada soma `launches` de receita do mês excluindo movimentos internos
    — mesmo critério do `monthly_income` de `get_financial_data` no dashboard,
    pra os dois números baterem.
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

def _spent_by_bucket(user_id: int, year: int, mon: int) -> dict[str, float]:
    """Gasto do mês agregado por pote (launches + cartão), via CATEGORY_BUCKET.

    O filtro de movimento interno tem a exceção do aporte: entra
    `is_internal_movement = false` OU categoria = investimento_aporte.
    """
    cat_l = cat_key_sql("categoria")
    cat_ct = cat_key_sql("ct.categoria")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                select cat, sum(total)::float as total from (
                  select {cat_l} as cat, sum(valor)::numeric as total
                  from launches
                  where user_id=%s
                    and {TIPO_DESPESA_SQL}
                    and (is_internal_movement = false
                         or {cat_l} = %s)
                    and date_part('year',  criado_em) = %s
                    and date_part('month', criado_em) = %s
                  group by {cat_l}
                  union all
                  select {cat_ct} as cat, sum(ct.valor)::numeric as total
                  from credit_transactions ct
                  join credit_bills b on b.id = ct.bill_id
                  where ct.user_id=%s
                    and ct.is_refund = false
                    and date_part('year',  b.period_end) = %s
                    and date_part('month', b.period_end) = %s
                  group by {cat_ct}
                ) s group by cat
                """,
                (user_id, _APORTE_KEY, year, mon, user_id, year, mon),
            )
            rows = cur.fetchall()
    out: dict[str, float] = {}
    for r in rows:
        bucket = CATEGORY_BUCKET.get(r["cat"], DEFAULT_BUCKET)
        out[bucket] = out.get(bucket, 0.0) + float(r["total"] or 0)
    return out


def get_household_budget_status(
    user_id: int, month: str | None = None
) -> dict[str, Any]:
    """Status do Orçamento Doméstico no mês: por pote, quanto da renda cabe ao
    pote vs quanto já foi gasto nele.

    Retorna:
      {
        "month": "YYYY-MM",
        "income": {"amount": 5000.0, "source": "override"|"computed"},
        "buckets": [
          {"key", "label", "color", "pct", "budget_amount", "spent",
           "remaining", "used_pct"},  # used_pct=None se budget_amount == 0
          ...
        ],
        "totals": {"spent", "budget_amount", "remaining", "used_pct"}
      }
    """
    ensure_user(user_id)
    month = _validate_month(month) if month else _current_month()
    year, mon = int(month[:4]), int(month[5:7])

    config = get_config(user_id)
    income_amount, income_source = get_monthly_income(user_id, month)
    spent = _spent_by_bucket(user_id, year, mon)

    buckets_out: list[dict[str, Any]] = []
    total_budget = 0.0
    total_spent = 0.0
    for b in BUCKETS:
        pct = float(config.get(b["key"], b["default_pct"]))
        budget_amount = round(income_amount * pct / 100.0, 2)
        bucket_spent = round(spent.get(b["key"], 0.0), 2)
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
