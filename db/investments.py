"""
db/investments.py — Investimentos: criar, aportar, resgatar, juros e CDI.
"""
import calendar
import logging
import sys
import requests
from datetime import datetime, date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from psycopg.types.json import Jsonb
import psycopg

from utils_date import _tz

from .connection import get_conn
from .users import ensure_user

logger = logging.getLogger(__name__)
_warned_bcb_requests: set[tuple] = set()


# ──────────────────────────────────────────────────────────────────────────────
# Helpers de dias úteis e datas
# ──────────────────────────────────────────────────────────────────────────────

def _business_days_between(d1: date, d2: date) -> int:
    """Dias úteis entre d1 (exclusive) e d2 (inclusive), seg-sex."""
    if d2 <= d1:
        return 0
    days = 0
    cur = d1
    while cur < d2:
        cur = cur.fromordinal(cur.toordinal() + 1)
        if cur.weekday() < 5:
            days += 1
    return days


def _fmt_ddmmyyyy(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def _warn_bcb_once(key: tuple, message: str, *args) -> None:
    if key in _warned_bcb_requests:
        return
    _warned_bcb_requests.add(key)
    logger.warning(message, *args)


def _is_sgs_no_values_payload(payload) -> bool:
    if not isinstance(payload, dict):
        return False

    error = payload.get("erro")
    if not isinstance(error, dict):
        return False

    detail = str(error.get("detail") or "")
    return "Value(s) not found" in detail


def _decode_sgs_response(r: requests.Response, series_code: int, *, context: tuple) -> list[dict]:
    try:
        payload = r.json()
    except Exception as e:
        _warn_bcb_once(
            (*context, "invalid_json", type(e).__name__, str(e)),
            "Resposta inválida da série SGS %s no BCB: %s",
            series_code,
            e,
        )
        return []

    if isinstance(payload, list):
        return payload

    if _is_sgs_no_values_payload(payload):
        logger.info("Sem valores publicados para série SGS %s no período consultado.", series_code)
        return []

    _warn_bcb_once(
        (*context, "unexpected_payload", str(payload)[:200]),
        "Resposta inesperada da série SGS %s no BCB: %s",
        series_code,
        payload,
    )
    return []


# ──────────────────────────────────────────────────────────────────────────────
# CDI — BCB SGS
# ──────────────────────────────────────────────────────────────────────────────

def _fetch_sgs_series_json(series_code: int, start: date, end: date) -> list[dict]:
    url = f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{series_code}/dados"
    params = {
        "formato": "json",
        "dataInicial": _fmt_ddmmyyyy(start),
        "dataFinal": _fmt_ddmmyyyy(end),
    }
    try:
        r = requests.get(url, params=params, timeout=20)
        if r.status_code == 404:
            data = _decode_sgs_response(r, series_code, context=("fetch_sgs_series_json", series_code, start, end))
            if data or _is_sgs_no_values_payload(r.json()):
                return data
        r.raise_for_status()
        return _decode_sgs_response(r, series_code, context=("fetch_sgs_series_json", series_code, start, end))
    except Exception as e:
        _warn_bcb_once(
            ("fetch_sgs_series_json", series_code, start, end, type(e).__name__, str(e)),
            "Falha ao buscar série SGS %s no BCB entre %s e %s: %s",
            series_code,
            start.isoformat(),
            end.isoformat(),
            e,
        )
        return []


def _fetch_sgs_latest_json(series_code: int, limit: int = 15) -> list[dict]:
    url = f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{series_code}/dados/ultimos/{limit}"
    params = {"formato": "json"}
    try:
        r = requests.get(url, params=params, timeout=20)
        if r.status_code == 404:
            data = _decode_sgs_response(r, series_code, context=("fetch_sgs_latest_json", series_code, limit))
            if data or _is_sgs_no_values_payload(r.json()):
                return data
        r.raise_for_status()
        return _decode_sgs_response(r, series_code, context=("fetch_sgs_latest_json", series_code, limit))
    except Exception as e:
        _warn_bcb_once(
            ("fetch_sgs_latest_json", series_code, limit, type(e).__name__, str(e)),
            "Falha ao buscar últimos valores da série SGS %s no BCB: %s",
            series_code,
            e,
        )
        return []


def _get_cdi_daily_map(cur, start: date, end: date) -> dict[date, float]:
    """
    Retorna {date: cdi_percent_per_day}.
    Usa cache em market_rates e busca do BCB o que estiver faltando.
    """
    if end <= start:
        return {}

    cur.execute(
        "select ref_date, value from market_rates "
        "where code='CDI' and ref_date >= %s and ref_date <= %s order by ref_date",
        (start, end),
    )
    cached = {row["ref_date"]: float(row["value"]) for row in cur.fetchall()}

    data = _fetch_sgs_series_json(12, start, end)
    if not isinstance(data, list) or not data:
        return cached

    to_upsert = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            raw_date = item.get("data")
            raw_val = item.get("valor")
            if not raw_date or raw_val is None:
                continue
            d = datetime.strptime(raw_date, "%d/%m/%Y").date()
            v = float(str(raw_val).replace(",", "."))
            if d not in cached:
                to_upsert.append((d, v))
            cached[d] = v
        except Exception as e:
            _warn_bcb_once(
                ("invalid_bcb_item", str(item), type(e).__name__, str(e)),
                "Item inválido do BCB ignorado: %s | erro=%s",
                item,
                e,
            )

    if to_upsert:
        cur.executemany(
            "insert into market_rates(code, ref_date, value) values ('CDI', %s, %s) "
            "on conflict (code, ref_date) do update set value=excluded.value",
            to_upsert,
        )

    return cached


def _get_sgs_daily_map(cur, code: str, series_code: int, start: date, end: date) -> dict[date, float]:
    """Retorna {date: percent_per_day} para séries SGS diárias, com cache em market_rates."""
    if end <= start:
        return {}

    cur.execute(
        "select ref_date, value from market_rates "
        "where code=%s and ref_date >= %s and ref_date <= %s order by ref_date",
        (code, start, end),
    )
    cached = {row["ref_date"]: float(row["value"]) for row in cur.fetchall()}

    data = _fetch_sgs_series_json(series_code, start, end)
    if not isinstance(data, list) or not data:
        return cached

    to_upsert = []
    for item in data:
        try:
            d = datetime.strptime(item["data"], "%d/%m/%Y").date()
            v = float(str(item["valor"]).replace(",", "."))
            if d not in cached:
                to_upsert.append((code, d, v))
            cached[d] = v
        except Exception as e:
            _warn_bcb_once(
                ("invalid_sgs_daily_item", code, str(item), type(e).__name__, str(e)),
                "Item inválido do SGS %s ignorado: %s | erro=%s",
                code,
                item,
                e,
            )

    if to_upsert:
        cur.executemany(
            "insert into market_rates(code, ref_date, value) values (%s, %s, %s) "
            "on conflict (code, ref_date) do update set value=excluded.value",
            to_upsert,
        )

    return cached


def _get_sgs_monthly_map(cur, code: str, series_code: int, start: date, end: date) -> dict[date, float]:
    """Retorna {ref_date: percent_per_month} para séries SGS mensais, com cache local."""
    return _get_sgs_daily_map(cur, code, series_code, start, end)


def _get_selic_daily_map(cur, start: date, end: date) -> dict[date, float]:
    """Taxa SELIC diária (% a.d.) no SGS/BCB (série 11)."""
    return _get_sgs_daily_map(cur, "SELIC_DAILY", 11, start, end)


def _get_ipca_monthly_map(cur, start: date, end: date) -> dict[date, float]:
    """IPCA mensal (% a.m.) no SGS/BCB (série 433)."""
    return _get_sgs_monthly_map(cur, "IPCA_MONTHLY", 433, start, end)


def get_latest_cdi(cur) -> tuple[date, float] | None:
    """Retorna (data, valor_percent_ao_dia) da CDI mais recente."""
    data = _fetch_sgs_latest_json(12)
    latest: tuple[date, float] | None = None
    if data:
        for item in data:
            try:
                d = datetime.strptime(item["data"], "%d/%m/%Y").date()
                v = float(str(item["valor"]).replace(",", "."))
                if latest is None or d > latest[0]:
                    latest = (d, v)
            except Exception:
                continue

    if latest:
        cur.execute(
            "insert into market_rates(code, ref_date, value) values ('CDI', %s, %s) "
            "on conflict (code, ref_date) do update set value = excluded.value",
            latest,
        )
        return latest

    cur.execute(
        "select ref_date, value from market_rates where code = 'CDI' order by ref_date desc limit 1"
    )
    row = cur.fetchone()
    return (row["ref_date"], float(row["value"])) if row else None


def get_latest_cdi_aa(cur) -> tuple[date, float] | None:
    """CDI a.a. (base 252) direto do SGS/BCB (série 4389)."""
    data = _fetch_sgs_latest_json(4389)
    latest: tuple[date, float] | None = None
    if data:
        for item in data:
            try:
                d = datetime.strptime(item["data"], "%d/%m/%Y").date()
                v = float(str(item["valor"]).replace(",", "."))
                if latest is None or d > latest[0]:
                    latest = (d, v)
            except Exception:
                continue

    if latest:
        cur.execute(
            "insert into market_rates(code, ref_date, value) values ('CDI_AA', %s, %s) "
            "on conflict (code, ref_date) do update set value = excluded.value",
            latest,
        )
        return latest

    cur.execute(
        "select ref_date, value from market_rates where code = 'CDI_AA' order by ref_date desc limit 1"
    )
    row = cur.fetchone()
    return (row["ref_date"], float(row["value"])) if row else None


def get_latest_market_rate(cur, code: str, series_code: int) -> tuple[date, float] | None:
    """Retorna a taxa mais recente de uma série SGS, usando cache local como fallback."""
    data = _fetch_sgs_latest_json(series_code)
    latest: tuple[date, float] | None = None
    if data:
        for item in data:
            try:
                d = datetime.strptime(item["data"], "%d/%m/%Y").date()
                v = float(str(item["valor"]).replace(",", "."))
                if latest is None or d > latest[0]:
                    latest = (d, v)
            except Exception:
                continue

    if latest:
        cur.execute(
            "insert into market_rates(code, ref_date, value) values (%s, %s, %s) "
            "on conflict (code, ref_date) do update set value = excluded.value",
            (code, latest[0], latest[1]),
        )
        return latest

    cur.execute(
        "select ref_date, value from market_rates where code = %s order by ref_date desc limit 1",
        (code,),
    )
    row = cur.fetchone()
    return (row["ref_date"], float(row["value"])) if row else None


def get_latest_selic_aa(cur) -> tuple[date, float] | None:
    """Meta SELIC a.a. no SGS/BCB (série 432)."""
    return get_latest_market_rate(cur, "SELIC_AA", 432)


def get_latest_ipca_12m(cur) -> tuple[date, float] | None:
    """IPCA acumulado em 12 meses no SGS/BCB (série 13522)."""
    return get_latest_market_rate(cur, "IPCA_12M", 13522)


def get_dashboard_market_rates() -> dict:
    """Taxas oficiais úteis para o dashboard, com datas de referência."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            rates = {
                "cdi_aa": get_latest_cdi_aa(cur),
                "selic_aa": get_latest_selic_aa(cur),
                "ipca_12m": get_latest_ipca_12m(cur),
            }
        conn.commit()

    return {
        key: (
            {"date": ref_date.isoformat(), "value": value}
            if ref_date is not None else None
        )
        for key, maybe_rate in rates.items()
        for ref_date, value in [maybe_rate or (None, None)]
    }


def get_latest_cdi_daily_pct() -> float:
    """Retorna CDI diária em % ao dia (ex: 0.0550)."""
    data = _fetch_sgs_latest_json(12)
    if not data:
        raise RuntimeError("CDI_DAILY_NOT_AVAILABLE")

    latest = None
    for item in data:
        latest = float(str(item["valor"]).replace(",", "."))

    if latest is None:
        raise RuntimeError("CDI_DAILY_NOT_AVAILABLE")

    return float(latest)


MONEY = Decimal("0.01")
ZERO = Decimal("0")
LOT_EPSILON = Decimal("0.000001")

IOF_REGRESSIVE_RATES = {
    1: Decimal("0.96"),
    2: Decimal("0.93"),
    3: Decimal("0.90"),
    4: Decimal("0.86"),
    5: Decimal("0.83"),
    6: Decimal("0.80"),
    7: Decimal("0.76"),
    8: Decimal("0.73"),
    9: Decimal("0.70"),
    10: Decimal("0.66"),
    11: Decimal("0.63"),
    12: Decimal("0.60"),
    13: Decimal("0.56"),
    14: Decimal("0.53"),
    15: Decimal("0.50"),
    16: Decimal("0.46"),
    17: Decimal("0.43"),
    18: Decimal("0.40"),
    19: Decimal("0.36"),
    20: Decimal("0.33"),
    21: Decimal("0.30"),
    22: Decimal("0.26"),
    23: Decimal("0.23"),
    24: Decimal("0.20"),
    25: Decimal("0.16"),
    26: Decimal("0.13"),
    27: Decimal("0.10"),
    28: Decimal("0.06"),
    29: Decimal("0.03"),
}


def _money(value: Decimal | float | int | str) -> Decimal:
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def _ir_rate_for_days(days: int, tax_profile: str | None) -> Decimal:
    if tax_profile == "etf_rf_15":
        return Decimal("0.15")
    if tax_profile == "exempt_ir_iof":
        return ZERO
    if days <= 180:
        return Decimal("0.225")
    if days <= 360:
        return Decimal("0.20")
    if days <= 720:
        return Decimal("0.175")
    return Decimal("0.15")


def _iof_rate_for_days(days: int, tax_profile: str | None) -> Decimal:
    if tax_profile != "regressive_ir_iof":
        return ZERO
    if days <= 0:
        return Decimal("0.96")
    return IOF_REGRESSIVE_RATES.get(days, ZERO)


def _taxes_for_gain(gain: Decimal, days: int, tax_profile: str | None) -> tuple[Decimal, Decimal]:
    gain = max(Decimal(str(gain)), ZERO)
    if gain <= 0 or tax_profile == "exempt_ir_iof":
        return ZERO, ZERO
    iof = _money(gain * _iof_rate_for_days(days, tax_profile))
    ir_base = max(gain - iof, ZERO)
    ir = _money(ir_base * _ir_rate_for_days(days, tax_profile))
    return iof, ir


def _growth_for_period(
    cur,
    balance: Decimal,
    period: str,
    rate_value: Decimal,
    last_date: date | None,
    today: date,
) -> tuple[Decimal, date | None]:
    if last_date is None:
        return balance, last_date

    n = _business_days_between(last_date, today)
    if n <= 0:
        return balance, last_date

    rate = float(rate_value)

    if period == "cdi":
        mult = rate
        start = last_date + timedelta(days=1)
        db_pkg = sys.modules.get("db")
        fetch_cdi_daily_map = getattr(db_pkg, "_get_cdi_daily_map", _get_cdi_daily_map)
        cdi_map = fetch_cdi_daily_map(cur, start, today)
        cdi_days = sorted(d for d in cdi_map.keys() if last_date < d <= today)
        if not cdi_days:
            return balance, last_date

        factor = 1.0
        for d in cdi_days:
            factor *= (1.0 + (cdi_map[d] / 100.0) * mult)
        return Decimal(str(float(balance) * factor)), cdi_days[-1]

    if period == "cdi_spread":
        start = last_date + timedelta(days=1)
        db_pkg = sys.modules.get("db")
        fetch_cdi_daily_map = getattr(db_pkg, "_get_cdi_daily_map", _get_cdi_daily_map)
        cdi_map = fetch_cdi_daily_map(cur, start, today)
        cdi_days = sorted(d for d in cdi_map.keys() if last_date < d <= today)
        if not cdi_days:
            return balance, last_date

        spread_daily = (1.0 + rate) ** (1.0 / 252.0) - 1.0
        factor = 1.0
        for d in cdi_days:
            factor *= (1.0 + (cdi_map[d] / 100.0)) * (1.0 + spread_daily)
        return Decimal(str(float(balance) * factor)), cdi_days[-1]

    if period == "selic_spread":
        start = last_date + timedelta(days=1)
        selic_map = _get_selic_daily_map(cur, start, today)
        selic_days = sorted(d for d in selic_map.keys() if last_date < d <= today)
        if not selic_days:
            return balance, last_date

        spread_daily = (1.0 + rate) ** (1.0 / 252.0) - 1.0
        factor = 1.0
        for d in selic_days:
            factor *= (1.0 + (selic_map[d] / 100.0)) * (1.0 + spread_daily)
        return Decimal(str(float(balance) * factor)), selic_days[-1]

    if period == "ipca_spread":
        start = (last_date.replace(day=1) + timedelta(days=32)).replace(day=1)
        ipca_map = _get_ipca_monthly_map(cur, start, today)
        ipca_months = sorted(d for d in ipca_map.keys() if last_date < d <= today)
        if not ipca_months:
            return balance, last_date

        spread_monthly = (1.0 + rate) ** (1.0 / 12.0) - 1.0
        factor = 1.0
        for d in ipca_months:
            factor *= (1.0 + (ipca_map[d] / 100.0)) * (1.0 + spread_monthly)
        return Decimal(str(float(balance) * factor)), ipca_months[-1]

    if period == "daily":
        daily_rate = rate
    elif period == "monthly":
        daily_rate = (1.0 + rate) ** (1.0 / 21.0) - 1.0
    elif period == "yearly":
        daily_rate = (1.0 + rate) ** (1.0 / 252.0) - 1.0
    else:
        daily_rate = 0.0

    if daily_rate > 0:
        return Decimal(str(float(balance) * (1.0 + daily_rate) ** n)), today
    return balance, today


def _insert_investment_lot(
    cur,
    user_id: int,
    inv_id: int,
    amount: Decimal,
    opened_at: date,
    last_date: date | None = None,
) -> int:
    applied_date = last_date or opened_at
    cur.execute(
        """
        insert into investment_lots(
            user_id, investment_id, principal_initial, principal_remaining,
            balance, opened_at, last_date, status
        )
        values (%s,%s,%s,%s,%s,%s,%s,'open')
        returning id
        """,
        (user_id, inv_id, amount, amount, amount, opened_at, applied_date),
    )
    return cur.fetchone()["id"]


def _ensure_investment_lots(cur, user_id: int, inv: dict) -> None:
    cur.execute(
        "select count(*) as total from investment_lots where user_id=%s and investment_id=%s",
        (user_id, inv["id"]),
    )
    if int(cur.fetchone()["total"] or 0) > 0:
        return

    balance = Decimal(str(inv["balance"] or 0))
    if balance <= 0:
        return

    opened_at = inv.get("purchase_date") or inv.get("last_date") or datetime.now(_tz()).date()
    last_date = inv.get("last_date") or opened_at
    _insert_investment_lot(cur, user_id, inv["id"], balance, opened_at, last_date)


def _sync_investment_from_lots(cur, user_id: int, inv_id: int) -> Decimal:
    cur.execute(
        """
        select coalesce(sum(balance), 0) as balance, max(last_date) as last_date
        from investment_lots
        where user_id=%s and investment_id=%s and status='open'
        """,
        (user_id, inv_id),
    )
    totals = cur.fetchone()
    new_balance = Decimal(str(totals["balance"] or 0))
    new_last_date = totals["last_date"]
    if new_last_date is None:
        cur.execute("select last_date from investments where id=%s and user_id=%s", (inv_id, user_id))
        row = cur.fetchone()
        new_last_date = row["last_date"] if row else datetime.now(_tz()).date()
    cur.execute(
        "update investments set balance=%s, last_date=%s where id=%s and user_id=%s",
        (new_balance, new_last_date, inv_id, user_id),
    )
    return new_balance


def _fetch_lots_for_investments(cur, user_id: int, inv_ids: list[int]) -> dict[int, list[dict]]:
    if not inv_ids:
        return {}
    cur.execute(
        """
        select id, investment_id, principal_initial, principal_remaining, balance,
               opened_at, last_date, status, closed_at
        from investment_lots
        where user_id=%s and investment_id = any(%s)
        order by investment_id, opened_at, id
        """,
        (user_id, inv_ids),
    )
    lots_by_inv: dict[int, list[dict]] = {int(inv_id): [] for inv_id in inv_ids}
    for row in cur.fetchall():
        lot = dict(row)
        lot["age_days"] = max(0, (datetime.now(_tz()).date() - lot["opened_at"]).days)
        lots_by_inv.setdefault(int(row["investment_id"]), []).append(lot)
    return lots_by_inv


# ──────────────────────────────────────────────────────────────────────────────
# Accrual (aplicação de juros)
# ──────────────────────────────────────────────────────────────────────────────

def accrue_investment_db(cur, user_id: int, inv_id: int, today: date | None = None):
    """
    Atualiza (balance, last_date) aplicando juros por dias úteis.
    daily → rate por dia útil
    monthly → rate distribuído em 21 dias úteis
    yearly → rate distribuído em 252 dias úteis
    cdi → aplica CDI diária do período multiplicada pelo mult (ex: 1.10 = 110% CDI)
    cdi_spread/selic_spread → aplica taxa diária oficial + spread anual convertido para dia útil
    ipca_spread → aplica IPCA mensal publicado + spread anual convertido para mês
    """
    if today is None:
        today = datetime.now(_tz()).date()

    cur.execute(
        """
        select id, balance, rate, period, last_date, purchase_date
        from investments
        where id=%s and user_id=%s for update
        """,
        (inv_id, user_id),
    )
    inv = cur.fetchone()
    if not inv:
        raise LookupError("INV_NOT_FOUND")

    _ensure_investment_lots(cur, user_id, inv)

    cur.execute(
        """
        select id, balance, last_date
        from investment_lots
        where user_id=%s and investment_id=%s and status='open'
        order by opened_at, id
        for update
        """,
        (user_id, inv_id),
    )
    lots = cur.fetchall()
    if not lots:
        cur.execute(
            "update investments set balance=0 where id=%s and user_id=%s",
            (inv_id, user_id),
        )
        return ZERO

    for lot in lots:
        new_balance, applied_until = _growth_for_period(
            cur,
            Decimal(str(lot["balance"] or 0)),
            inv["period"],
            Decimal(str(inv["rate"] or 0)),
            lot["last_date"],
            today,
        )
        if new_balance != lot["balance"] or applied_until != lot["last_date"]:
            cur.execute(
                "update investment_lots set balance=%s, last_date=%s where id=%s and user_id=%s",
                (new_balance, applied_until or lot["last_date"], lot["id"], user_id),
            )

    return _sync_investment_from_lots(cur, user_id, inv_id)


# ──────────────────────────────────────────────────────────────────────────────
# CRUD de investimentos
# ──────────────────────────────────────────────────────────────────────────────

VALID_INVESTMENT_PERIODS = {
    "daily", "monthly", "yearly", "cdi", "cdi_spread", "ipca_spread", "selic_spread"
}

VALID_INVESTMENT_INDEXERS = {
    "daily", "monthly", "fixed", "pct_cdi", "cdi_spread", "ipca_spread", "selic_spread"
}

TAX_PROFILE_BY_ASSET_TYPE = {
    "LCI": "exempt_ir_iof",
    "LCA": "exempt_ir_iof",
    "CRI": "exempt_ir_iof",
    "CRA": "exempt_ir_iof",
    "ETF Renda Fixa": "etf_rf_15",
}


def _default_indexer_for_period(period: str) -> str:
    if period == "cdi":
        return "pct_cdi"
    if period == "yearly":
        return "fixed"
    return period


def _tax_profile_for_asset(asset_type: str | None) -> str:
    return TAX_PROFILE_BY_ASSET_TYPE.get(asset_type or "CDB", "regressive_ir_iof")

def create_investment(user_id: int, name: str, rate: float, period: str, nota: str | None = None):
    """
    Cria investimento. Retorna (launch_id, inv_name_canon).
    Se já existir, retorna (None, inv_name_canon).
    """
    ensure_user(user_id)
    name = (name or "").strip()
    if not name:
        raise ValueError("EMPTY_NAME")
    if period not in ("daily", "monthly", "yearly"):
        raise ValueError("BAD_PERIOD")

    r = Decimal(str(rate))
    if r <= 0:
        raise ValueError("BAD_RATE")

    criado_em = datetime.now(_tz())
    last_date = datetime.now(_tz()).date()

    with get_conn() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    "insert into investments(user_id, name, balance, rate, period, last_date) "
                    "values (%s,%s,0,%s,%s,%s) returning name",
                    (user_id, name, r, period, last_date),
                )
                inv_name = cur.fetchone()["name"]
                created = True
            except psycopg.errors.UniqueViolation:
                conn.rollback()
                created = False
                with get_conn() as conn2:
                    with conn2.cursor() as cur2:
                        cur2.execute(
                            "select name from investments where user_id=%s and lower(name)=lower(%s)",
                            (user_id, name),
                        )
                        row = cur2.fetchone()
                        if not row:
                            raise
                        inv_name = row["name"]

            if not created:
                return None, inv_name

            efeitos = {
                "delta_conta": 0.0, "delta_pocket": None,
                "delta_invest": {"nome": inv_name, "delta": 0.0},
                "create_pocket": None,
                "create_investment": {"nome": inv_name, "rate": float(r), "period": period},
            }
            cur.execute(
                "insert into launches(user_id, tipo, valor, alvo, nota, criado_em, efeitos) "
                "values (%s,%s,%s,%s,%s,%s,%s) returning id",
                (user_id, "create_investment", Decimal("0"), inv_name, nota, criado_em, Jsonb(efeitos)),
            )
            launch_id = cur.fetchone()["id"]

        conn.commit()

    return launch_id, inv_name


def create_investment_db(
    user_id: int,
    name: str,
    rate: float,
    period: str,
    nota: str | None = None,
    *,
    asset_type: str | None = None,
    indexer: str | None = None,
    issuer: str | None = None,
    purchase_date: date | str | None = None,
    maturity_date: date | str | None = None,
    interest_payment_frequency: str | None = None,
    tax_profile: str | None = None,
    initial_amount: float | Decimal | None = None,
    initial_note: str | None = None,
):
    """
    Cria investimento (suporta period='cdi'). Retorna (launch_id, inv_id, canon_name).
    Se já existir, retorna (None, inv_id, canon_name).
    """
    ensure_user(user_id)
    name = (name or "").strip()
    if not name:
        raise ValueError("EMPTY_NAME")
    if period not in VALID_INVESTMENT_PERIODS:
        raise ValueError("INVALID_PERIOD")

    r = Decimal(str(rate))
    if r <= 0 and period != "selic_spread":
        raise ValueError("INVALID_RATE")

    criado_em = datetime.now(_tz())
    today = date.today()
    asset_type = (asset_type or "CDB").strip() or "CDB"
    indexer = (indexer or _default_indexer_for_period(period)).strip()
    if indexer not in VALID_INVESTMENT_INDEXERS:
        raise ValueError("INVALID_INDEXER")
    issuer = (issuer or "").strip() or None
    tax_profile = (tax_profile or _tax_profile_for_asset(asset_type)).strip()
    interest_payment_frequency = (interest_payment_frequency or "maturity").strip()
    if isinstance(purchase_date, str) and purchase_date:
        purchase_date = date.fromisoformat(purchase_date)
    if isinstance(maturity_date, str) and maturity_date:
        maturity_date = date.fromisoformat(maturity_date)
    initial = Decimal(str(initial_amount or 0))
    if initial < 0:
        raise ValueError("INITIAL_AMOUNT_INVALID")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into investments(
                    user_id, name, balance, rate, period, last_date,
                    asset_type, indexer, issuer, purchase_date, maturity_date,
                    interest_payment_frequency, tax_profile
                )
                values (%s,%s,0,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                on conflict (user_id, name) do nothing
                returning id, name
                """,
                (
                    user_id, name, r, period, today,
                    asset_type, indexer, issuer, purchase_date, maturity_date,
                    interest_payment_frequency, tax_profile,
                ),
            )
            row = cur.fetchone()

            if row:
                inv_id, canon = row["id"], row["name"]
                created = True
            else:
                created = False
                cur.execute(
                    "select id, name from investments where user_id=%s and lower(name)=lower(%s)",
                    (user_id, name),
                )
                r2 = cur.fetchone()
                if not r2:
                    raise RuntimeError("INVESTMENT_LOOKUP_FAILED")
                inv_id, canon = r2["id"], r2["name"]

            if not created:
                conn.commit()
                return None, inv_id, canon

            efeitos = {
                "delta_conta": 0.0, "delta_pocket": None, "delta_invest": None,
                "create_pocket": None, "create_investment": {"nome": canon},
                "delete_pocket": None, "delete_investment": None,
                "investment_meta": {
                    "asset_type": asset_type,
                    "indexer": indexer,
                    "tax_profile": tax_profile,
                },
            }
            cur.execute(
                "insert into launches(user_id, tipo, valor, alvo, nota, criado_em, efeitos) "
                "values (%s,%s,%s,%s,%s,%s,%s) returning id",
                (user_id, "create_investment", Decimal("0"), canon, nota, criado_em, Jsonb(efeitos)),
            )
            launch_id = cur.fetchone()["id"]

            if initial > 0:
                cur.execute("select balance from accounts where user_id=%s for update", (user_id,))
                acc = cur.fetchone()
                if not acc:
                    raise RuntimeError("ACCOUNT_MISSING")
                if acc["balance"] < initial:
                    raise ValueError("INSUFFICIENT_ACCOUNT")

                cur.execute(
                    "update accounts set balance = balance - %s where user_id=%s",
                    (initial, user_id),
                )
                lot_opened_at = purchase_date or today
                lot_id = _insert_investment_lot(cur, user_id, inv_id, initial, lot_opened_at, lot_opened_at)
                _sync_investment_from_lots(cur, user_id, inv_id)

                deposit_effects = {
                    "delta_conta": -float(initial), "delta_pocket": None,
                    "delta_invest": {"nome": canon, "delta": float(initial)},
                    "create_pocket": None, "create_investment": None,
                    "investment_lot_create": {"lot_id": lot_id, "investment_id": inv_id},
                }
                cur.execute(
                    "insert into launches(user_id, tipo, valor, alvo, nota, criado_em, efeitos, is_internal_movement) "
                    "values (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        user_id,
                        "aporte_investimento",
                        initial,
                        canon,
                        initial_note or f"Aporte inicial em {canon}",
                        criado_em,
                        Jsonb(deposit_effects),
                        True,
                    ),
                )

        conn.commit()

    return launch_id, inv_id, canon


