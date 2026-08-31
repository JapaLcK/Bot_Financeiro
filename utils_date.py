import os
import time as time_module
import re
import calendar
import unicodedata
from datetime import datetime, date, time, timedelta, timezone
from zoneinfo import ZoneInfo
from dateutil.relativedelta import relativedelta


# ---------------- timezone helpers ----------------

def _tz():
    tz_name = os.getenv("REPORT_TIMEZONE") or os.getenv("TZ") or "America/Sao_Paulo"
    return ZoneInfo(tz_name)


def _agora() -> datetime:
    """Instante atual em UTC. Existe para a guarda de `align_process_tz` poder
    comparar os dois lados sem depender do fuso que ela está validando."""
    return datetime.now(timezone.utc)


def tz_name() -> str:
    """Nome IANA do fuso do app, derivado do MESMO `_tz()`.

    Sem `getenv` próprio de propósito: um segundo lugar lendo o ambiente
    divergiria de `_tz()` no dia em que alguém setasse só uma das variáveis.
    """
    return _tz().key


# O que o AMBIENTE REAL trouxe, antes de este módulo escrever qualquer coisa em
# `TZ`. `config/env.py::load_app_env` usa isto para saber se o `TZ` que está no
# ambiente é do operador (e então tem precedência sobre o `.env`) ou é o que
# `align_process_tz` acabou de escrever aqui embaixo (e então não pode roubar do
# `.env` a chance de ser lido).
_TZ_ENV_ORIGINAL = os.environ.get("TZ")


def align_process_tz() -> str:
    """Põe PROCESSO, APP e SESSÃO DO POSTGRES no mesmo fuso. Fonte única.

    São três relógios que precisam concordar, e até aqui não concordavam:

    - o APP já lia `_tz()` (`REPORT_TIMEZONE` → `TZ` → America/Sao_Paulo);
    - o PROCESSO seguia o `TZ` do sistema — UTC no Railway e no CI. É o que
      `date.today()` e `datetime.now()` NAIVE respondem: 52 e 5 chamadas em
      29/08/2026, nenhuma delas tocada por este conserto. Entre elas
      `core/handlers/greeting.py:108`, que dizia "boa noite" às 15h de
      Brasília — corrigido de graça aqui. Os dois números saem DAQUI, e não de
      `grep`: `ast` conta CHAMADA, o `grep` conta texto e soma comentário e
      docstring (`grep -rn "[.]today()"` devolve 170, com `tests/` dentro):

          python3 - <<'EOF'
          import ast, pathlib
          for attr in ("today", "now"):
              print(attr, sum(
                  isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                  and n.func.attr == attr and not n.args and not n.keywords
                  for p in pathlib.Path(".").rglob("*.py")
                  if p.parts[0] not in {"tests", "scripts", "mobile", ".venv", ".claude"}
                  for n in ast.walk(ast.parse(p.read_text(encoding="utf-8")))))
          EOF
    - a SESSÃO do Postgres seguia o fuso do servidor, também UTC. É por ela que
      o banco resolve `::date`, `date_trunc` e a promoção de um `timestamp`
      NAIVE a `timestamptz` — o que desloca em 3h as dezenas de cortes de janela do
      tipo `criado_em < datetime.combine(fim + 1 dia, 00:00)` (db/accounts.py,
      db/plans.py, core/budget_alerts.py, forecast, admin). Um gasto de 31/08
      às 22:00 em São Paulo era lido como setembro.

    A ESCRITA de `criado_em` sempre esteve certa (`datetime.now(_tz())`, aware,
    coluna `timestamptz`): o defeito é de LEITURA, e não há dado corrompido
    nessa coluna. Consertar só a sessão não bastaria — o processo continuaria em
    UTC e as duas pontas divergiriam entre 00:00 e 03:00 UTC.

    `PGTZ` é o que fixa o fuso da sessão: a libpq o lê em TODA conexão nova, o
    que cobre as portas do processo inteiro (o pool síncrono e o `reset` de
    `db/connection.py`, o async de `frontend/routes/shared.py`, o
    `core/admin_dashboard.py`) sem uma linha de código em nenhuma delas. Medido:
    sobrescreve inclusive um `PGTZ` hostil já posto pelo operador.

    O `except Exception` de quem chama isto no import (logo abaixo) é largo de
    propósito. Não é só `ImportError` de `tzset` e `ZoneInfoNotFoundError`:
    `ZoneInfo` levanta `ValueError` para nome com travessia de caminho, esta
    função levanta `RuntimeError` quando a libc não aceita o nome (abaixo), e a
    lista não é fechada. Estreitar deixa um traceback cru subir do import e torna
    INALCANÇÁVEL a guarda legível de `config/env.py::load_app_env`, que é quem
    recusa fuso inválido no boot com `exit(1)`.
    """
    name = _tz().key
    os.environ["TZ"] = name
    os.environ["PGTZ"] = name

    # `time.tzset` é POSIX e NÃO EXISTE no Windows, que o `docs/readme.md`
    # lista como sistema suportado. Sem esta guarda o `ImportError` sobe até o
    # `except` de `load_app_env` e vira `sys.exit(1)`: TODO entrypoint deixaria
    # de subir no Windows, com a mensagem de "fuso inválido" — que ali seria
    # mentira, porque o `ZoneInfo` resolveu o nome sem problema (Codex, #180).
    #
    # O que se perde lá, dito com todas as letras: sem `tzset` o PROCESSO fica
    # no fuso do sistema, então `date.today()` e os `datetime.now()` naive não
    # são alinhados e a divergência processo × app continua existindo no
    # Windows. O que É alinhado lá é a SESSÃO do banco, via `PGTZ` — que é a
    # metade que causa o bug da #178. Windows é ambiente de desenvolvimento
    # aqui; produção é Linux (Railway) e o dev de referência é macOS, e nos dois
    # o alinhamento é completo. Fechar a metade que falta no Windows exigiria
    # não usar `date.today()` em lugar nenhum — que é o PR que este evitou.
    tzset = getattr(time_module, "tzset", None)
    if tzset is None:
        return name
    tzset()

    # `tzset()` NÃO levanta quando a libc não conhece o nome: ela cai para UTC
    # CALADA (medido no macOS em 29/08/2026 com um `Fake/Zone`). Sem esta
    # comparação a função retornava com sucesso deixando o PROCESSO em UTC e o
    # app/sessão no fuso pedido — o invariante desta função quebrado em
    # silêncio, que é o pior modo de falha possível para ela. É `ZoneInfo`
    # (banco do Python) contra a libc (banco do sistema): duas leituras
    # diferentes do mesmo nome.
    #
    # Alcançável só quando as duas divergem — hoje elas não divergem porque
    # `tzdata` (o banco em pip) NÃO está no requirements.txt, e o do sistema
    # existe: `_tz()` roda em TODA escrita de lançamento (`now_tz()`) e a
    # produção grava, logo /usr/share/zoneinfo está lá. É por isso que este PR
    # não acrescenta `tzdata` — a dependência criaria justamente a divergência
    # que esta guarda existe para pegar.
    # Só faz sentido onde o `tzset` acima RODOU: sem ele o processo fica no fuso
    # do sistema de propósito (Windows), e esta comparação acusaria uma
    # divergência que é conhecida e documentada, não um defeito.
    #
    # TRÊS instantes, não só "agora": comparar um só instante deixa passar a
    # zona cujo offset COINCIDE com o do fallback hoje e diverge depois. O
    # exemplo é do Codex (#180, P2), e é concreto: um `Fake/Zone` com o conteúdo
    # de `Europe/London` subindo no inverno dá +00:00 nos dois lados — a guarda
    # passa —, e em março o app vai para +01:00 enquanto `date.today()` fica em
    # UTC, recriando a divergência de fronteira de dia que esta função existe
    # para fechar, já em produção e sem sinal. Janeiro e julho pegam os dois
    # sentidos de horário de verão (norte e sul); `agora` pega o caso em que a
    # zona mudou de regra sem mudar de nome.
    for instante in (datetime(_agora().year, 1, 15, 12, tzinfo=timezone.utc),
                     datetime(_agora().year, 7, 15, 12, tzinfo=timezone.utc),
                     _agora()):
        do_processo = instante.astimezone().utcoffset()
        do_app = instante.astimezone(_tz()).utcoffset()
        if do_processo != do_app:
            raise RuntimeError(
                f"a libc não aceitou o fuso {name!r}: em {instante:%d/%m} o "
                f"processo diz {do_processo} e o app diz {do_app}"
            )
    return name


