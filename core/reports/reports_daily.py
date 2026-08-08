from __future__ import annotations
from utils_date import now_tz, _tz
from db import (
    get_consolidated_balance, get_launches_by_period, get_summary_by_period,
    list_users_with_daily_report_enabled, list_identities_by_user,
    list_users_with_weekly_report_enabled, list_users_with_monthly_report_enabled,
    list_credit_card_due_reminders, mark_card_reminder_sent,
)
from datetime import time, timedelta, date
from discord.ext import tasks
from core.observability import get_logger

logger = get_logger(__name__)


def _fmt_brl(v: float) -> str:
    s = f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"


def _add_months(y: int, m: int, delta: int) -> tuple[int, int]:
    m2 = m + delta
    y2 = y + (m2 - 1) // 12
    m2 = (m2 - 1) % 12 + 1
    return y2, m2


def _saldo_atual(user_id: int) -> float:
    """Saldo verdadeiro do usuário: Carteira manual + bancos conectados (Open Finance).

    Em beta (consolidated_balance_enabled): fora do allowlist de teste, mantém o
    comportamento antigo (só a Carteira manual)."""
    from core.services.plan_service import consolidated_balance_enabled

    cb = get_consolidated_balance(user_id) or {}
    if consolidated_balance_enabled(user_id):
        return float(cb.get("consolidated") or 0)
    return float(cb.get("manual") or 0)


def _card_bill_due_date(period_end: date, closing_day: int, due_day: int) -> date:
    if due_day >= closing_day:
        return date(period_end.year, period_end.month, due_day)
    y2, m2 = _add_months(period_end.year, period_end.month, 1)
    return date(y2, m2, due_day)


def build_due_bill_reminders(user_id: int, today: date | None = None) -> list[dict]:
    today = today or now_tz().date()
    reminders = []

    for row in list_credit_card_due_reminders(user_id, today) or []:
        due_date = _card_bill_due_date(row["period_end"], int(row["closing_day"]), int(row["due_day"]))
        days_before = int(row.get("reminders_days_before") or 0)
        total = float(row.get("total") or 0)
        paid = float(row.get("paid_amount") or 0)
        due_amount = max(0.0, total - paid)
        days_left = (due_date - today).days

        if due_amount <= 0:
            continue
        if days_left != days_before:
            continue
        if row.get("reminder_last_sent_on") == today:
            continue

        message = (
            f"💳 Lembrete de fatura: {row['card_name']}\n"
            f"📅 Vence em {days_left} dia(s): {due_date.strftime('%d/%m/%Y')}\n"
            f"🧾 Fechamento desta fatura: {row['period_end'].strftime('%d/%m/%Y')}\n"
            f"💰 Total: {_fmt_brl(total)} | Pago: {_fmt_brl(paid)} | Em aberto: {_fmt_brl(due_amount)}"
        )
        reminders.append({
            "card_id": int(row["card_id"]),
            "bill_id": int(row["bill_id"]),
            "message": message,
        })

    return reminders


def build_daily_report_summary(user_id: int) -> dict[str, str]:
    saldo = _saldo_atual(user_id)

    now      = now_tz()
    ref_date = now.date() - timedelta(days=1)   # o report roda às 9h e se refere a ontem

    # Busca lançamentos e totais do dia de referência correto
    launches_dia = get_launches_by_period(user_id, ref_date, ref_date) or []
    summary_dia  = get_summary_by_period(user_id, ref_date, ref_date)

    gasto_dia   = summary_dia.get("despesa", 0.0)
    receita_dia = summary_dia.get("receita", 0.0)

    return {
        "ref_date": ref_date.strftime("%d/%m/%Y"),
        "saldo": _fmt_brl(saldo),
        "gastos": _fmt_brl(gasto_dia),
        "receita": _fmt_brl(receita_dia),
        "lancamentos": str(len(launches_dia)),
    }


def build_daily_report_text(user_id: int) -> str:
    summary = build_daily_report_summary(user_id)

    lines = []
    lines.append("📊 *Resumo diário do Bot Financeiro*")
    lines.append(f"📅 Dados referentes a: {summary['ref_date']}")
    lines.append("")
    lines.append(f"🏦 Saldo atual: {summary['saldo']}")
    lines.append(f"📉 Gastos de ontem: {summary['gastos']}")
    lines.append(f"📈 Receitas de ontem: {summary['receita']}")
    lines.append(f"📊 Lançamentos de ontem: {summary['lancamentos']}")

    return "\n".join(lines).strip()


# --- resumos por período (semanal / mensal) ---

def _build_period_report_summary(user_id: int, start_date: date, end_date: date) -> dict[str, str]:
    saldo = _saldo_atual(user_id)

    launches = get_launches_by_period(user_id, start_date, end_date) or []
    summary  = get_summary_by_period(user_id, start_date, end_date)

    return {
        "start": start_date.strftime("%d/%m/%Y"),
        "end": end_date.strftime("%d/%m/%Y"),
        "saldo": _fmt_brl(saldo),
        "gastos": _fmt_brl(summary.get("despesa", 0.0)),
        "receita": _fmt_brl(summary.get("receita", 0.0)),
        "lancamentos": str(len(launches)),
    }


def build_weekly_report_summary(user_id: int, closed: bool = False) -> dict[str, str]:
    """Resumo semanal.

    closed=False (sob demanda): semana atual, de segunda até hoje.
    closed=True  (agendado na segunda): semana anterior completa (seg → dom).
    """
    today = now_tz().date()
    this_monday = today - timedelta(days=today.weekday())
    if closed:
        start = this_monday - timedelta(days=7)   # segunda da semana passada
        end   = this_monday - timedelta(days=1)   # domingo da semana passada
    else:
        start = this_monday
        end   = today
    return _build_period_report_summary(user_id, start, end)


