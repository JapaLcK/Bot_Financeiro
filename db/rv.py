"""db/rv.py — Renda variável (ações e FIIs) a partir do Open Finance.

MVP OF-first (Fase 1): NÃO há tabela própria nem entrada manual ainda. As posições
são derivadas em tempo de LEITURA de `open_finance_investments` — o espelho que o
sync do Pluggy já mantém (com o `raw` completo). Mesmo padrão de
`list_caixinha_candidates`: sem migração, sem escrever no hot-path do sync.
Read-only: a corretora é a fonte da verdade; o próximo sync atualiza os números.

Escopo do MVP: só Ações (EQUITY/STOCK) e FIIs (EQUITY/REAL_ESTATE_FUND).
ETF (type=ETF de topo), fundos (MUTUAL_FUND) e previdência (SECURITY) ficam de fora —
cada um é um branch a mais em `pluggy_rv_kind` + a query. Renda fixa
(FIXED_INCOME/CDB = caixinha do Banqueiro) é deliberadamente ignorada aqui.

Taxonomia/campos validados no sandbox do Pluggy em 2026-08-11 (connector 2):
FII veio como {type:EQUITY, subtype:REAL_ESTATE_FUND, code:GGRC11, quantity, value,
amount, balance}. `value` (preço unitário) pode vir null → fallback balance/quantity.
`quantity` pode vir 0/null → filtramos por valor de mercado > 0.
"""
from __future__ import annotations

import re
from decimal import InvalidOperation

from .connection import get_conn
from .of_snapshots import _numero
from .users import ensure_user

# Rótulos por classe (exibição no dashboard e na IA).
RV_KIND_LABELS = {"stock": "Ação", "fii": "FII"}


def pluggy_rv_kind(inv_type: str | None, subtype: str | None) -> str | None:
    """Classe de renda variável do MVP a partir de type/subtype do Pluggy.

    Retorna 'fii', 'stock' ou None (não é ação/FII → não entra no MVP).
    """
    t = (inv_type or "").upper()
    st = (subtype or "").upper()
    if t == "EQUITY":
        if st == "REAL_ESTATE_FUND":
            return "fii"
        # EQUITY/STOCK (ou EQUITY sem subtype: a maioria das corretoras manda ação
        # como EQUITY/STOCK, algumas omitem o subtype) → ação.
        return "stock"
    return None


def _f(value, default: float = 0.0) -> float:
    """Converte pra float tolerando None/Decimal/str inválida."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError, InvalidOperation):
        return default


def _custo(raw) -> float | None:
    """Custo de aquisição = `amountOriginal` do Pluggy; `amount` é o saldo e não
    serve. None = custo desconhecido (nunca 0). Parser: `of_snapshots._numero`."""
    d = _numero(raw.get("amountOriginal")) if isinstance(raw, dict) else None
    return float(d) if d is not None and d > 0 else None


def list_rv_positions(user_id: int) -> list[dict]:
    """Posições de renda variável (ações/FIIs) do usuário via Open Finance, já com
    preço de mercado e P&L. Só entra posição com valor de mercado > 0."""
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select i.id as of_investment_id, i.name, i.type, i.subtype,
                       i.balance, i.currency, i.raw
                from open_finance_investments i
                join open_finance_connections c on c.id = i.connection_id
                where c.user_id = %s
                  and upper(coalesce(i.type, '')) = 'EQUITY'
                order by i.balance desc nulls last
                """,
                (user_id,),
            )
            rows = [dict(r) for r in (cur.fetchall() or [])]

    out: list[dict] = []
    for r in rows:
        kind = pluggy_rv_kind(r.get("type"), r.get("subtype"))
        if not kind:
            continue
        raw = r.get("raw") or {}
        market_value = _f(r.get("balance"))
        if market_value <= 0:
            continue  # posição zerada/vendida — não exibe

        # Custo desconhecido => invested/pnl/pnl_pct/avg_price None (nunca 0).
        invested = _custo(raw)
        cost_known = invested is not None
        quantity = _f(raw.get("quantity")) or None

        # preço unitário: `value` pode vir null (validado no sandbox) → fallback.
        unit = raw.get("value")
        if unit is not None:
            market_price = _f(unit)
        elif quantity:
            market_price = market_value / quantity
        else:
            market_price = None

        pnl = (market_value - invested) if cost_known else None
        ticker = (raw.get("code") or raw.get("isin") or r.get("name") or "").upper()

        out.append({
            "of_investment_id": r.get("of_investment_id"),
            "ticker": ticker,
            "name": r.get("name"),
            "kind": kind,
            "kind_label": RV_KIND_LABELS.get(kind, kind),
            "quantity": quantity,
            "avg_price": (invested / quantity) if cost_known and quantity else None,
            "market_price": market_price,
            "market_value": market_value,
            "invested": invested,
            "cost_known": cost_known,
            "pnl": pnl,
            "pnl_pct": (pnl / invested) if cost_known else None,
            "currency": r.get("currency") or "BRL",
            "source": "open_finance",
            # rentabilidade que o Pluggy já entrega (quando o conector mandar).
            "last_month_rate": raw.get("lastMonthRate"),
            "twelve_months_rate": raw.get("lastTwelveMonthsRate"),
        })
    return out


