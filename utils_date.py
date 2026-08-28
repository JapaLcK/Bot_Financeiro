import os
import re
import calendar
import unicodedata
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


def day_tz(dt):
    """Dia de PAREDE de um instante, no fuso do app.

    `dt.date()` cru devolve o dia no fuso da SESSÃO do Postgres — UTC no
    Railway. Um gasto de 26/08 21:30 em São Paulo saía como 27/08 por esse
    caminho, enquanto o dashboard, que formata o instante no navegador, dizia
    26/08. Fonte única das DUAS listagens que imprimem o dia de um
    `criado_em` (`list_launches_by_category`, db/accounts.py; `list_launches`,
    core/handlers/launches.py): com uma cópia em cada lado, "liste lazer" e
    "meus últimos lançamentos" divergiam em um dia para o mesmo lançamento.

    Naive (a perna do crédito, `purchased_at::timestamp`) já é dia de parede:
    passa direto. Sem `.date()` (str, None) volta como veio — quem chama trata.
    """
    if dt is None or not hasattr(dt, "date"):
        return dt
    return (dt.astimezone(_tz()) if dt.tzinfo else dt).date()


def launch_day(dt, posted_at=None, has_time=True):
    """Dia de um lançamento. Sem hora confiável, quem manda é a DATA.

    `day_tz` converte um INSTANTE — e onde instante não existe ele inventa um.
    Duas fontes chegam sem hora e as duas pagaram por isso:

    - compra no crédito: `purchased_at` é `date` (db/schema.py) e na UNION de
      `list_launches_by_category` o Postgres promove `timestamp` → `timestamptz`
      pelo fuso da SESSÃO. A sessão da PRODUÇÃO é `Etc/UTC` (medido no Railway em
      27/08/2026 08:46 UTC), então 27/08 00:00 vira 27/08 00:00Z e `day_tz`
      devolve 26/08: toda compra de cartão está saindo com o DIA ANTERIOR nessa
      lista hoje — não é risco futuro. Na máquina local a sessão é -04 e o erro
      some, que é por que só o CI ficou vermelho.
    - Open Finance importado ANTES de c474fba (18/08/2026): gravava a `date`
      crua em `criado_em` (timestamptz) → meia-noite no fuso da SESSÃO, que na
      produção é UTC, e o dia exibido escorregava um pra trás. Essas linhas
      continuam no banco, sem `time_known` no `efeitos` (logo `has_time=false`),
      e só o `posted_at` delas está certo.

    NÃO vale para o que os importadores gravam HOJE: extrato OFX/CSV/PDF
    (`ofx_import.py:209`, `statement_import.py:666`) e Open Finance sem hora
    (`db/open_finance.py:1197`) já gravam `criado_em` = MEIO-DIA local do
    `posted_at`, então ali `day_tz(criado_em) == posted_at` e esta função não
    muda nada — a justificativa "criado_em é a hora da importação" era falsa.

    Editar a data pelo dashboard mexe nos DOIS campos (`update_launch_fields`,
    db/accounts.py); sem isso `posted_at` velho engolia a edição pra sempre.

    `has_time` é o mesmo CASE do SQL (`LAUNCH_HAS_TIME_SQL`, db/connection.py).
    Sem `posted_at` cai em `day_tz` — o comportamento de hoje.
    """
    if not has_time and posted_at is not None:
        return posted_at
    return day_tz(dt)


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

# ---------------- parsing de PERÍODO em linguagem natural ----------------

# Nomes de mês (sem acento — o texto é normalizado antes de casar). Inclui
# abreviações comuns. Usado por parse_period_from_text.
_MONTHS_PT: dict[str, int] = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6,
    "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
    "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
    "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12,
}

_MONTH_NAMES_PT = [
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
]