def build_monthly_report_summary(user_id: int, closed: bool = False) -> dict[str, str]:
    """Resumo mensal.

    closed=False (sob demanda): mês atual, do dia 1 até hoje.
    closed=True  (agendado no dia 1): mês anterior completo.
    """
    today = now_tz().date()
    if closed:
        end   = today.replace(day=1) - timedelta(days=1)  # último dia do mês anterior
        start = end.replace(day=1)                         # dia 1 do mês anterior
    else:
        start = today.replace(day=1)
        end   = today
    return _build_period_report_summary(user_id, start, end)


def build_weekly_report_text(user_id: int, closed: bool = False) -> str:
    summary = build_weekly_report_summary(user_id, closed=closed)

    lines = []
    lines.append("📊 *Resumo semanal do Bot Financeiro*")
    lines.append(f"📅 Período: {summary['start']} a {summary['end']}")
    lines.append("")
    lines.append(f"🏦 Saldo atual: {summary['saldo']}")
    lines.append(f"📉 Gastos da semana: {summary['gastos']}")
    lines.append(f"📈 Receitas da semana: {summary['receita']}")
    lines.append(f"📊 Lançamentos da semana: {summary['lancamentos']}")

    return "\n".join(lines).strip()


def build_monthly_report_text(user_id: int, closed: bool = False) -> str:
    summary = build_monthly_report_summary(user_id, closed=closed)

    lines = []
    lines.append("📊 *Resumo mensal do Bot Financeiro*")
    lines.append(f"📅 Período: {summary['start']} a {summary['end']}")
    lines.append("")
    lines.append(f"🏦 Saldo atual: {summary['saldo']}")
    lines.append(f"📉 Gastos do mês: {summary['gastos']}")
    lines.append(f"📈 Receitas do mês: {summary['receita']}")
    lines.append(f"📊 Lançamentos do mês: {summary['lancamentos']}")

    return "\n".join(lines).strip()

# --- scheduler Discord (09:00) ---

@tasks.loop(time=time(hour=9, minute=0, tzinfo=_tz()))
async def _daily_report_discord(bot):
    # busca usuários com report habilitado
    user_ids = list_users_with_daily_report_enabled(9, 0)
    logger.info("Daily report iniciado para %d usuários", len(user_ids))

    for uid in user_ids:
        msg = build_daily_report_text(uid)
        reminders = build_due_bill_reminders(uid)

        # manda para todas identidades discord ligadas no user
        ids = list_identities_by_user(uid)
        discord_targets = [x["external_id"] for x in ids if x["provider"] == "discord"]

        for discord_id in discord_targets:
            try:
                user = await bot.fetch_user(int(discord_id))
                if user:
                    for reminder in reminders:
                        await user.send(reminder["message"])
                    await user.send(msg)
            except Exception as e:
                logger.error("Falha ao enviar daily report para discord_id=%s: %s", discord_id, e, exc_info=True)

        for reminder in reminders:
            try:
                mark_card_reminder_sent(uid, reminder["card_id"], now_tz().date())
            except Exception as e:
                logger.error("Falha ao marcar reminder como enviado (card_id=%s): %s", reminder.get("card_id"), e, exc_info=True)


# --- scheduler Discord resumos periódicos (semanal seg / mensal dia 1, 09:00) ---

@tasks.loop(time=time(hour=9, minute=0, tzinfo=_tz()))
async def _periodic_reports_discord(bot):
    today     = now_tz().date()
    is_monday = today.weekday() == 0   # segunda-feira → resumo semanal
    is_first  = today.day == 1         # dia 1 do mês  → resumo mensal

    if not (is_monday or is_first):
        return

    # toggles independentes: cada resumo tem seu próprio liga/desliga
    weekly_users  = set(list_users_with_weekly_report_enabled())  if is_monday else set()
    monthly_users = set(list_users_with_monthly_report_enabled()) if is_first else set()
    user_ids = weekly_users | monthly_users
    logger.info(
        "Resumos periódicos iniciados (semanal=%d, mensal=%d usuários)",
        len(weekly_users), len(monthly_users),
    )

    for uid in user_ids:
        ids = list_identities_by_user(uid)
        discord_targets = [x["external_id"] for x in ids if x["provider"] == "discord"]
        if not discord_targets:
            continue

        # closed=True → resume o período que acabou de fechar (semana/mês anterior).
        # Sem claim aqui: o tasks.loop dispara uma vez ao dia (mesma estratégia do
        # _daily_report_discord). O claim é usado só no WhatsApp, cujo loop faz
        # polling a cada 30s e precisa do dedup atômico — se o Discord também
        # consumisse o claim, usuários com os dois canais receberiam em um só.
        messages = []
        if uid in weekly_users:
            messages.append(build_weekly_report_text(uid, closed=True))
        if uid in monthly_users:
            messages.append(build_monthly_report_text(uid, closed=True))

        if not messages:
            continue

        for discord_id in discord_targets:
            try:
                user = await bot.fetch_user(int(discord_id))
                if user:
                    for msg in messages:
                        await user.send(msg)
            except Exception as e:
                logger.error("Falha ao enviar resumo periódico para discord_id=%s: %s", discord_id, e, exc_info=True)


def setup_daily_report(bot):
    # evita duplicar task quando o bot reinicia/reconecta
    if not _daily_report_discord.is_running():
        _daily_report_discord.start(bot)
    if not _periodic_reports_discord.is_running():
        _periodic_reports_discord.start(bot)