def rv_portfolio_summary(user_id: int, positions: list[dict] | None = None) -> dict:
    """Totais da carteira de renda variável (valor de mercado, investido, P&L).

    Passe `positions` (de um `list_rv_positions` já feito) pra derivar o resumo do
    MESMO snapshot — sem isso relemos numa outra conexão e, se um sync commitar no
    meio, o resumo mistura lista antiga com totais novos (concentração até >100%).
    """
    pos = positions if positions is not None else list_rv_positions(user_id)
    mv = sum(p["market_value"] for p in pos)
    # Só é confiável quando TODAS as posições trazem custo; senão invested/pnl são None.
    cost_known = bool(pos) and all(p.get("cost_known") for p in pos)
    invested = sum(p["invested"] for p in pos) if cost_known else (None if pos else 0.0)
    pnl = (mv - invested) if invested is not None else None
    return {
        "market_value": mv,
        "invested": invested,
        "cost_known": cost_known,
        "pnl": pnl,
        "pnl_pct": (pnl / invested) if cost_known else (None if pos else 0.0),
        "count": len(pos),
    }


# ── Renda fixa do banco (Open Finance): CDB, Tesouro — NÃO vira caixinha ───────
# Decisão 2026-08-11: o Nubank (e outros) manda os investimentos com nome jurídico
# gigante e SEM apelido; não dá pra tratar como "caixinha". Então renda fixa do banco
# aparece aqui, na aba de Investimentos, agregada e read-only.

def _clean_of_name(name: str) -> str:
    """Nome legível pro investimento do banco (Pluggy manda o nome jurídico completo)."""
    n = (name or "").strip()
    up = n.upper()
    if up.startswith("CDB"):
        parts = [p.strip() for p in n.split(" - ")]
        if len(parts) > 1:
            issuer = re.sub(r"\b(S\.?/?A\.?|SOCIEDADE.*|FINANCIAMENTO.*|LTDA.*)\b.*", "", parts[1], flags=re.I)
            issuer = " ".join(w.capitalize() for w in issuer.split()[:3]).strip()
            return f"CDB · {issuer}" if issuer else "CDB"
        return "CDB"
    if up.startswith("TESOURO"):
        return n  # já limpo (ex.: "Tesouro IPCA+ 2032")
    if len(n) > 40:  # fundo/previdência com nome jurídico longo
        base = re.sub(r"\b(FUNDO DE INVESTIMENTO.*|RESPONSABILIDADE.*|LTDA.*|S\.?/?A\.?)\b.*", "", n, flags=re.I).strip()
        base = (base or n[:38]).strip()
        return (base[:38] + "…") if len(base) > 38 else base
    return n


def list_of_fixed_income(user_id: int, *, currency: str | None = None) -> list[dict]:
    """Renda fixa do banco (FIXED_INCOME: CDB/Tesouro) via Open Finance, agregada por
    nome limpo, read-only. Exclui o que virou caixinha (of_investment_id vinculado) e
    saldo <= 0. P&L = balance − amountOriginal (None sem custo). `currency` filtra antes de agregar; sem
    filtro preserva a consulta legada usada pelo dashboard."""
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select i.name, i.balance,
                       i.raw
                from open_finance_investments i
                join open_finance_connections c on c.id = i.connection_id
                where c.user_id = %s
                  and upper(coalesce(i.type, '')) = 'FIXED_INCOME'
                  and coalesce(i.balance, 0) > 0
                  and (%s::text is null or upper(coalesce(i.currency, 'BRL')) = upper(%s))
                  and not exists (
                    select 1 from pockets p
                    where p.of_investment_id = i.id and p.user_id = %s
                  )
                """,
                (user_id, currency, currency, user_id),
            )
            rows = [dict(r) for r in (cur.fetchall() or [])]

    groups: dict[str, dict] = {}
    for r in rows:
        label = _clean_of_name(r.get("name"))
        cost = _custo(r.get("raw"))
        g = groups.setdefault(label, {"name": label, "balance": 0.0, "invested": 0.0, "count": 0, "cost_known": True})
        g["balance"] += _f(r.get("balance"))
        g["count"] += 1
        if cost is None:
            g["cost_known"] = False
        else:
            g["invested"] += cost

    out = []
    for g in groups.values():
        if g["cost_known"]:
            pnl = g["balance"] - g["invested"]
            out.append({**g, "pnl": pnl, "pnl_pct": (pnl / g["invested"]) if g["invested"] > 0 else 0.0})
        else:
            out.append({**g, "invested": None, "pnl": None, "pnl_pct": None})
    out.sort(key=lambda x: x["balance"], reverse=True)
    return out


def of_fixed_income_summary(user_id: int, *, currency: str | None = None, items: list[dict] | None = None) -> dict:
    """`items` (de um `list_of_fixed_income` já feito) deriva o resumo do mesmo snapshot."""
    if items is None:
        items = list_of_fixed_income(user_id, currency=currency)
    bal = sum(i["balance"] for i in items)
    cost_known = bool(items) and all(i["cost_known"] for i in items)
    inv = sum(i["invested"] for i in items) if cost_known else (None if items else 0.0)
    return {
        "balance": bal, "invested": inv, "cost_known": cost_known,
        "pnl": (bal - inv) if inv is not None else (None if items else 0.0),
        "count": sum(i["count"] for i in items),
    }
