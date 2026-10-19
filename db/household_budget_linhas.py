"""
db/household_budget_linhas.py — linhas do mês do Orçamento Doméstico, por pote.

Mapeamento categoria → pote e leitura das linhas (launches + cartão). Usado por
`db/household_budget.py`, que soma essas linhas no status do mês.

- Pote de cada linha (`_pote`, ÚNICO lugar; launches e cartão), em ordem:
  1. tipo `deposito_caixinha` → metas; `aporte_investimento` → liberdade.
  2. chave de investimento (`investimento_aporte` ou `contains_word` com
     `INVESTMENT_CATEGORY_HINTS`, como `is_internal_category`) → liberdade,
     interna ou não (o aporte É a alocação do pote; 'Tesouro Selic' idem).
  3. interna e não investimento (ajuste_saldo, transferências...) → fora.
  4. não interna e em `CATEGORY_BUCKET` → o pote do mapa.
  5. não interna e fora do mapa → conforto.
  Saque de caixinha, resgate e `rendimentos` (não é despesa) ficam fora.
"""
from __future__ import annotations

from typing import Any

from utils_text import INVESTMENT_CATEGORY_HINTS, contains_word

from .connection import CAT_VAZIA_LABEL, get_conn, cat_key_sql, TIPO_DESPESA_SQL


# Mapeamento fixo categoria canônica → pote. As chaves são NORMALIZADAS na
# montagem do dict (lower + sem acento, mesma tabela do `cat_key_sql`) porque o
# gasto chega agregado por `cat_key_sql` — sem isso 'educação' nunca casava com
# a chave 'educacao' da query e caía no fallback (medido).
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
    "criptomoedas":        "liberdade_financeira",
}
_CAT_ACCENTS_TR = str.maketrans(
    "áàâãäéèêëíìîïóòôõöúùûüç", "aaaaaeeeeiiiiooooouuuuc"
)
CATEGORY_BUCKET: dict[str, str] = {
    k.lower().translate(_CAT_ACCENTS_TR): v
    for k, v in _CATEGORY_BUCKET_DISPLAY.items()
}
_DISPLAY_BY_KEY = {k.lower().translate(_CAT_ACCENTS_TR): k for k in _CATEGORY_BUCKET_DISPLAY}
DEFAULT_BUCKET = "conforto"
_APORTE_KEY = "investimento_aporte"

# Launches sem categoria que caem num pote pelo TIPO (rótulo exibido incluso).
_TIPO_BUCKET = {"deposito_caixinha": "metas", "aporte_investimento": "liberdade_financeira"}
_TIPO_LABEL = {"deposito_caixinha": "Depósitos em caixinhas",
               "aporte_investimento": "Aportes em investimentos"}
_CAT_LABEL = {"investimento_aporte": "investimentos", "investments": "investimentos"}  # nome de exibição ≠ chave


def _pote(tipo: str, key: str, interno: bool) -> str | None:
    """Pote da linha, ou None se não conta. Tabela no docstring do módulo."""
    if tipo in _TIPO_BUCKET:
        return _TIPO_BUCKET[tipo]
    if key == _APORTE_KEY or any(contains_word(key, h) for h in INVESTMENT_CATEGORY_HINTS):
        return "liberdade_financeira"
    if interno:
        return None
    return CATEGORY_BUCKET.get(key, DEFAULT_BUCKET)


def _month_rows(user_id: int, year: int, mon: int) -> list[dict[str, Any]]:
    """Linhas do mês que contam nos potes (launches + cartão), com o pote de cada.

    O SQL traz `is_internal_movement` e o filtro é `_pote` (None = fora). Tudo
    filtrado por user_id, inclusive credit_transactions/bills.
    """
    # OF: "Investments" virou investimento_aporte (#149), mas no pote segue como antes.
    k = lambda t: f"case when {t}source = 'open_finance' and {cat_key_sql(t + 'categoria')} = '{_APORTE_KEY}' then 'investments' else {cat_key_sql(t + 'categoria')} end"  # noqa: E731
    cat_l, cat_ct = k(""), k("ct.")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                select criado_em::date as dia, coalesce(nullif(alvo, ''), nota, '') as descricao,
                       categoria, {cat_l} as cat, tipo, valor, 'conta' as origem, is_internal_movement as interno
                from launches
                where user_id=%s
                  and date_part('year',  criado_em) = %s
                  and date_part('month', criado_em) = %s
                  and ({TIPO_DESPESA_SQL} or tipo = any(%s))
                union all
                select ct.purchased_at as dia, coalesce(ct.nota, '') as descricao,
                       ct.categoria, {cat_ct} as cat, 'credito' as tipo, ct.valor, 'cartao' as origem, false as interno
                from credit_transactions ct
                join credit_bills b on b.id = ct.bill_id and b.user_id = ct.user_id
                where ct.user_id=%s
                  and ct.is_refund = false
                  and date_part('year',  b.period_end) = %s
                  and date_part('month', b.period_end) = %s
                """,
                (user_id, year, mon, list(_TIPO_BUCKET),
                 user_id, year, mon),
            )
            rows = cur.fetchall()
    out = []
    for r in rows:
        por_tipo = r["tipo"] in _TIPO_BUCKET
        key = r["tipo"] if por_tipo else r["cat"]
        bucket = _pote(r["tipo"], key, r["interno"])
        if bucket is None:
            continue
        label = _TIPO_LABEL[r["tipo"]] if por_tipo else (
            _CAT_LABEL.get(key) or _DISPLAY_BY_KEY.get(key) or r["categoria"] or CAT_VAZIA_LABEL).capitalize()
        out.append({
            "bucket": bucket,
            "key": key, "categoria": label, "dia": r["dia"],
            "descricao": r["descricao"], "valor": round(float(r["valor"] or 0), 2),
            "origem": r["origem"],
        })
    return out


def _bucket_categories(bucket: str) -> list[str]:
    cats = [(_CAT_LABEL.get(k) or k).capitalize()
            for k, v in _CATEGORY_BUCKET_DISPLAY.items() if v == bucket]
    return cats + [_TIPO_LABEL[t] for t, v in _TIPO_BUCKET.items() if v == bucket]