def _strip_accents(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").lower()
    return "".join(c for c in s if not unicodedata.combining(c))


def parse_period_from_text(text: str) -> tuple[date, date, str] | None:
    """Interpreta um período em linguagem natural PT-BR.

    Retorna ``(start_date, end_date, label)`` com datas INCLUSIVAS e um rótulo
    pronto pra frase (ex.: "esta semana", "no mês passado", "em julho"), ou
    ``None`` se nenhum período for reconhecido.

    Reconhece: hoje, ontem, anteontem, esta/essa semana, semana passada,
    este/esse mês, mês passado, últimos N dias, este/esse ano, ano passado,
    nome de mês ("julho", "em julho") e data única dd/mm(/aaaa).
    """
    if not text:
        return None

    t = _strip_accents(text)
    today = today_tz()

    # --- dias pontuais ---
    if re.search(r"\bhoje\b", t):
        return today, today, "hoje"
    if re.search(r"\banteontem\b", t):
        d = today - timedelta(days=2)
        return d, d, "anteontem"
    if re.search(r"\bontem\b", t):
        d = today - timedelta(days=1)
        return d, d, "ontem"

    # --- últimos N dias ---
    m = re.search(r"\bultim[oa]s?\s+(\d{1,3})\s+dias?\b", t)
    if m:
        n = max(1, int(m.group(1)))
        return today - timedelta(days=n - 1), today, f"nos últimos {n} dias"

    # --- semana ---
    if re.search(r"\b(semana passada|ultima semana|semana anterior)\b", t):
        base = today - timedelta(days=7)
        monday = base - timedelta(days=base.weekday())
        return monday, monday + timedelta(days=6), "na semana passada"
    if re.search(r"\bsemana\b", t):
        monday = today - timedelta(days=today.weekday())
        return monday, today, "esta semana"

    # --- ano ---
    _has_explicit_year = re.search(r"(?<![\d/\-])20\d{2}(?![\d/\-])", t)
    if re.search(r"\b(ano passado|ultimo ano|ano anterior)\b", t):
        y = today.year - 1
        return date(y, 1, 1), date(y, 12, 31), "no ano passado"
    # "este ano"/"no ano" = ano corrente — mas só se NÃO houver um ano explícito
    # ("no ano de 2023" deve virar 2023, tratado no bloco de ano específico).
    if not _has_explicit_year and (
        re.search(r"\b(este|esse|neste|nesse)\s+ano\b", t) or re.search(r"\bno ano\b", t)
    ):
        # ano-calendário cheio (não corta em hoje) pra bater com o dashboard,
        # que atribui gasto de cartão pelo mês da fatura — parcelas futuras do
        # ano corrente contam.
        return date(today.year, 1, 1), date(today.year, 12, 31), "neste ano"

    # --- mês passado (antes de casar nome de mês / "mês" genérico) ---
    if re.search(r"\b(mes passado|ultimo mes|mes anterior)\b", t):
        first_this = today.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        return last_prev.replace(day=1), last_prev, "no mês passado"

    # --- nome de mês ("julho", "em julho") ---
    for name, num in _MONTHS_PT.items():
        if re.search(rf"\b{name}\b", t):
            year = today.year
            # se o mês ainda não chegou neste ano, assume o ano passado
            if num > today.month:
                year -= 1
            last_day = calendar.monthrange(year, num)[1]
            # mês-calendário cheio (mesmo o corrente): o dashboard conta cartão
            # pelo mês da fatura, então parcelas que fecham depois de hoje mas
            # dentro do mês entram.
            start = date(year, num, 1)
            end = date(year, num, last_day)
            return start, end, f"em {_MONTH_NAMES_PT[num - 1]}"

    # --- mês corrente ("este mês", "do mês", "mês") ---
    if re.search(r"\bmes\b", t):
        start = today.replace(day=1)
        last_day = calendar.monthrange(today.year, today.month)[1]
        return start, date(today.year, today.month, last_day), "neste mês"

    # --- ano específico ("em 2026", "de 2025") ---
    # Lookarounds excluem anos que fazem parte de uma data dd/mm/aaaa (tratada
    # logo abaixo) ou de um número maior. Consultas de gasto não citam valores,
    # então um 20xx solto é sempre um ano.
    m = re.search(r"(?<![\d/\-])(20\d{2})(?![\d/\-])", t)
    if m:
        year = int(m.group(1))
        # ano-calendário cheio (ver comentário em "este ano").
        return date(year, 1, 1), date(year, 12, 31), f"em {year}"

    # --- data única dd/mm(/aaaa) ---
    dt, _ = extract_date_from_text(text)
    if dt:
        d = dt.date()
        return d, d, d.strftime("%d/%m/%Y")

    return None


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
