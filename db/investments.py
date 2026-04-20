"""
db/investments.py — Investimentos: criar, aportar, resgatar, juros e CDI.
"""
import calendar
import requests
from datetime import datetime, date, timedelta
from decimal import Decimal

from psycopg.types.json import Jsonb
import psycopg

from utils_date import _tz

from .connection import get_conn
from .users import ensure_user


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
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[WARN] Falha ao buscar série SGS {series_code} no BCB: {e}")
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
            print(f"[WARN] Item inválido do BCB ignorado: {item} | erro={e}")

    if to_upsert:
        cur.executemany(
            "insert into market_rates(code, ref_date, value) values ('CDI', %s, %s) "
            "on conflict (code, ref_date) do update set value=excluded.value",
            to_upsert,
        )

    return cached


def get_latest_cdi(cur) -> tuple[date, float] | None:
    """Retorna (data, valor_percent_ao_dia) da CDI mais recente."""
    today = datetime.now(_tz()).date()
    start = today - timedelta(days=15)

    data = _fetch_sgs_series_json(12, start, today)
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
    today = datetime.now(_tz()).date()
    start = today - timedelta(days=15)

    data = _fetch_sgs_series_json(4389, start, today)
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


def get_latest_cdi_daily_pct() -> float:
    """Retorna CDI diária em % ao dia (ex: 0.0550)."""
    today = datetime.now(_tz()).date()
    start = today - timedelta(days=10)

    data = _fetch_sgs_series_json(12, start, today)
    if not data:
        raise RuntimeError("CDI_DAILY_NOT_AVAILABLE")

    latest = None
    for item in data:
        latest = float(str(item["valor"]).replace(",", "."))

    if latest is None:
        raise RuntimeError("CDI_DAILY_NOT_AVAILABLE")

    return float(latest)


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
    """
    if today is None:
        today = datetime.now(_tz()).date()

    cur.execute(
        "select id, balance, rate, period, last_date from investments "
        "where id=%s and user_id=%s for update",
        (inv_id, user_id),
    )
    inv = cur.fetchone()
    if not inv:
        raise LookupError("INV_NOT_FOUND")

    last_date = inv["last_date"]
    if last_date is None:
        return Decimal(inv["balance"])

    n = _business_days_between(last_date, today)
    if n <= 0:
        return Decimal(inv["balance"])

    bal = Decimal(inv["balance"])
    period = inv["period"]
    rate = float(inv["rate"])

    if period == "cdi":
        mult = float(inv["rate"])
        start = last_date + timedelta(days=1)
        cdi_map = _get_cdi_daily_map(cur, start, today)
        cdi_days = sorted(d for d in cdi_map.keys() if last_date < d <= today)

        if not cdi_days:
            return Decimal(inv["balance"])

        factor = 1.0
        for d in cdi_days:
            factor *= (1.0 + (cdi_map[d] / 100.0) * mult)

        new_bal = Decimal(str(float(bal) * factor))
        applied_until = cdi_days[-1]

    else:
        if period == "daily":
            daily_rate = rate
        elif period == "monthly":
            daily_rate = (1.0 + rate) ** (1.0 / 21.0) - 1.0
        elif period == "yearly":
            daily_rate = (1.0 + rate) ** (1.0 / 252.0) - 1.0
        else:
            daily_rate = 0.0

        if daily_rate > 0:
            new_bal = Decimal(str(float(bal) * (1.0 + daily_rate) ** n))
        else:
            new_bal = bal
        applied_until = today

    cur.execute(
        "update investments set balance=%s, last_date=%s where id=%s and user_id=%s",
        (new_bal, applied_until, inv_id, user_id),
    )
    return new_bal


# ──────────────────────────────────────────────────────────────────────────────
# CRUD de investimentos
# ──────────────────────────────────────────────────────────────────────────────

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


def create_investment_db(user_id: int, name: str, rate: float, period: str, nota: str | None = None):
    """
    Cria investimento (suporta period='cdi'). Retorna (launch_id, inv_id, canon_name).
    Se já existir, retorna (None, inv_id, canon_name).
    """
    ensure_user(user_id)
    name = (name or "").strip()
    if not name:
        raise ValueError("EMPTY_NAME")
    if period not in ("daily", "monthly", "yearly", "cdi"):
        raise ValueError("INVALID_PERIOD")

    r = Decimal(str(rate))
    if r <= 0:
        raise ValueError("INVALID_RATE")

    criado_em = datetime.now(_tz())
    today = date.today()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into investments(user_id, name, balance, rate, period, last_date) "
                "values (%s,%s,0,%s,%s,%s) on conflict (user_id, name) do nothing "
                "returning id, name",
                (user_id, name, r, period, today),
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
            }
            cur.execute(
                "insert into launches(user_id, tipo, valor, alvo, nota, criado_em, efeitos) "
                "values (%s,%s,%s,%s,%s,%s,%s) returning id",
                (user_id, "create_investment", Decimal("0"), canon, nota, criado_em, Jsonb(efeitos)),
            )
            launch_id = cur.fetchone()["id"]

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
                "select id, name, balance, rate, period, last_date from investments "
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
                "select id, name, balance, rate, period, last_date "
                "from investments where user_id=%s order by lower(name)",
                (user_id,),
            )
            return cur.fetchall()


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
                "select id, name, balance, rate, period, last_date "
                "from investments where user_id=%s order by lower(name)",
                (user_id,),
            )
            out = cur.fetchall()

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

            cur.execute(
                "update investments set balance = balance + %s where id=%s returning balance",
                (v, inv_id),
            )
            new_inv = cur.fetchone()["balance"]

            efeitos = {
                "delta_conta": -float(v), "delta_pocket": None,
                "delta_invest": {"nome": canon, "delta": +float(v)},
                "create_pocket": None, "create_investment": None,
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
    """Investimento → Conta (com accrual antes). Retorna (launch_id, new_acc, new_inv, canon)."""
    ensure_user(user_id)
    v = Decimal(str(amount))
    if v <= 0:
        raise ValueError("AMOUNT_INVALID")

    criado_em = datetime.now(_tz())
    today = datetime.now(_tz()).date()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id, name from investments "
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
                "update investments set balance = balance - %s where id=%s returning balance",
                (v, inv_id),
            )
            new_inv = cur.fetchone()["balance"]

            cur.execute(
                "update accounts set balance = balance + %s where user_id=%s returning balance",
                (v, user_id),
            )
            new_acc = cur.fetchone()["balance"]

            efeitos = {
                "delta_conta": +float(v), "delta_pocket": None,
                "delta_invest": {"nome": canon, "delta": -float(v)},
                "create_pocket": None, "create_investment": None,
            }
            cur.execute(
                "insert into launches(user_id, tipo, valor, alvo, nota, criado_em, efeitos, is_internal_movement) "
                "values (%s,%s,%s,%s,%s,%s,%s,%s) returning id",
                (user_id, "resgate_investimento", v, canon, nota, criado_em, Jsonb(efeitos), True),
            )
            launch_id = cur.fetchone()["id"]

        conn.commit()

    return launch_id, new_acc, new_inv, canon
