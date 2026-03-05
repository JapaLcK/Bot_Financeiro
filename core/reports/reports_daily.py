from __future__ import annotations

from db import get_balance, list_pockets, list_investments, list_launches

def _fmt_brl(v: float) -> str:
    # simples e confiável sem depender de locale
    s = f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"

def _s(x: str | None) -> str:
    return (x or "").strip()

def build_daily_report_text(user_id: int) -> str:
    saldo = float(get_balance(user_id) or 0)

    pockets = list_pockets(user_id) or []
    invs = list_investments(user_id) or []
    last5 = list_launches(user_id, limit=5) or []

    total_pockets = sum(float(p.get("balance") or 0) for p in pockets)
    total_invest = sum(float(i.get("balance") or 0) for i in invs)

    patrimonio = saldo + total_pockets + total_invest

    gasto_hoje = 0
    receita_hoje = 0

    for r in last5:
        tipo = (r.get("tipo") or "").lower()
        valor = float(r.get("valor") or 0)

        if tipo == "despesa":
            gasto_hoje += valor
        elif tipo == "receita":
            receita_hoje += valor

    lines = []

    lines.append("📊 *Resumo diário do Bot Financeiro*")
    lines.append("")
    lines.append(f"💰 *Patrimônio total:* {_fmt_brl(patrimonio)}")
    lines.append("")
    lines.append(f"🏦 Saldo em conta: {_fmt_brl(saldo)}")
    lines.append(f"📦 Total em caixinhas: {_fmt_brl(total_pockets)}")
    lines.append(f"📈 Total investido: {_fmt_brl(total_invest)}")
    lines.append("")
    lines.append(f"📉 Gastos hoje: {_fmt_brl(gasto_hoje)}")
    lines.append(f"📈 Receitas hoje: {_fmt_brl(receita_hoje)}")
    lines.append(f"📊 Lançamentos hoje: {len(last5)}")
    lines.append("")
    lines.append("──────────────")

    if pockets:
        lines.append("")
        lines.append("📦 *Caixinhas*")
        for p in pockets[:20]:
            name = _s(p.get("name"))
            bal = float(p.get("balance") or 0)
            lines.append(f"• {name}: {_fmt_brl(bal)}")

    if invs:
        lines.append("")
        lines.append("📈 *Investimentos*")
        for it in invs[:20]:
            name = _s(it.get("name"))
            bal = float(it.get("balance") or 0)
            lines.append(f"• {name}: {_fmt_brl(bal)}")

    lines.append("")
    lines.append("──────────────")

    if last5:
        lines.append("")
        lines.append("🧾 *Últimos 5 lançamentos*")

        for r in last5:
            rid = r.get("id")
            tipo = _s(r.get("tipo")).lower()
            valor = float(r.get("valor") or 0)
            nota = _s(r.get("nota"))
            cat = _s(r.get("categoria") or "outros")

            icon = "🔻" if tipo == "despesa" else "🔺"

            lines.append(
                f"{icon} #{rid} {_fmt_brl(valor)} ({cat}) — {nota}"
            )

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
from datetime import time
from discord.ext import tasks

from utils_date import _tz
from db import list_users_with_daily_report_enabled, list_identities_by_user


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