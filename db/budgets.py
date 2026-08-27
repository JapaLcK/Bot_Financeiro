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

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from .connection import get_conn, cat_key_sql, CAT_META_SQL, CAT_CANON_ORDER
from .users import ensure_user


# Casamento de categoria: `cat_key_sql` (fonte única, db/connection.py) — case-,
# acento-insensível E com o vazio colapsado em 'sem categoria'. Para categoria
# escrita é idêntico ao `cat_norm_sql`; só o vazio muda, e ele precisa mudar: o
# donut rotula a linha sem categoria como "sem categoria" e anexa o orçamento de
# mesmo nome, então um leitor que casasse a coluna crua dizia "0 gasto" no mesmo
# mês em que o donut mostrava a barra em 20% do orçamento (medido). Vale também
# para `category_budgets.categoria`, onde as duas expressões dão o mesmo valor —
# usar uma só evita a próxima divergência.
_CAT_EQ    = f"{cat_key_sql('categoria')} = {cat_key_sql('%s')}"
_CAT_CT_EQ = f"{cat_key_sql('ct.categoria')} = {cat_key_sql('%s')}"

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
                f"where user_id=%s and {_CAT_EQ}{CAT_CANON_ORDER}",
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
                f"where user_id=%s and {_CAT_EQ}{CAT_CANON_ORDER}",
                (user_id, cat),
            )
            existing = cur.fetchone()
            if existing:
                canon = existing["categoria"]
                # Pela grafia EXATA da canônica: com gêmeas legadas o
                # `{_CAT_EQ}` casa as duas e o update apagaria o limite da outra.
                cur.execute(
                    "update category_budgets set budget=%s "
                    "where user_id=%s and categoria=%s",
                    (Decimal(str(budget)), user_id, canon),
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
                f"where user_id=%s and {_CAT_EQ}",
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
                f"""
                select
                  coalesce((
                    select sum(valor) from launches
                    where user_id=%s
                      and tipo in ('despesa', 'saida')
                      and {_CAT_EQ}
                      and is_internal_movement = false
                      and date_part('year',  criado_em) = %s
                      and date_part('month', criado_em) = %s
                  ), 0) +
                  coalesce((
                    select sum(ct.valor)
                    from credit_transactions ct
                    join credit_bills b on b.id = ct.bill_id
                    where ct.user_id=%s
                      and {_CAT_CT_EQ}
                      and ct.is_refund = false
                      and date_part('year',  b.period_end) = %s
                      and date_part('month', b.period_end) = %s
                  ), 0) as total
                """,
                (
                    user_id, categoria, today.year, today.month,
                    user_id, categoria, today.year, today.month,
                ),
            )
            row = cur.fetchone()
            return float(row["total"] or 0)


