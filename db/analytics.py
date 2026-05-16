"""
db/analytics.py — Agregações para a view "Análises" + Histórico paginado.

Funções aqui são SÍNCRONAS. O endpoint paraleliza chamando cada uma via
asyncio.to_thread + asyncio.gather (cada thread pega sua própria conn do
pool sync em db.connection.get_conn).

Convenção de período:
  - `from_date` (inclusive), `to_date` (exclusive) — datas (date, não datetime).
  - Para "últimos N meses" usar `resolve_window(months=N)` que retorna o
    intervalo [primeiro_dia_do_mês_N_atrás, primeiro_dia_do_próximo_mês).

Regra fechada em Sprint 3:
  Despesas no cartão de crédito (credit_transactions) são alocadas pelo mês
  em que a `credit_bills.period_end` cai — NÃO pela `purchased_at`. Isso
  reflete o "consumo" real do mês (parcelamento aparece distribuído).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from .connection import get_conn


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _first_of_month(y: int, m: int) -> date:
    return date(y, m, 1)


def _next_month(d: date) -> date:
    return date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)


def _add_months(d: date, n: int) -> date:
    """Soma n meses preservando o dia 1. n pode ser negativo."""
    total = (d.year * 12 + (d.month - 1)) + n
    y, m = divmod(total, 12)
    return date(y, m + 1, 1)


def resolve_window(
    months: int | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
) -> tuple[date, date]:
    """
    Retorna (start_inclusive, end_exclusive) cobrindo o período pedido.

    - Se `from_date` e `to_date` vierem: usa eles direto (end é exclusivo).
    - Senão usa rolling window de `months` meses CHEIOS terminando no mês
      atual (inclusive). Default = 6 meses.
    """
    if from_date and to_date:
        return (from_date, to_date)

    n = max(1, min(int(months or 6), 36))
    today = date.today()
    end_excl = _next_month(today)  # 1º dia do próximo mês
    start_incl = _add_months(end_excl, -n)
    return (start_incl, end_excl)


def _to_float(v: Any) -> float:
    if v is None:
        return 0.0
    if isinstance(v, Decimal):
        return float(v)
    return float(v)


# ─────────────────────────────────────────────────────────────────────────────
# KPIs — totais agregados do período + comparativo com período anterior
# ─────────────────────────────────────────────────────────────────────────────

def compute_kpis(user_id: int, from_date: date, to_date: date) -> dict:
    """
    Retorna {
      from, to,
      total_income, total_expense, net, savings_rate,
      transactions_count,
      prev: { total_income, total_expense, net, savings_rate },
      delta_pct: { income, expense, net },        # variação % vs período anterior
      peak_day: { date, total } | None,           # dia de maior despesa total
      largest_expense: { date, valor, alvo,
                         nota, categoria } | None # maior despesa individual
    }
    """
    span_days = (to_date - from_date).days
    prev_from = from_date - timedelta(days=span_days)
    prev_to = from_date

    def _totals(start: date, end: date) -> dict:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT tipo, SUM(valor) AS total, SUM(cnt) AS count
                    FROM (
                      SELECT tipo, valor, 1 AS cnt
                      FROM launches
                      WHERE user_id = %s
                        AND criado_em >= %s AND criado_em < %s
                        AND is_internal_movement = false
                        AND tipo IN ('receita', 'despesa', 'saida')
                      UNION ALL
                      SELECT 'despesa' AS tipo, ct.valor, 1 AS cnt
                      FROM credit_transactions ct
                      JOIN credit_bills b ON b.id = ct.bill_id
                      WHERE ct.user_id = %s
                        AND ct.is_refund = false
                        AND b.period_end >= %s AND b.period_end < %s
                    ) merged
                    GROUP BY tipo
                    """,
                    (user_id, start, end, user_id, start, end),
                )
                rows = cur.fetchall()

        income = 0.0
        expense = 0.0
        count = 0
        for r in rows:
            t = (r["tipo"] or "").strip().lower()
            total = _to_float(r["total"])
            cnt = int(r["count"] or 0)
            count += cnt
            if t == "receita":
                income += total
            elif t in ("despesa", "saida"):
                expense += total

        net = income - expense
        savings_rate = (net / income) if income > 0 else 0.0
        return {
            "total_income": round(income, 2),
            "total_expense": round(expense, 2),
            "net": round(net, 2),
            "savings_rate": round(savings_rate, 4),
            "transactions_count": count,
        }

    def _highlights(start: date, end: date) -> tuple[dict | None, dict | None]:
        """Retorna (peak_day, largest_expense) — pode ser None se sem dados.

        Regra de alocação (consistente com total_expense — Sprint 3):
          - launches: filtra por `criado_em ∈ [start, end)`
          - credit_transactions: filtra por `bill.period_end ∈ [start, end)`
                                 (NÃO por purchased_at — senão compra de maio
                                 que fecha em junho ficaria fora do
                                 total_expense mas dentro do peak_day,
                                 quebrando a intuição "soma dos dias = total")

        A DATA exibida (`peak_day.date`, `largest_expense.date`) usa a data
        real da compra (`criado_em` / `purchased_at`) — é o que o user
        reconhece. Só o filtro do PERÍODO usa a regra da fatura.
        """
        with get_conn() as conn:
            with conn.cursor() as cur:
                # Peak day
                cur.execute(
                    """
                    SELECT day, SUM(valor) AS total
                    FROM (
                      SELECT DATE(criado_em) AS day, valor
                      FROM launches
                      WHERE user_id = %s
                        AND tipo IN ('despesa', 'saida')
                        AND is_internal_movement = false
                        AND criado_em >= %s AND criado_em < %s
                      UNION ALL
                      SELECT ct.purchased_at AS day, ct.valor
                      FROM credit_transactions ct
                      JOIN credit_bills b ON b.id = ct.bill_id
                      WHERE ct.user_id = %s
                        AND ct.is_refund = false
                        AND b.period_end >= %s AND b.period_end < %s
                    ) merged
                    GROUP BY day
                    ORDER BY total DESC
                    LIMIT 1
                    """,
                    (user_id, start, end, user_id, start, end),
                )
                row = cur.fetchone()
                peak = None
                if row and row["day"]:
                    peak = {
                        "date": row["day"].isoformat(),
                        "total": round(_to_float(row["total"]), 2),
                    }

                # Largest individual expense — junta launches + credit_tx,
                # devolve um único top 1 com metadados úteis.
                cur.execute(
                    """
                    SELECT day, valor, alvo, nota, categoria
                    FROM (
                      SELECT DATE(criado_em) AS day, valor,
                             alvo, nota, categoria
                      FROM launches
                      WHERE user_id = %s
                        AND tipo IN ('despesa', 'saida')
                        AND is_internal_movement = false
                        AND criado_em >= %s AND criado_em < %s
                      UNION ALL
                      SELECT ct.purchased_at AS day, ct.valor,
                             c.name AS alvo, ct.nota, ct.categoria
                      FROM credit_transactions ct
                      JOIN credit_cards c ON c.id = ct.card_id
                      JOIN credit_bills b ON b.id = ct.bill_id
                      WHERE ct.user_id = %s
                        AND ct.is_refund = false
                        AND b.period_end >= %s AND b.period_end < %s
                    ) merged
                    ORDER BY valor DESC
                    LIMIT 1
                    """,
                    (user_id, start, end, user_id, start, end),
                )
                row = cur.fetchone()
                largest = None
                if row:
                    largest = {
                        "date": row["day"].isoformat() if row["day"] else None,
                        "valor": round(_to_float(row["valor"]), 2),
                        "alvo": row["alvo"],
                        "nota": row["nota"],
                        "categoria": row["categoria"],
                    }

        return peak, largest

    cur = _totals(from_date, to_date)
    prev = _totals(prev_from, prev_to)
    peak_day, largest_expense = _highlights(from_date, to_date)

    def _pct(a: float, b: float) -> float | None:
        """Variação % de b → a. None se b é 0 (indefinido)."""
        if b == 0:
            return None
        return round(((a - b) / abs(b)) * 100, 1)

    return {
        "from": from_date.isoformat(),
        "to": to_date.isoformat(),
        **cur,
        "prev": prev,
        "delta_pct": {
            "income": _pct(cur["total_income"], prev["total_income"]),
            "expense": _pct(cur["total_expense"], prev["total_expense"]),
            "net": _pct(cur["net"], prev["net"]),
        },
        "peak_day": peak_day,
        "largest_expense": largest_expense,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Evolution — receita / despesa / net por mês (sempre buckets mensais)
# ─────────────────────────────────────────────────────────────────────────────

def compute_evolution(user_id: int, months: int = 6) -> list[dict]:
    """
    Retorna lista (do mais antigo pro mais recente):
      [{ month: "2026-01", income: X, expense: Y, net: Z }, ...]

    Sempre N meses cheios terminando no mês atual.
    Credit_transactions alocadas por bill.period_end (consistência Sprint 3).
    """
    from_date, to_date = resolve_window(months=months)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT mes, tipo, SUM(valor) AS total
                FROM (
                  SELECT TO_CHAR(DATE_TRUNC('month', criado_em), 'YYYY-MM') AS mes,
                         tipo, valor
                  FROM launches
                  WHERE user_id = %s
                    AND criado_em >= %s AND criado_em < %s
                    AND is_internal_movement = false
                    AND tipo IN ('receita', 'despesa', 'saida')
                  UNION ALL
                  SELECT TO_CHAR(DATE_TRUNC('month', b.period_end), 'YYYY-MM') AS mes,
                         'despesa' AS tipo, ct.valor
                  FROM credit_transactions ct
                  JOIN credit_bills b ON b.id = ct.bill_id
                  WHERE ct.user_id = %s
                    AND ct.is_refund = false
                    AND b.period_end >= %s AND b.period_end < %s
                ) merged
                GROUP BY mes, tipo
                ORDER BY mes
                """,
                (user_id, from_date, to_date, user_id, from_date, to_date),
            )
            rows = cur.fetchall()

    # Garante que TODOS os N meses aparecem, mesmo sem dados.
    n = max(1, min(int(months or 6), 36))
    buckets: dict[str, dict] = {}
    cursor = _add_months(_next_month(date.today()), -n)
    for _ in range(n):
        key = f"{cursor.year:04d}-{cursor.month:02d}"
        buckets[key] = {"month": key, "income": 0.0, "expense": 0.0, "net": 0.0}
        cursor = _next_month(cursor)

    for r in rows:
        k = r["mes"]
        if k not in buckets:
            continue
        t = (r["tipo"] or "").strip().lower()
        v = _to_float(r["total"])
        if t == "receita":
            buckets[k]["income"] += v
        elif t in ("despesa", "saida"):
            buckets[k]["expense"] += v

    for b in buckets.values():
        b["net"] = round(b["income"] - b["expense"], 2)
        b["income"] = round(b["income"], 2)
        b["expense"] = round(b["expense"], 2)

    return list(buckets.values())


# ─────────────────────────────────────────────────────────────────────────────
# Categories — distribuição de despesas por categoria no período
# ─────────────────────────────────────────────────────────────────────────────

def compute_categories(
    user_id: int,
    from_date: date,
    to_date: date,
    limit: int = 10,
) -> list[dict]:
    """
    Retorna lista ordenada por total DESC:
      [{ name, total, count, pct, emoji, color }, ...]

    `pct` = participação no total de despesas do período (0-100).
    Junta launches (tipo=despesa, !internal) + credit_transactions
    (alocadas por bill.period_end). Faz LEFT JOIN com user_categories
    pra trazer emoji/color quando o usuário customizou.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH despesas AS (
                  SELECT COALESCE(NULLIF(categoria, ''), 'sem categoria') AS categoria,
                         valor
                  FROM launches
                  WHERE user_id = %s
                    AND tipo IN ('despesa', 'saida')
                    AND is_internal_movement = false
                    AND criado_em >= %s AND criado_em < %s
                  UNION ALL
                  SELECT COALESCE(NULLIF(ct.categoria, ''), 'sem categoria') AS categoria,
                         ct.valor
                  FROM credit_transactions ct
                  JOIN credit_bills b ON b.id = ct.bill_id
                  WHERE ct.user_id = %s
                    AND ct.is_refund = false
                    AND b.period_end >= %s AND b.period_end < %s
                ),
                agg AS (
                  SELECT categoria,
                         SUM(valor) AS total,
                         COUNT(*)   AS count
                  FROM despesas
                  GROUP BY categoria
                )
                SELECT a.categoria AS name,
                       a.total,
                       a.count,
                       uc.emoji,
                       uc.color
                FROM agg a
                LEFT JOIN user_categories uc
                  ON uc.user_id = %s AND LOWER(uc.name) = LOWER(a.categoria)
                ORDER BY a.total DESC
                LIMIT %s
                """,
                (
                    user_id, from_date, to_date,
                    user_id, from_date, to_date,
                    user_id, limit,
                ),
            )
            rows = cur.fetchall()

    total_sum = sum(_to_float(r["total"]) for r in rows) or 1.0
    out: list[dict] = []
    for r in rows:
        total = _to_float(r["total"])
        out.append({
            "name": r["name"],
            "total": round(total, 2),
            "count": int(r["count"] or 0),
            "pct": round((total / total_sum) * 100, 1),
            "emoji": r["emoji"],
            "color": r["color"],
        })
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Weekday pattern — total e média de despesa por dia da semana
# ─────────────────────────────────────────────────────────────────────────────

# EXTRACT(DOW FROM ts) → 0=Sun..6=Sat
_DOW_PT = ["dom", "seg", "ter", "qua", "qui", "sex", "sab"]


def compute_weekday_pattern(user_id: int, from_date: date, to_date: date) -> list[dict]:
    """
    Retorna 7 buckets (sempre na ordem seg→dom):
      [{ dow: 1, label: "seg", total: X, count: Y, avg: Z }, ...]

    `avg` = total / número de vezes que esse dia da semana caiu no período.
    Considera SÓ despesas (launches tipo=despesa !internal +
    credit_transactions, esta usando purchased_at — aqui faz mais sentido
    o dia da compra real, não o fechamento da fatura).
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT dow, SUM(valor) AS total, COUNT(*) AS count
                FROM (
                  SELECT EXTRACT(DOW FROM criado_em)::int AS dow, valor
                  FROM launches
                  WHERE user_id = %s
                    AND tipo IN ('despesa', 'saida')
                    AND is_internal_movement = false
                    AND criado_em >= %s AND criado_em < %s
                  UNION ALL
                  SELECT EXTRACT(DOW FROM ct.purchased_at)::int AS dow, ct.valor
                  FROM credit_transactions ct
                  WHERE ct.user_id = %s
                    AND ct.is_refund = false
                    AND ct.purchased_at >= %s AND ct.purchased_at < %s
                ) merged
                GROUP BY dow
                """,
                (user_id, from_date, to_date, user_id, from_date, to_date),
            )
            rows = {int(r["dow"]): r for r in cur.fetchall()}

    # Quantos dias de cada DOW caem no período [from, to)?
    # Python weekday(): 0=seg..6=dom. Postgres EXTRACT(DOW): 0=dom..6=sab.
    # Conversão: dow_pg = (weekday() + 1) % 7  →  seg=1, ter=2, ..., dom=0.
    counts_per_dow = [0] * 7
    d = from_date
    while d < to_date:
        dow_pg = (d.weekday() + 1) % 7
        counts_per_dow[dow_pg] += 1
        d = d + timedelta(days=1)

    # Ordem de retorno: seg, ter, qua, qui, sex, sab, dom (1,2,3,4,5,6,0).
    order = [1, 2, 3, 4, 5, 6, 0]
    out: list[dict] = []
    for dow in order:
        row = rows.get(dow)
        total = _to_float(row["total"]) if row else 0.0
        count = int(row["count"] or 0) if row else 0
        days_in_period = counts_per_dow[dow] or 1
        avg = total / days_in_period
        out.append({
            "dow": dow,
            "label": _DOW_PT[dow],
            "total": round(total, 2),
            "count": count,
            "avg": round(avg, 2),
        })
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Top merchants — junta launches + credit_transactions
# ─────────────────────────────────────────────────────────────────────────────

def compute_top_merchants(
    user_id: int,
    from_date: date,
    to_date: date,
    limit: int = 10,
) -> list[dict]:
    """
    Retorna lista ordenada por total DESC:
      [{ name, total, count, sources: { debito: X, credito: Y } }, ...]

    `name` = COALESCE(launches.alvo, launches.nota) pra débito;
            credit_transactions.nota pra crédito. Normalizado por
            LOWER(TRIM(...)) pra agregar "iFood" e "ifood ".
    `sources` mostra quanto veio de débito vs crédito (útil pra UI).
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH merged AS (
                  SELECT LOWER(TRIM(COALESCE(NULLIF(alvo, ''), NULLIF(nota, ''), 'sem nome'))) AS key,
                         COALESCE(NULLIF(alvo, ''), NULLIF(nota, ''), 'Sem nome')             AS display,
                         valor,
                         'debito' AS source
                  FROM launches
                  WHERE user_id = %s
                    AND tipo IN ('despesa', 'saida')
                    AND is_internal_movement = false
                    AND criado_em >= %s AND criado_em < %s
                  UNION ALL
                  SELECT LOWER(TRIM(COALESCE(NULLIF(ct.nota, ''), 'sem nome')))    AS key,
                         COALESCE(NULLIF(ct.nota, ''), 'Sem nome')                  AS display,
                         ct.valor,
                         'credito' AS source
                  FROM credit_transactions ct
                  JOIN credit_bills b ON b.id = ct.bill_id
                  WHERE ct.user_id = %s
                    AND ct.is_refund = false
                    AND b.period_end >= %s AND b.period_end < %s
                )
                SELECT key,
                       -- pega o display mais "limpo" (não-nulo, primeira ocorrência)
                       (ARRAY_AGG(display ORDER BY display))[1] AS display,
                       SUM(valor) AS total,
                       COUNT(*)   AS count,
                       SUM(CASE WHEN source = 'debito'  THEN valor ELSE 0 END) AS debito_total,
                       SUM(CASE WHEN source = 'credito' THEN valor ELSE 0 END) AS credito_total
                FROM merged
                WHERE key <> 'sem nome'
                GROUP BY key
                ORDER BY total DESC
                LIMIT %s
                """,
                (user_id, from_date, to_date, user_id, from_date, to_date, limit),
            )
            rows = cur.fetchall()

    return [
        {
            "name": r["display"],
            "total": round(_to_float(r["total"]), 2),
            "count": int(r["count"] or 0),
            "sources": {
                "debito": round(_to_float(r["debito_total"]), 2),
                "credito": round(_to_float(r["credito_total"]), 2),
            },
        }
        for r in rows
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Histórico paginado — timeline com filtros
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# History — atalhos de filtro (KPIs do topo da view Histórico)
# ─────────────────────────────────────────────────────────────────────────────

def compute_history_quick_stats(
    user_id: int,
    from_date: date,
    to_date: date,
) -> dict:
    """
    Retorna stats agregados do período pros 4 cards do topo do Histórico:
      - total_count:       total de lançamentos (launches + credit_tx)
      - avg_per_month:     média de lançamentos por mês (total/n_meses)
      - receitas_count:    só receitas (launches tipo=receita)
      - despesas_count:    despesas (launches tipo=despesa + credit_tx)
      - months_in_period:  número de meses cobertos pelo período

    Despesas incluem credit_transactions (compras no cartão), alocadas por
    bill.period_end — mesma regra do total_expense (Sprint 3).
    """
    # Calcula número de meses cobertos pelo período [from, to)
    months_in_period = (
        (to_date.year - from_date.year) * 12
        + (to_date.month - from_date.month)
    )
    months_in_period = max(1, months_in_period)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                  SUM(CASE WHEN tipo = 'receita'                      THEN 1 ELSE 0 END) AS receitas,
                  SUM(CASE WHEN tipo IN ('despesa', 'saida', 'credito') THEN 1 ELSE 0 END) AS despesas,
                  COUNT(*) AS total
                FROM (
                  SELECT tipo
                  FROM launches
                  WHERE user_id = %s
                    AND criado_em >= %s AND criado_em < %s
                    AND is_internal_movement = false
                    AND tipo IN ('despesa', 'receita', 'saida')
                  UNION ALL
                  SELECT 'credito' AS tipo
                  FROM credit_transactions ct
                  JOIN credit_bills b ON b.id = ct.bill_id
                  WHERE ct.user_id = %s
                    AND ct.is_refund = false
                    AND b.period_end >= %s AND b.period_end < %s
                ) merged
                """,
                (user_id, from_date, to_date, user_id, from_date, to_date),
            )
            row = cur.fetchone() or {}
            receitas_count = int(row.get("receitas") or 0)
            despesas_count = int(row.get("despesas") or 0)
            total_count = int(row.get("total") or 0)

    avg_per_month = round(total_count / months_in_period, 1)

    return {
        "total_count": total_count,
        "avg_per_month": avg_per_month,
        "receitas_count": receitas_count,
        "despesas_count": despesas_count,
        "months_in_period": months_in_period,
    }


