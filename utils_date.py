import os
import re
import calendar
from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo
from dateutil.relativedelta import relativedelta


# ---------------- timezone helpers ----------------

def _tz():
    tz_name = os.getenv("REPORT_TIMEZONE") or os.getenv("TZ") or "America/Sao_Paulo"
    return ZoneInfo(tz_name)

def now_tz() -> datetime:
    return datetime.now(_tz())

def today_tz() -> date:
    return now_tz().date()


# ---------------- feriados nacionais (BR) ----------------

def _easter_sunday(year: int) -> date:
    """Domingo de Páscoa (algoritmo de Meeus/Jones/Butcher)."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


_BR_HOLIDAYS_CACHE: dict[int, set[date]] = {}


def br_national_holidays(year: int) -> set[date]:
    """
    Feriados nacionais brasileiros (relevantes para mercado financeiro/ANBIMA):
    fixos federais + móveis baseados na Páscoa.
    """
    cached = _BR_HOLIDAYS_CACHE.get(year)
    if cached is not None:
        return cached

    easter = _easter_sunday(year)
    holidays = {
        date(year, 1, 1),                       # Confraternização Universal
        easter - timedelta(days=48),            # Carnaval (segunda)
        easter - timedelta(days=47),            # Carnaval (terça)
        easter - timedelta(days=2),             # Sexta-feira Santa
        date(year, 4, 21),                      # Tiradentes
        date(year, 5, 1),                       # Dia do Trabalho
        easter + timedelta(days=60),            # Corpus Christi
        date(year, 9, 7),                       # Independência
        date(year, 10, 12),                     # Nossa Senhora Aparecida
        date(year, 11, 2),                      # Finados
        date(year, 11, 15),                     # Proclamação da República
        date(year, 11, 20),                     # Consciência Negra
        date(year, 12, 25),                     # Natal
    }
    _BR_HOLIDAYS_CACHE[year] = holidays
    return holidays


def is_br_business_day(d: date) -> bool:
    """True se for seg-sex e não for feriado nacional brasileiro."""
    if d.weekday() >= 5:
        return False
    return d not in br_national_holidays(d.year)


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

    def _clean(pattern: str) -> str:
        cleaned = re.sub(pattern, " ", original, flags=re.IGNORECASE)
        return " ".join(cleaned.split()).strip(" ,.-")

    # hoje / ontem
    if re.search(r"\bhoje\b", t):
        cleaned = _clean(r"\bhoje\b")
        dt = datetime.combine(now.date(), time(0, 0), tzinfo=tz)
        return dt, cleaned

    if re.search(r"\bontem\b", t):
        cleaned = _clean(r"\bontem\b")
        d = now.date() - timedelta(days=1)
        dt = datetime.combine(d, time(0, 0), tzinfo=tz)
        return dt, cleaned

    # [dia] dd/mm(/yyyy)?
    date_pattern = r"\b(?:dia\s+)?(\d{1,2})[\/\-](\d{1,2})(?:[\/\-](\d{2,4}))?\b"
    m = re.search(date_pattern, t)
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

    cleaned = _clean(date_pattern)

    dt = datetime.combine(d, time(0, 0), tzinfo=tz)
    return dt, cleaned

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

# funcao para checar report diario
def should_run_daily_at(now: datetime, hour: int = 9, minute: int = 0) -> bool:
    """
    True se 'now' (no fuso do bot) estiver no minuto exato do report.
    Útil pra runners que rodam a cada 1 minuto.
    """
    return now.hour == hour and now.minute == minute

# quando eh o proximo report diario
def next_daily_run(hour: int = 9, minute: int = 0) -> datetime:
    tz = _tz()
    now = now_tz()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target = target + timedelta(days=1)
    return target
