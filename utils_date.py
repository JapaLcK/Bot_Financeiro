import os
import re
from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo
from dateutil.relativedelta import relativedelta
import calendar

def _tz():
    return ZoneInfo(os.getenv("REPORT_TIMEZONE", "America/Cuiaba"))

def now_tz() -> datetime:
    return datetime.now(_tz())

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

    cleaned = re.sub(r"\b\d{1,2}[\/\-]\d{1,2}(?:[\/\-]\d{2,4})?\b", "", original, flags=re.IGNORECASE).strip()
    dt = datetime.combine(d, time(0, 0), tzinfo=tz)
    return dt, " ".join(cleaned.split())

def parse_date_str(s: str) -> date:
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError("Data inválida. Use YYYY-MM-DD ou DD/MM/YYYY.")

def month_range_today():
    today = datetime.now(_tz()).date()
    start = today.replace(day=1)
    if today.month == 12:
        end = today.replace(day=31)
    else:
        next_month = today.replace(day=28) + timedelta(days=4)
        end = next_month.replace(day=1) - timedelta(days=1)
    return start, end

def months_between(d1: date, d2: date):
    if d2 <= d1:
        return 0
    rd = relativedelta(d2, d1)
    return rd.years * 12 + rd.months

def days_between(d1: date, d2: date):
    return max(0, (d2 - d1).days)

def fmt_br(d) -> str:
    if not d:
        return ""
    # aceita date ou datetime
    try:
        d = d.date()
    except Exception:
        pass
    return d.strftime("%d/%m/%y")


