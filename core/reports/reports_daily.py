from __future__ import annotations
from utils_date import now_tz, _tz
from db import (
    get_balance, list_pockets, accrue_all_investments,
    get_launches_by_period, get_summary_by_period,
    list_users_with_daily_report_enabled, list_identities_by_user,
    list_credit_card_due_reminders, mark_card_reminder_sent,
)
from datetime import time, timedelta, date
from discord.ext import tasks
from core.observability import get_logger

logger = get_logger(__name__)


def _fmt_brl(v: float) -> str:
    s = f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"

def _s(x: str | None) -> str:
    return (x or "").strip()


def _add_months(y: int, m: int, delta: int) -> tuple[int, int]:
    m2 = m + delta
    y2 = y + (m2 - 1) // 12
    m2 = (m2 - 1) % 12 + 1
    return y2, m2


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

def build_daily_report_text(user_id: int) -> str:
    saldo = float(get_balance(user_id) or 0)

    pockets = list_pockets(user_id) or []
    invs    = accrue_all_investments(user_id) or []

    total_pockets = sum(float(p.get("balance") or 0) for p in pockets)
    total_invest  = sum(float(i.get("balance") or 0) for i in invs)
    patrimonio    = saldo + total_pockets + total_invest

    now      = now_tz()
    ref_date = now.date() - timedelta(days=1)   # o report roda às 9h e se refere a ontem

    # Busca lançamentos e totais do dia de referência correto
    launches_dia = get_launches_by_period(user_id, ref_date, ref_date) or []
    summary_dia  = get_summary_by_period(user_id, ref_date, ref_date)

    gasto_dia   = summary_dia.get("despesa", 0.0)
    receita_dia = summary_dia.get("receita", 0.0)

    ref_str      = now.strftime("%d/%m/%Y %H:%M")
    ref_date_str = ref_date.strftime("%d/%m/%Y")
    ref_day_str  = ref_date.strftime("%d/%m")

    lines = []
    lines.append("📊 *Resumo diário do Bot Financeiro*")
    lines.append(f"📅 Dados referentes a: {ref_date_str}")
    lines.append(f"🗓️ Gerado em: {ref_str}")
    lines.append("")
    lines.append(f"💰 *Patrimônio total:* {_fmt_brl(patrimonio)}")
    lines.append("")
    lines.append(f"🏦 Saldo em conta: {_fmt_brl(saldo)}")
    lines.append(f"📦 Total em caixinhas: {_fmt_brl(total_pockets)}")
    lines.append(f"📈 Total investido: {_fmt_brl(total_invest)}")
    lines.append("")
    lines.append(f"📉 Gastos em {ref_day_str}: {_fmt_brl(gasto_dia)}")
    lines.append(f"📈 Receitas em {ref_day_str}: {_fmt_brl(receita_dia)}")
    lines.append(f"📊 Lançamentos em {ref_day_str}: {len(launches_dia)}")
    lines.append("")
    lines.append("──────────────")

    if pockets:
        lines.append("")
        lines.append("📦 *Caixinhas*")
        for p in pockets[:20]:
            lines.append(f"• {_s(p.get('name'))}: {_fmt_brl(float(p.get('balance') or 0))}")

    if invs:
        lines.append("")
        lines.append("📈 *Investimentos*")
        for it in invs[:20]:
            lines.append(f"• {_s(it.get('name'))}: {_fmt_brl(float(it.get('balance') or 0))}")

    lines.append("")
    lines.append("──────────────")

    if launches_dia:
        lines.append("")
        lines.append(f"🧾 *Lançamentos de {ref_date_str}*")
        # mostra até os últimos 5 do dia (get_launches_by_period retorna asc)
        for r in launches_dia[-5:]:
            rid   = r.get("id")
            tipo  = _s(r.get("tipo")).lower()
            valor = float(r.get("valor") or 0)
            nota  = _s(r.get("nota"))
            cat   = _s(r.get("categoria") or "outros")
            icon  = "🔻" if tipo == "despesa" else "🔺"
            lines.append(f"{icon} #{rid} {_fmt_brl(valor)} ({cat}) — {nota}")
    else:
        lines.append("")
        lines.append(f"📭 Nenhum lançamento em {ref_date_str}")

    lines.append("")
    lines.append("──────────────")
    lines.append("")
    lines.append("⚙️ Para desligar o report diário automatico:")
    lines.append("*desligar report diario*")

    msg = "\n".join(lines).strip()
    if len(msg) > 3500:
        msg = msg[:3500] + "\n…"

    return msg

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


def setup_daily_report(bot):
    # evita duplicar task quando o bot reinicia/reconecta
    if not _daily_report_discord.is_running():
        _daily_report_discord.start(bot)
