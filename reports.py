from discord.ext import tasks
from datetime import datetime, date, timedelta, time
from zoneinfo import ZoneInfo
import os
from discord.ext import tasks



# IMPORTS DO SEU PROJETO (ajuste os paths conforme seu repo)
from db import get_conn, get_launches_by_period
from sheets_export import export_rows_to_month_sheet  # sua função atual


def month_range_prev(today: date):
    first_this_month = today.replace(day=1)
    last_prev = first_this_month - timedelta(days=1)
    start_prev = last_prev.replace(day=1)
    end_prev = last_prev
    return start_prev, end_prev


def list_user_ids():
    """
    Multiusuário sem config manual:
    - tenta tabela users primeiro
    - se não existir, pega distinct user_id de launches
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute("select id from users order by id asc")
                rows = cur.fetchall()
                if rows:
                    return [r["id"] for r in rows]
            except Exception:
                conn.rollback()

            cur.execute("select distinct user_id from launches order by user_id asc")
            rows = cur.fetchall()
            return [r["user_id"] for r in rows]


def _tz():
    return ZoneInfo(os.getenv("REPORT_TIMEZONE", "America/Cuiaba"))

def setup_monthly_export(bot):
    """
    Liga o scheduler 1x ao subir o bot.
    No dia 1, no horário configurado, exporta o mês anterior para TODOS os usuários encontrados no DB.
    """

    @tasks.loop(minutes=1)
    async def monthly_export_loop():
        tz = _tz()
        now = datetime.now(tz)

        hour = int(os.getenv("REPORT_HOUR", "9"))
        minute = int(os.getenv("REPORT_MINUTE", "0"))

        # roda só no minuto configurado
        if now.hour != hour or now.minute != minute:
            return

        # só no dia 1
        if now.day != 1:
            return

        start_d, end_d = month_range_prev(now.date())
        month_key = start_d.strftime("%Y-%m")  # ex: 2026-01

        user_ids = list_user_ids()
        if not user_ids:
            return

        start_dt = datetime.combine(start_d, time.min)
        end_dt = datetime.combine(end_d, time.max)

        for uid in user_ids:
            # busca os lançamentos do usuário no mês anterior
            rows = get_launches_by_period(uid, start_d, end_d)
            if not rows:
                continue

            tab_name = f"{month_key}__{uid}"

            export_rows_to_month_sheet(
                uid,
                rows,
                start_dt,
                end_dt,
                worksheet_name=tab_name
            )

        # opcional: avisar em algum canal
        channel_id = int(os.getenv("REPORT_CHANNEL_ID", "0"))
        if channel_id:
            ch = bot.get_channel(channel_id)
            if ch:
                await ch.send(
                    f"📈 Export mensal automático concluído para **{month_key}** "
                    f"(usuários: {len(user_ids)})."
                )

    @monthly_export_loop.before_loop
    async def before_loop():
        await bot.wait_until_ready()

    if not monthly_export_loop.is_running():
        monthly_export_loop.start()
