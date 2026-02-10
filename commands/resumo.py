import re
from datetime import datetime, date, timedelta
from timezone import _tz
from db import get_summary_by_period

def format_money(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def today_local() -> date:
    return datetime.now(_tz()).date()

def month_range(d: date) -> tuple[date, date]:
    start = d.replace(day=1)
    if d.month == 12:
        next_month = date(d.year + 1, 1, 1)
    else:
        next_month = date(d.year, d.month + 1, 1)
    end = next_month - timedelta(days=1)
    return start, end

def week_range_sun_start(d: date) -> tuple[date, date]:
    # python: Monday=0 ... Sunday=6
    # queremos semana começando no domingo:
    days_since_sun = (d.weekday() + 1) % 7  # domingo -> 0, segunda -> 1 ...
    start = d - timedelta(days=days_since_sun)
    end = start + timedelta(days=6)
    return start, end

def parse_ddmmyyyy(s: str) -> date | None:
    s = s.strip()
    try:
        return datetime.strptime(s, "%d/%m/%Y").date()
    except ValueError:
        return None

def parse_resumo_range(text: str) -> tuple[date, date, str] | None:
    """
    Retorna (start, end, label) ou None se não reconheceu.
    """
    t = text.strip().lower()

    # remove prefixo "resumo"
    t = re.sub(r"^\s*resumo\s*", "", t).strip()

    today = today_local()

    if t == "" or t == "hoje" or t == "de hoje":
        return today, today, "Resumo de hoje"

    if t == "ontem" or t == "de ontem":
        d = today - timedelta(days=1)
        return d, d, "Resumo de ontem"

    if t in ("mes", "mês", "do mes", "do mês"):
        s, e = month_range(today)
        return s, e, "Resumo do mês"

    if t in ("semana", "da semana", "essa semana"):
        s, e = week_range_sun_start(today)
        return s, e, "Resumo da semana (dom–sáb)"

    # período: "dd/mm/yyyy - dd/mm/yyyy" (com espaços opcionais)
    m = re.search(r"(\d{2}/\d{2}/\d{4})\s*-\s*(\d{2}/\d{2}/\d{4})", t)
    if m:
        d1 = parse_ddmmyyyy(m.group(1))
        d2 = parse_ddmmyyyy(m.group(2))
        if not d1 or not d2:
            return None
        start, end = (d1, d2) if d1 <= d2 else (d2, d1)
        return start, end, f"Resumo do período ({start.strftime('%d/%m/%Y')}–{end.strftime('%d/%m/%Y')})"

    # data específica: "dd/mm/yyyy"
    d = parse_ddmmyyyy(t)
    if d:
        return d, d, f"Resumo de {d.strftime('%d/%m/%Y')}"

    return None

async def handle_resumo(message, user_id: int, text: str):
    parsed = parse_resumo_range(text)
    if not parsed:
        await message.reply(
            "Use:\n"
            "`resumo` / `resumo hoje`\n"
            "`resumo ontem`\n"
            "`resumo semana`\n"
            "`resumo mes`\n"
            "`resumo DD/MM/AAAA`\n"
            "`resumo DD/MM/AAAA - DD/MM/AAAA`"
        )
        return

    start, end, label = parsed

    sums = get_summary_by_period(user_id, start, end)
    receitas = sums.get("receita", 0.0)
    despesas = sums.get("despesa", 0.0)
    aportes  = sums.get("aporte_investimento", 0.0)
    saldo = receitas - despesas

    await message.reply(
        f"{label}\n"
        f"Entradas: {format_money(receitas)}\n"
        f"Saídas: {format_money(despesas)}\n"
        f"Aportes: {format_money(aportes)}\n"
        f"Saldo: {format_money(saldo)}"
    )
