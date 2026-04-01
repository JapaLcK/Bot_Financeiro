from __future__ import annotations
from utils_date import now_tz, _tz
from db import (
    get_balance, list_pockets, list_investments,
    get_launches_by_period, get_summary_by_period,
    list_users_with_daily_report_enabled, list_identities_by_user,
)
from datetime import time, timedelta
from discord.ext import tasks


def _fmt_brl(v: float) -> str:
    s = f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"

def _s(x: str | None) -> str:
    return (x or "").strip()

def build_daily_report_text(user_id: int) -> str:
    saldo = float(get_balance(user_id) or 0)

    pockets = list_pockets(user_id) or []
    invs    = list_investments(user_id) or []

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
    print("Daily report rodou")
    # busca usuários com report habilitado
    user_ids = list_users_with_daily_report_enabled(9, 0)

    for uid in user_ids:
        msg = build_daily_report_text(uid)

        # manda para todas identidades discord ligadas no user
        ids = list_identities_by_user(uid)
        discord_targets = [x["external_id"] for x in ids if x["provider"] == "discord"]

        for discord_id in discord_targets:
            try:
                user = await bot.fetch_user(int(discord_id))
                if user:
                    await user.send(msg)
            except Exception:
                pass


def setup_daily_report(bot):
    # evita duplicar task quando o bot reinicia/reconecta
    if not _daily_report_discord.is_running():
        _daily_report_discord.start(bot)