def list_history(
    user_id: int,
    from_date: date | None = None,
    to_date: date | None = None,
    categoria: str | None = None,
    tipo: str | None = None,
    q: str | None = None,
    uncategorized: bool = False,
    refunds_only: bool = False,
    page: int = 1,
    limit: int = 50,
) -> dict:
    """
    Retorna { items: [...], total, page, limit, total_pages }.

    Junta launches + credit_transactions (alocadas por bill.period_end).
    Filtros opcionais:
      - from_date / to_date  (faixa inclusiva → exclusiva)
      - categoria            (case-insensitive)
      - tipo                 ('despesa' | 'receita' | 'credito' | 'all')
      - q                    (busca textual livre — AND entre palavras,
                              OR entre campos: alvo, nota, categoria)
      - uncategorized        (só lançamentos sem categoria — atalho de
                              investigação)
      - refunds_only         (só estornos: credit_transactions.is_refund=true.
                              Implica tipo='credito' — refunds só existem
                              no cartão hoje)

    Filtro 'receita' exclui credit_transactions (todas são despesas no cartão).
    Filtro 'credito' devolve só credit_transactions.

    Busca textual: cada palavra digitada é casada contra alvo+nota+categoria
    via ILIKE (case-insensitive, substring). Múltiplas palavras viram AND
    (todas precisam aparecer em algum lugar). Ex.: "compra shopping" casa
    com nota="parcela de compra de roupa shopping".

    LIMITAÇÃO conhecida: ILIKE não normaliza acentos — "credito" não casa
    "crédito". Pra resolver definitivamente: `CREATE EXTENSION unaccent;`
    no Postgres e trocar pra `unaccent(...) ILIKE unaccent(%s)`. Anotado
    como melhoria pós-Sprint 6.
    """
    page = max(1, int(page or 1))
    limit = max(1, min(int(limit or 50), 200))
    offset = (page - 1) * limit
    tipo_norm = (tipo or "all").strip().lower()
    if tipo_norm not in ("despesa", "receita", "credito", "all"):
        tipo_norm = "all"

    # refunds_only força credit-only (refunds só vivem em credit_transactions)
    if refunds_only:
        tipo_norm = "credito"

    include_launches = tipo_norm in ("all", "despesa", "receita")
    include_credit = tipo_norm in ("all", "credito")

    # Quebra a busca em palavras (até 6 — limita custo da query).
    # Filtra tokens muito curtos pra evitar match excessivo (ex.: "a", "e").
    search_terms: list[str] = []
    if q:
        for raw in str(q).strip().split():
            term = raw.strip().lower()
            if len(term) >= 2:
                search_terms.append(term)
            if len(search_terms) >= 6:
                break

    def _search_clause(prefix: str) -> tuple[str, list[Any]]:
        """Retorna (SQL fragment, params) — todas as palavras AND'ed,
        cada palavra OR entre campos. `prefix` deixa o caller decidir o
        alias (ex.: '' pra launches, 'ct.' pra credit_transactions)."""
        if not search_terms:
            return ("", [])
        per_term_sqls: list[str] = []
        per_term_params: list[Any] = []
        for term in search_terms:
            pattern = f"%{term}%"
            per_term_sqls.append(
                f"(COALESCE({prefix}alvo, '') ILIKE %s "
                f"OR COALESCE({prefix}nota, '') ILIKE %s "
                f"OR COALESCE({prefix}categoria, '') ILIKE %s)"
            )
            per_term_params.extend([pattern, pattern, pattern])
        return (" AND ".join(per_term_sqls), per_term_params)

    # ── Sub-query de launches ────────────────────────────────────────────────
    launches_sql = ""
    launches_params: list[Any] = []
    if include_launches:
        clauses = ["user_id = %s"]
        launches_params.append(user_id)
        if from_date:
            clauses.append("criado_em >= %s")
            launches_params.append(from_date)
        if to_date:
            clauses.append("criado_em < %s")
            launches_params.append(to_date)
        if categoria:
            clauses.append("LOWER(COALESCE(categoria, '')) = LOWER(%s)")
            launches_params.append(categoria)
        if uncategorized:
            clauses.append("(categoria IS NULL OR categoria = '')")
        if tipo_norm in ("despesa", "receita"):
            clauses.append("tipo = %s")
            launches_params.append(tipo_norm)
        else:
            # 'all': mantém só despesa/receita (resto é movimentação interna,
            # criar_caixinha, etc.)
            clauses.append("tipo IN ('despesa', 'receita', 'saida')")
        clauses.append("is_internal_movement = false")
        search_sql, search_params = _search_clause("")
        if search_sql:
            clauses.append(search_sql)
            launches_params.extend(search_params)
        launches_sql = f"""
          SELECT id, tipo, valor, alvo, nota, categoria, criado_em
          FROM launches
          WHERE {" AND ".join(clauses)}
        """

    # ── Sub-query de credit_transactions ─────────────────────────────────────
    credit_sql = ""
    credit_params: list[Any] = []
    if include_credit:
        # is_refund: true se refunds_only, false caso contrário (default).
        clauses = ["ct.user_id = %s", f"ct.is_refund = {'true' if refunds_only else 'false'}"]
        credit_params.append(user_id)
        if from_date:
            clauses.append("b.period_end >= %s")
            credit_params.append(from_date)
        if to_date:
            clauses.append("b.period_end < %s")
            credit_params.append(to_date)
        if categoria:
            clauses.append("LOWER(COALESCE(ct.categoria, '')) = LOWER(%s)")
            credit_params.append(categoria)
        if uncategorized:
            clauses.append("(ct.categoria IS NULL OR ct.categoria = '')")
        # Pra credit_transactions, "alvo" no SELECT é c.name (alias de card);
        # mas a busca textual deve casar contra ct.nota e ct.categoria (e
        # opcionalmente nome do cartão também — útil pra "nubank").
        if search_terms:
            per_term_sqls: list[str] = []
            for term in search_terms:
                pattern = f"%{term}%"
                per_term_sqls.append(
                    "(COALESCE(c.name, '') ILIKE %s "
                    "OR COALESCE(ct.nota, '') ILIKE %s "
                    "OR COALESCE(ct.categoria, '') ILIKE %s)"
                )
                credit_params.extend([pattern, pattern, pattern])
            clauses.append(" AND ".join(per_term_sqls))
        credit_sql = f"""
          SELECT ct.id, 'credito' AS tipo, ct.valor,
                 c.name AS alvo, ct.nota, ct.categoria, ct.created_at AS criado_em
          FROM credit_transactions ct
          JOIN credit_cards c ON c.id = ct.card_id
          JOIN credit_bills b ON b.id = ct.bill_id
          WHERE {" AND ".join(clauses)}
        """

    # ── Une os ramos ────────────────────────────────────────────────────────
    if launches_sql and credit_sql:
        union_sql = f"({launches_sql}) UNION ALL ({credit_sql})"
        union_params = launches_params + credit_params
    elif launches_sql:
        union_sql = launches_sql
        union_params = launches_params
    elif credit_sql:
        union_sql = credit_sql
        union_params = credit_params
    else:
        # Caso teórico (tipo inválido com nenhum ramo). Retorna vazio.
        return {"items": [], "total": 0, "page": page, "limit": limit, "total_pages": 0}

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(*) AS total FROM ({union_sql}) merged",
                tuple(union_params),
            )
            total = int(cur.fetchone()["total"] or 0)

            cur.execute(
                f"""
                SELECT id, tipo, valor, alvo, nota, categoria, criado_em
                FROM ({union_sql}) merged
                ORDER BY criado_em DESC, id ASC
                LIMIT %s OFFSET %s
                """,
                tuple(union_params + [limit, offset]),
            )
            rows = cur.fetchall()

    items = [
        {
            "id": int(r["id"]),
            "tipo": r["tipo"],
            "valor": _to_float(r["valor"]),
            "alvo": r["alvo"],
            "nota": r["nota"],
            "categoria": r["categoria"],
            "criado_em": r["criado_em"].isoformat() if r["criado_em"] else None,
        }
        for r in rows
    ]
    total_pages = (total + limit - 1) // limit if total > 0 else 0
    return {
        "items": items,
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": total_pages,
    }
