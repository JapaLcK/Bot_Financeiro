"""Foto diária por posição do Open Finance (`open_finance_investment_snapshots`).

Só GRAVA — nenhuma regra de cálculo lê isto ainda. Quem ler no futuro filtra
pelo dono da conexão: `join open_finance_connections c on c.id = s.connection_id
where c.user_id = %s` (a tabela não tem user_id, ver o DDL em db/schema.py).
"""
import math
import re
from datetime import datetime
from decimal import Decimal

from utils_date import _tz

# A MESMA gramática do `_SQL_PROFIT` (db/open_finance.py). `fullmatch`, e não
# `$`: em Python `$` aceita um `\n` no fim, no Postgres não.
# tests/test_of_snapshots_historico.py compara as duas nas mesmas entradas.
_NUMERO = re.compile(r"-?[0-9]+(\.[0-9]+)?")


def _numero(v) -> Decimal | None:
    """Número do banco, ou None. Nunca 0 no lugar do que não se leu."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return Decimal(v)  # sem passar por float: 10**400 estouraria o isfinite
    if isinstance(v, float):
        return Decimal(str(v)) if math.isfinite(v) else None
    if isinstance(v, str) and _NUMERO.fullmatch(v):
        return Decimal(v)
    return None


def _texto(v) -> str | None:
    return v if isinstance(v, str) else None


def _instante(v) -> datetime | None:
    """`date` da posição (ISO, com `Z` ou offset). Sem fuso (data pura) = fuso
    do app. Ilegível → None, nunca "hoje"."""
    if not isinstance(v, str):
        return None
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=_tz())


# (coluna, campo do `raw` da Pluggy, conversor)
_CAMPOS = (
    ("position_at", "date", _instante),
    ("status", "status", _texto),
    ("balance", "balance", _numero),
    ("amount", "amount", _numero),
    ("amount_original", "amountOriginal", _numero),
    ("quantity", "quantity", _numero),
    ("contract_rate", "rate", _numero),
    ("contract_rate_type", "rateType", _texto),
    ("last_month_rate", "lastMonthRate", _numero),
    ("last_twelve_months_rate", "lastTwelveMonthsRate", _numero),
    ("annual_rate", "annualRate", _numero),
)
_COLUNAS = ("connection_id", "provider_investment_id", "observed_on", "observed_at",
            "collection_confirmed", *(c for c, _, _ in _CAMPOS))

# Última coleta do dia vence, mas a não confirmada nunca sobrescreve a confirmada.
_SQL = (
    f"insert into open_finance_investment_snapshots ({', '.join(_COLUNAS)}) "
    f"values ({', '.join(['%s'] * len(_COLUNAS))}) "
    "on conflict (connection_id, provider_investment_id, observed_on) do update set "
    + ", ".join(f"{c} = excluded.{c}" for c in _COLUNAS[3:])
    + " where excluded.collection_confirmed"
      " or not open_finance_investment_snapshots.collection_confirmed"
)


def grava_fotos_posicoes(cur, connection_id: int, investments: list[dict],
                         now: datetime, confirmada: bool) -> None:
    """Uma linha por posição no dia de `now` (fuso do app, vindo do Python —
    nunca `current_date` no SQL). Lista vazia não grava nada: lacuna, não zero."""
    linhas = []
    for inv in investments:
        if not inv.get("provider_investment_id"):
            continue
        raw = inv.get("raw") if isinstance(inv.get("raw"), dict) else {}
        linhas.append((connection_id, inv["provider_investment_id"], now.date(), now,
                       confirmada, *(conv(raw.get(campo)) for _, campo, conv in _CAMPOS)))
    if linhas:
        cur.executemany(_SQL, linhas)
