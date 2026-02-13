import os
import re
import calendar
from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo
from dateutil.relativedelta import relativedelta


# ---------------- timezone helpers ----------------

def _tz():
    return ZoneInfo(os.getenv("REPORT_TIMEZONE", "America/Cuiaba"))

def now_tz() -> datetime:
    return datetime.now(_tz())

def today_tz() -> date:
    return now_tz().date()


# ---------------- parsing / formatting ----------------

def extract_date_from_text(text: str) -> tuple[datetime | None, str]:
    """
    Procura uma data no texto e retorna (datetime_00h, texto_limpo).

    Aceita:
      - dd/mm, dd-mm
      - dd/mm/yyyy, dd-mm-yyyy
      - hoje, ontem

    Se não achar data, retorna (None, texto_original).
    """
    original = text or ""
    t = original.strip().lower()

    now = now_tz()
    tz = _tz()

    # hoje / ontem
    if re.search(r"\bhoje\b", t):
        cleaned = re.sub(r"\bhoje\b", "", original, flags=re.IGNORECASE).strip()
        dt = datetime.combine(now.date(), time(0, 0), tzinfo=tz)
        return dt, " ".join(cleaned.split())

    if re.search(r"\bontem\b", t):
        cleaned = re.sub(r"\bontem\b", "", original, flags=re.IGNORECASE).strip()
        d = now.date() - timedelta(days=1)
        dt = datetime.combine(d, time(0, 0), tzinfo=tz)
        return dt, " ".join(cleaned.split())

    # dd/mm(/yyyy)?
    m = re.search(r"\b(\d{1,2})[\/\-](\d{1,2})(?:[\/\-](\d{2,4}))?\b", t)
    if not m:
        return None, original

    dd = int(m.group(1))
    mm = int(m.group(2))
    yy_raw = m.group(3)

    if yy_raw:
        yy = int(yy_raw)
        if yy < 100:
            yy += 2000
    else:
        yy = now.year

    try:
        d = date(yy, mm, dd)
    except ValueError:
        return None, original

    cleaned = re.sub(
        r"\b\d{1,2}[\/\-]\d{1,2}(?:[\/\-]\d{2,4})?\b",
        "",
        original,
        flags=re.IGNORECASE
    ).strip()

    dt = datetime.combine(d, time(0, 0), tzinfo=tz)
    return dt, " ".join(cleaned.split())

def parse_date_str(s: str) -> date:
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError("Data inválida. Use YYYY-MM-DD ou DD/MM/YYYY.")

def fmt_br(d) -> str:
    if not d:
        return ""
    # aceita date ou datetime
    try:
        d = d.date()
    except Exception:
        pass
    return d.strftime("%d/%m/%y")


# ---------------- generic ranges / diffs ----------------

def month_range_today():
    """
    Retorna (start, end) do mês corrente, usando timezone do bot.
    """
    today = today_tz()
    start = today.replace(day=1)
    last_day = calendar.monthrange(today.year, today.month)[1]
    end = today.replace(day=last_day)
    return start, end

def months_between(d1: date, d2: date):
    if d2 <= d1:
        return 0
    rd = relativedelta(d2, d1)
    return rd.years * 12 + rd.months

def days_between(d1: date, d2: date):
    return max(0, (d2 - d1).days)


# ---------------- credit card billing helpers ----------------

def clamp_day(year: int, month: int, day: int) -> int:
    """
    Garante que o dia existe no mês (ex: 31 em fevereiro vira 28/29).
    """
    last = calendar.monthrange(year, month)[1]
    return max(1, min(day, last))

def add_months(y: int, m: int, delta: int) -> tuple[int, int]:
    """
    Soma meses (delta pode ser +1, -1, etc) e retorna (year, month).
    """
    mm = m + delta
    yy = y
    while mm > 12:
        mm -= 12
        yy += 1
    while mm < 1:
        mm += 12
        yy -= 1
    return yy, mm

def billing_period_for_close_day(ref: date, close_day: int) -> tuple[date, date]:
    """
    Retorna (period_start, period_end) inclusivo, onde period_end é o dia de fechamento.

    Ex: close_day=10
      ref=2026-02-12 => start=2026-02-11, end=2026-03-10
      ref=2026-02-05 => start=2026-01-11, end=2026-02-10
    """
    y, m = ref.year, ref.month
    close_this_month = date(y, m, clamp_day(y, m, close_day))

    if ref <= close_this_month:
        # fatura fecha neste mês
        end = close_this_month
        py, pm = add_months(y, m, -1)
        prev_close = date(py, pm, clamp_day(py, pm, close_day))
        start = prev_close + timedelta(days=1)
    else:
        # fatura fecha no próximo mês
        ny, nm = add_months(y, m, +1)
        end = date(ny, nm, clamp_day(ny, nm, close_day))
        this_close = close_this_month
        start = this_close + timedelta(days=1)

    return start, end