def sum_spent_in_category_period(
    user_id: int, categoria: str, start_date: date, end_date: date
) -> float:
    """Soma o gasto de uma categoria num período arbitrário [start, end] inclusivo.

    Usado pela resposta "quanto gastei na categoria X" do bot — espelha a
    atribuição do DASHBOARD pra os números baterem:
      - launches: tipo='despesa', is_internal_movement=false, por criado_em
      - cartão: is_refund=false, atribuído ao período pelo MÊS DA FATURA
        (credit_bills.period_end). Assim um gasto parcelado conta uma parcela
        por mês, e não os R$ totais na data da compra.
    Comparação de categoria pela `cat_key_sql`: case-, acento-insensível E com o
    vazio colapsado em 'sem categoria', a MESMA chave da lista
    (`list_launches_by_category`) e do donut. Tem que ser a mesma porque o
    `_total_despesa` do bot (core/handlers/launches.py) subtrai um do outro e
    chama a diferença de movimentação interna: com `cat_norm_sql` cru aqui, uma
    despesa SEM categoria dava total 0 contra lista R$ 120,00, e o bot respondia
    "não conta como gasto — é movimentação interna" sobre um gasto de verdade.
    Para categoria escrita as duas expressões são idênticas; só o vazio muda.
    """
    ensure_user(user_id)
    start_dt = datetime.combine(start_date, datetime.min.time())
    end_excl = datetime.combine(end_date + timedelta(days=1), datetime.min.time())
    end_date_excl = end_date + timedelta(days=1)  # janela meio-aberta em period_end
    _cat = cat_key_sql("categoria")
    _cat_ct = cat_key_sql("ct.categoria")
    _arg = cat_key_sql("%s")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                select
                  coalesce((
                    select sum(valor) from launches
                    where user_id=%s
                      and tipo = 'despesa'
                      and {_cat} = {_arg}
                      and is_internal_movement = false
                      and criado_em >= %s
                      and criado_em <  %s
                  ), 0) +
                  coalesce((
                    select sum(ct.valor)
                    from credit_transactions ct
                    join credit_bills b on b.id = ct.bill_id
                    where ct.user_id=%s
                      and {_cat_ct} = {_arg}
                      and ct.is_refund = false
                      and b.period_end >= %s
                      and b.period_end <  %s
                  ), 0) as total
                """,
                (
                    user_id, categoria, start_dt, end_excl,
                    user_id, categoria, start_date, end_date_excl,
                ),
            )
            row = cur.fetchone()
            return float(row["total"] or 0)


def _parse_ym(month: str | None) -> tuple[int, int]:
    """Parse 'YYYY-MM' → (year, month). Default = mês corrente."""
    if not month:
        today = date.today()
        return today.year, today.month
    try:
        y, m = month.split("-", 1)
        return int(y), int(m)
    except (ValueError, AttributeError):
        today = date.today()
        return today.year, today.month


def get_budgets_status_for_month(
    user_id: int, month: str | None = None
) -> dict[str, Any]:
    """Status dos orçamentos no mês: gasto vs limite com cor semáforo por categoria.

    Retorna:
      {
        "month": "YYYY-MM",
        "budgets": [
          {
            "categoria": "alimentação",
            "emoji": "🍔", "color": "#f59e0b",
            "budget": 800.0, "spent": 412.30,
            "pct": 51.5, "status": "verde",  # verde<80, amarelo<100, vermelho>=100
            "remaining": 387.70
          }, ...
        ],
        "totals": {
          "budget": 2300.0, "spent": 1213.60,
          "pct": 52.8, "remaining": 1086.40,
          "at_risk": 1  # qtd de categorias amarela|vermelho
        }
      }
    """
    ensure_user(user_id)
    year, mon = _parse_ym(month)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                with budgets as (
                  select id, categoria, budget
                  from category_budgets
                  where user_id=%s
                ),
                spent_launches as (
                  select {cat_key_sql('categoria')} as cat, sum(valor)::numeric as total
                  from launches
                  where user_id=%s
                    and tipo in ('despesa', 'saida')
                    and is_internal_movement = false
                    and date_part('year',  criado_em) = %s
                    and date_part('month', criado_em) = %s
                  group by {cat_key_sql('categoria')}
                ),
                spent_cards as (
                  select {cat_key_sql('ct.categoria')} as cat, sum(ct.valor)::numeric as total
                  from credit_transactions ct
                  join credit_bills b on b.id = ct.bill_id
                  where ct.user_id=%s
                    and ct.is_refund = false
                    and date_part('year',  b.period_end) = %s
                    and date_part('month', b.period_end) = %s
                  group by {cat_key_sql('ct.categoria')}
                ),
                spent_all as (
                  select cat, sum(total) as total from (
                    select * from spent_launches
                    union all
                    select * from spent_cards
                  ) s group by cat
                ),
                cat_meta as (
                  {CAT_META_SQL}
                )
                select
                  b.categoria,
                  {cat_key_sql('b.categoria')} as cat_key,
                  b.budget::float as budget,
                  coalesce(sa.total, 0)::float as spent,
                  uc.emoji,
                  uc.color
                from budgets b
                left join spent_all sa on sa.cat = {cat_key_sql('b.categoria')}
                left join cat_meta uc on uc.cat = {cat_key_sql('b.categoria')}
                order by lower(b.categoria)
                """,
                (
                    user_id,
                    user_id, year, mon,
                    user_id, year, mon,
                    user_id,
                ),
            )
            rows = cur.fetchall() or []

    total_budget = 0.0
    total_spent = 0.0
    # Gêmeas legadas ('cafe' e 'café' na mesma conta) casam com o MESMO gasto:
    # cada linha mostra o gasto contra o próprio limite, mas o total soma o
    # gasto UMA vez por categoria normalizada. `total_budget` NÃO deduplica —
    # os dois limites foram criados pelo usuário.
    spent_counted: set[str] = set()
    at_risk_cats: set[str] = set()
    budgets_out: list[dict[str, Any]] = []
    for r in rows:
        budget = float(r["budget"] or 0)
        spent = float(r["spent"] or 0)
        pct = (spent / budget * 100.0) if budget > 0 else 0.0
        if pct >= 100:
            status = "vermelho"
        elif pct >= 80:
            status = "amarelo"
        else:
            status = "verde"
        if status != "verde":
            at_risk_cats.add(r["cat_key"])
        total_budget += budget
        if r["cat_key"] not in spent_counted:
            spent_counted.add(r["cat_key"])
            total_spent += spent
        budgets_out.append({
            "categoria": r["categoria"],
            "emoji": r["emoji"] or "🏷️",
            "color": r["color"] or "#7c3aed",
            "budget": round(budget, 2),
            "spent": round(spent, 2),
            "pct": round(pct, 1),
            "status": status,
            "remaining": round(budget - spent, 2),
        })

    total_pct = (total_spent / total_budget * 100.0) if total_budget > 0 else 0.0
    return {
        "month": f"{year:04d}-{mon:02d}",
        "budgets": budgets_out,
        "totals": {
            "budget": round(total_budget, 2),
            "spent": round(total_spent, 2),
            "pct": round(total_pct, 1),
            "remaining": round(total_budget - total_spent, 2),
            "at_risk": len(at_risk_cats),
        },
    }


__all__ = [
    "list_budgets",
    "get_budget",
    "upsert_budget",
    "delete_budget",
    "list_user_categories",
    "sum_spent_in_category_this_month",
    "get_budgets_status_for_month",
]
