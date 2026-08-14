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

        # `invested_known` é None quando o conector não mandou um custo utilizável;
        # nesse caso caímos pro valor de mercado (P&L 0) mas marcamos cost_known=False
        # pra quem consome não confundir "R$ 0,00 de resultado" com "custo desconhecido".
        # amount<=0 (ou 0 do conector) também é custo inútil: P&L viraria o valor de
        # mercado inteiro como "lucro" — então segue como desconhecido.
        invested_known = _f(raw.get("amount"), None)
        cost_known = invested_known is not None and invested_known > 0
        invested = invested_known if cost_known else market_value
        quantity = _f(raw.get("quantity")) or None

        # preço unitário: `value` pode vir null (validado no sandbox) → fallback.
        unit = raw.get("value")
        if unit is not None:
            market_price = _f(unit)
        elif quantity:
            market_price = market_value / quantity
        else:
            market_price = None

        pnl = market_value - invested
        ticker = (raw.get("code") or raw.get("isin") or r.get("name") or "").upper()

        out.append({
            "of_investment_id": r.get("of_investment_id"),
            "ticker": ticker,
            "name": r.get("name"),
            "kind": kind,
            "kind_label": RV_KIND_LABELS.get(kind, kind),
            "quantity": quantity,
            "avg_price": (invested / quantity) if quantity else None,
            "market_price": market_price,
            "market_value": market_value,
            "invested": invested,
            "cost_known": cost_known,
            "pnl": pnl,
            "pnl_pct": (pnl / invested) if invested > 0 else 0.0,
            "currency": r.get("currency") or "BRL",
            "source": "open_finance",
            # rentabilidade que o Pluggy já entrega (quando o conector mandar).
            "last_month_rate": raw.get("lastMonthRate"),
            "twelve_months_rate": raw.get("lastTwelveMonthsRate"),
        })
    return out


def rv_portfolio_summary(user_id: int) -> dict:
    """Totais da carteira de renda variável (valor de mercado, investido, P&L)."""
    pos = list_rv_positions(user_id)
    mv = sum(p["market_value"] for p in pos)
    invested = sum(p["invested"] for p in pos)
    pnl = mv - invested
    # Só é confiável quando TODAS as posições trazem custo (senão o P&L agregado
    # mistura custo real com fallback pro valor de mercado e subestima o retorno).
    cost_known = bool(pos) and all(p.get("cost_known") for p in pos)
    return {
        "market_value": mv,
        "invested": invested,
        "cost_known": cost_known,
        "pnl": pnl,
        "pnl_pct": (pnl / invested) if invested > 0 else 0.0,
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


def list_of_fixed_income(user_id: int) -> list[dict]:
    """Renda fixa do banco (FIXED_INCOME: CDB/Tesouro) via Open Finance, agregada por
    nome limpo, read-only. Exclui o que virou caixinha (of_investment_id vinculado) e
    saldo <= 0. P&L = balance − amount."""
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select i.name, i.balance,
                       nullif(i.raw->>'amount', '')::numeric as amount
                from open_finance_investments i
                join open_finance_connections c on c.id = i.connection_id
                where c.user_id = %s
                  and upper(coalesce(i.type, '')) = 'FIXED_INCOME'
                  and coalesce(i.balance, 0) > 0
                  and not exists (
                    select 1 from pockets p
                    where p.of_investment_id = i.id and p.user_id = %s
                  )
                """,
                (user_id, user_id),
            )
            rows = [dict(r) for r in (cur.fetchall() or [])]

    groups: dict[str, dict] = {}
    for r in rows:
        label = _clean_of_name(r.get("name"))
        bal = _f(r.get("balance"))
        inv = _f(r.get("amount"), bal)
        g = groups.setdefault(label, {"name": label, "balance": 0.0, "invested": 0.0, "count": 0})
        g["balance"] += bal
        g["invested"] += inv
        g["count"] += 1

    out = []
    for g in groups.values():
        pnl = g["balance"] - g["invested"]
        out.append({**g, "pnl": pnl, "pnl_pct": (pnl / g["invested"]) if g["invested"] > 0 else 0.0})
    out.sort(key=lambda x: x["balance"], reverse=True)
    return out


def of_fixed_income_summary(user_id: int) -> dict:
    items = list_of_fixed_income(user_id)
    bal = sum(i["balance"] for i in items)
    inv = sum(i["invested"] for i in items)
    return {"balance": bal, "invested": inv, "pnl": bal - inv, "count": sum(i["count"] for i in items)}