def delete_investment(user_id: int, investment_name: str, nota: str | None = None):
    """Exclui investimento (saldo=0). Retorna (launch_id, canon_name)."""
    ensure_user(user_id)
    investment_name = (investment_name or "").strip()
    if not investment_name:
        raise ValueError("EMPTY_NAME")

    criado_em = datetime.now(_tz())

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, name, balance, rate, period, last_date,
                       asset_type, indexer, issuer, purchase_date, maturity_date,
                       interest_payment_frequency, tax_profile
                from investments
                """
                "where user_id=%s and lower(name)=lower(%s) for update",
                (user_id, investment_name),
            )
            inv = cur.fetchone()
            if not inv:
                raise LookupError("INV_NOT_FOUND")

            inv_id, canon = inv["id"], inv["name"]
            if Decimal(str(inv["balance"])) != Decimal("0"):
                raise ValueError("INV_NOT_ZERO")

            cur.execute("delete from investments where id=%s", (inv_id,))

            efeitos = {
                "delta_conta": 0.0, "delta_pocket": None, "delta_invest": None,
                "create_pocket": None, "create_investment": None, "delete_pocket": None,
                "delete_investment": {
                    "nome": canon, "balance": 0.0,
                    "rate": float(inv["rate"]), "period": inv["period"],
                    "asset_type": inv.get("asset_type"),
                    "indexer": inv.get("indexer"),
                    "issuer": inv.get("issuer"),
                    "purchase_date": inv["purchase_date"].isoformat() if inv.get("purchase_date") else None,
                    "maturity_date": inv["maturity_date"].isoformat() if inv.get("maturity_date") else None,
                    "interest_payment_frequency": inv.get("interest_payment_frequency"),
                    "tax_profile": inv.get("tax_profile"),
                    "last_date": inv["last_date"].isoformat() if inv["last_date"]
                                 else datetime.now(_tz()).date().isoformat(),
                },
            }
            cur.execute(
                "insert into launches(user_id, tipo, valor, alvo, nota, criado_em, efeitos) "
                "values (%s,%s,%s,%s,%s,%s,%s) returning id",
                (user_id, "delete_investment", Decimal("0"), canon, nota, criado_em, Jsonb(efeitos)),
            )
            launch_id = cur.fetchone()["id"]

        conn.commit()

    return launch_id, canon


def list_investments(user_id: int):
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, name, balance, rate, period, last_date,
                       asset_type, indexer, issuer, purchase_date, maturity_date,
                       interest_payment_frequency, tax_profile
                """
                "from investments where user_id=%s order by lower(name)",
                (user_id,),
            )
            rows = [dict(r) for r in cur.fetchall()]
            lots_by_inv = _fetch_lots_for_investments(cur, user_id, [int(r["id"]) for r in rows])
            for row in rows:
                row["lots"] = lots_by_inv.get(int(row["id"]), [])
            return rows


