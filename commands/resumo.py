from datetime import date, datetime
from utils_date import _tz
from db import get_summary_by_period

def today_local() -> date:
    return datetime.now(_tz()).date()

def month_range_local(d: date) -> tuple[date, date]:
    start = d.replace(day=1)
    if d.month == 12:
        next_month = date(d.year + 1, 1, 1)
    else:
        next_month = date(d.year, d.month + 1, 1)
    end = next_month.fromordinal(next_month.toordinal() - 1)
    return start, end

def format_money(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

async def handle_resumo(message, user_id: int, text: str):
    t = text.lower().strip()

    d = today_local()

    if t == "resumo" or t == "resumo hoje":
        start = end = d
        label = "Resumo de hoje"
    elif t in ("resumo mes", "resumo mês"):
        start, end = month_range_local(d)
        label = "Resumo do mês"
    else:
        await message.reply("Use: `resumo hoje` ou `resumo mes`")
        return

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