try:
    align_process_tz()
except Exception:
    # Nada é engolido de vez: sem fuso válido o primeiro `_tz()` levanta do
    # mesmo jeito, e o boot já recusou antes (config/env.py).
    pass


def now_tz() -> datetime:
    return datetime.now(_tz())

def today_tz() -> date:
    return now_tz().date()


def day_tz(dt):
    """Dia de PAREDE de um instante, no fuso do app.

    `dt.date()` cru devolve o dia no fuso da SESSÃO do Postgres. Essa sessão
    ERA UTC no Railway, e um gasto de 26/08 21:30 em São Paulo saía como 27/08
    por esse caminho, enquanto o dashboard, que formata o instante no navegador,
    dizia 26/08. Desde `align_process_tz` (acima) a sessão roda no fuso do app e
    os dois caminhos coincidem — o que não promove `.date()` cru a correto: ele
    continua dependendo de configuração de servidor, e esta função não.
    Fonte única das DUAS listagens que imprimem o dia de um
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
      pelo fuso da SESSÃO. Enquanto a sessão da PRODUÇÃO era `Etc/UTC` (medido
      no Railway em 27/08/2026 08:46 UTC), 27/08 00:00 virava 27/08 00:00Z e
      `day_tz` devolvia 26/08: toda compra de cartão saía com o DIA ANTERIOR
      nessa lista, e só o CI ficava vermelho porque a sessão da máquina local
      era -04 e o erro sumia. Desde `align_process_tz` (acima) a sessão é o fuso
      do app e a promoção cai na meia-noite certa. O que mantém esta função
      necessária é a REGRA, não o fuso: sem hora confiável quem manda é a DATA,
      venha ela de que sessão vier.
    - Open Finance importado ANTES de c474fba (18/08/2026): gravava a `date`
      crua em `criado_em` (timestamptz) → meia-noite no fuso da SESSÃO, que na
      época era UTC na produção, e o dia exibido escorregava um pra trás. Essas
      linhas continuam no banco com MEIA-NOITE UTC gravada em disco: o instante
      é absoluto e não muda com o fuso da sessão, então `align_process_tz` não
      as conserta. Seguem sem `time_known` no `efeitos` (logo `has_time=false`),
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