def list_users_with_investments() -> list[int]:
    """Retorna usuários que possuem ao menos um investimento cadastrado."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select distinct user_id from investments order by user_id")
            return [int(row["user_id"]) for row in cur.fetchall()]


def accrue_all_investments(user_id: int):
    """Aplica juros em todos os investimentos do usuário e retorna a lista atualizada."""
    ensure_user(user_id)
    today = datetime.now(_tz()).date()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select id from investments where user_id=%s for update", (user_id,))
            rows = cur.fetchall()

            for r in rows:
                accrue_investment_db(cur, user_id, r["id"], today=today)

            cur.execute(
                """
                select id, name, balance, rate, period, last_date,
                       asset_type, indexer, issuer, purchase_date, maturity_date,
                       interest_payment_frequency, tax_profile
                """
                "from investments where user_id=%s order by lower(name)",
                (user_id,),
            )
            out = [dict(r) for r in cur.fetchall()]
            lots_by_inv = _fetch_lots_for_investments(cur, user_id, [int(r["id"]) for r in out])
            for row in out:
                row["lots"] = lots_by_inv.get(int(row["id"]), [])

        conn.commit()

    return out


def _canon_investment_name(cur, user_id: int, name: str) -> str | None:
    cur.execute(
        "select name from investments where user_id = %s and lower(name) = lower(%s)",
        (user_id, name),
    )
    row = cur.fetchone()
    return row["name"] if row else None


def investment_deposit_from_account(
    user_id: int, investment_name: str, amount: float, nota: str | None = None
):
    """Conta → Investimento (com accrual antes). Retorna (launch_id, new_acc, new_inv, canon)."""
    ensure_user(user_id)
    v = Decimal(str(amount))
    if v <= 0:
        raise ValueError("AMOUNT_INVALID")

    criado_em = datetime.now(_tz())
    today = datetime.now(_tz()).date()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select balance from accounts where user_id=%s for update", (user_id,))
            acc = cur.fetchone()
            if not acc:
                raise RuntimeError("ACCOUNT_MISSING")
            if acc["balance"] < v:
                raise ValueError("INSUFFICIENT_ACCOUNT")

            cur.execute(
                "select id, name from investments "
                "where user_id=%s and lower(name)=lower(%s) for update",
                (user_id, investment_name),
            )
            inv = cur.fetchone()
            if not inv:
                raise LookupError("INV_NOT_FOUND")

            inv_id, canon = inv["id"], inv["name"]
            accrue_investment_db(cur, user_id, inv_id, today=today)

            cur.execute(
                "update accounts set balance = balance - %s where user_id=%s returning balance",
                (v, user_id),
            )
            new_acc = cur.fetchone()["balance"]

            lot_id = _insert_investment_lot(cur, user_id, inv_id, v, today, today)
            new_inv = _sync_investment_from_lots(cur, user_id, inv_id)

            efeitos = {
                "delta_conta": -float(v), "delta_pocket": None,
                "delta_invest": {"nome": canon, "delta": +float(v)},
                "create_pocket": None, "create_investment": None,
                "investment_lot_create": {"lot_id": lot_id, "investment_id": inv_id},
            }
            cur.execute(
                "insert into launches(user_id, tipo, valor, alvo, nota, criado_em, efeitos, is_internal_movement) "
                "values (%s,%s,%s,%s,%s,%s,%s,%s) returning id",
                (user_id, "aporte_investimento", v, canon, nota, criado_em, Jsonb(efeitos), True),
            )
            launch_id = cur.fetchone()["id"]

        conn.commit()

    return launch_id, new_acc, new_inv, canon


def investment_withdraw_to_account(
    user_id: int, investment_name: str, amount: float, nota: str | None = None
):
    """Investimento → Conta via PEPS/FIFO. Retorna (launch_id, new_acc, new_inv, canon, tax_summary)."""
    ensure_user(user_id)
    v = Decimal(str(amount))
    if v <= 0:
        raise ValueError("AMOUNT_INVALID")

    criado_em = datetime.now(_tz())
    today = datetime.now(_tz()).date()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id, name, tax_profile from investments "
                "where user_id=%s and lower(name)=lower(%s) for update",
                (user_id, investment_name),
            )
            inv = cur.fetchone()
            if not inv:
                raise LookupError("INV_NOT_FOUND")

            inv_id, canon = inv["id"], inv["name"]
            new_bal_before = accrue_investment_db(cur, user_id, inv_id, today=today)

            if new_bal_before < v:
                raise ValueError("INSUFFICIENT_INVEST")

            cur.execute(
                """
                select id, principal_remaining, balance, opened_at, last_date, status
                from investment_lots
                where user_id=%s and investment_id=%s and status='open' and balance > 0
                order by opened_at, id
                for update
                """,
                (user_id, inv_id),
            )
            lots = cur.fetchall()
            remaining = v
            total_gross = ZERO
            total_net = ZERO
            total_iof = ZERO
            total_ir = ZERO
            lot_effects = []
            breakdown = []
            tax_profile = inv.get("tax_profile") or "regressive_ir_iof"

            for lot in lots:
                if remaining <= 0:
                    break

                lot_balance = Decimal(str(lot["balance"] or 0))
                if lot_balance <= 0:
                    continue
                lot_principal = Decimal(str(lot["principal_remaining"] or 0))
                take = min(lot_balance, remaining)

                if lot_balance <= lot_principal or lot_balance <= 0:
                    principal_part = min(take, lot_principal)
                    gain_part = ZERO
                else:
                    ratio = take / lot_balance
                    principal_part = min(lot_principal, lot_principal * ratio)
                    gain_part = max(take - principal_part, ZERO)

                age_days = max(0, (today - lot["opened_at"]).days)
                iof, ir = _taxes_for_gain(gain_part, age_days, tax_profile)
                net = take - iof - ir

                new_lot_balance = lot_balance - take
                new_lot_principal = max(lot_principal - principal_part, ZERO)
                closes = new_lot_balance <= LOT_EPSILON
                after_status = "closed" if closes else "open"
                after_balance = ZERO if closes else new_lot_balance
                after_principal = ZERO if closes else new_lot_principal

                lot_effects.append({
                    "lot_id": int(lot["id"]),
                    "before": {
                        "balance": float(lot_balance),
                        "principal_remaining": float(lot_principal),
                        "status": lot["status"],
                        "closed_at": None,
                    },
                    "after": {
                        "balance": float(after_balance),
                        "principal_remaining": float(after_principal),
                        "status": after_status,
                        "closed_at": today.isoformat() if closes else None,
                    },
                })
                breakdown.append({
                    "lot_id": int(lot["id"]),
                    "opened_at": lot["opened_at"].isoformat(),
                    "age_days": age_days,
                    "gross": float(take),
                    "principal": float(principal_part),
                    "gain": float(gain_part),
                    "iof": float(iof),
                    "ir": float(ir),
                    "net": float(net),
                    "ir_rate": float(_ir_rate_for_days(age_days, tax_profile)),
                    "iof_rate": float(_iof_rate_for_days(age_days, tax_profile)),
                })

                cur.execute(
                    """
                    update investment_lots
                    set balance=%s, principal_remaining=%s, status=%s, closed_at=%s, last_date=%s
                    where id=%s and user_id=%s
                    """,
                    (
                        after_balance,
                        after_principal,
                        after_status,
                        today if closes else None,
                        today,
                        lot["id"],
                        user_id,
                    ),
                )

                remaining -= take
                total_gross += take
                total_net += net
                total_iof += iof
                total_ir += ir

            if remaining > LOT_EPSILON:
                raise ValueError("INSUFFICIENT_INVEST")

            new_inv = _sync_investment_from_lots(cur, user_id, inv_id)

            cur.execute(
                "update accounts set balance = balance + %s where user_id=%s returning balance",
                (total_net, user_id),
            )
            new_acc = cur.fetchone()["balance"]

            tax_summary = {
                "gross": float(total_gross),
                "net": float(total_net),
                "iof": float(total_iof),
                "ir": float(total_ir),
                "tax_profile": tax_profile,
                "method": "PEPS",
                "lots": breakdown,
            }
            efeitos = {
                "delta_conta": +float(total_net), "delta_pocket": None,
                "delta_invest": {"nome": canon, "delta": -float(total_gross)},
                "create_pocket": None, "create_investment": None,
                "investment_lot_withdrawals": lot_effects,
                "tax_summary": tax_summary,
            }
            cur.execute(
                "insert into launches(user_id, tipo, valor, alvo, nota, criado_em, efeitos, is_internal_movement) "
                "values (%s,%s,%s,%s,%s,%s,%s,%s) returning id",
                (user_id, "resgate_investimento", total_gross, canon, nota, criado_em, Jsonb(efeitos), True),
            )
            launch_id = cur.fetchone()["id"]

        conn.commit()

    return launch_id, new_acc, new_inv, canon, tax_summary
