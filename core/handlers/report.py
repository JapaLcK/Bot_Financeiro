# core/handlers/report.py
from __future__ import annotations
import db
from core.reports.reports_daily import build_daily_report_text


def daily(user_id: int) -> str:
    return build_daily_report_text(user_id)


def enable(user_id: int) -> str:
    db.set_daily_report_enabled(user_id, True)
    prefs = db.get_daily_report_prefs(user_id)
    h, m = prefs.get("hour", 9), prefs.get("minute", 0)
    return (
        f"✅ Report diário ligado. Você vai receber todo dia às *{h:02d}h{m:02d}*.\n"
        f"Para mudar o horário: *ligar report diario 20h*"
    )


def set_hour(user_id: int, hour: int, minute: int = 0) -> str:
    if not (0 <= hour <= 23) or not (0 <= minute <= 59):
        return "⚠️ Horário inválido. Use um formato como *20h* ou *8h30*."
    db.set_daily_report_hour(user_id, hour, minute)
    suffix = f"{minute:02d}" if minute else ""
    hora_fmt = f"{hour}h{suffix}" if suffix else f"{hour}h"
    return (
        f"✅ Report diário ligado para todos os dias às *{hora_fmt}*.\n"
        f"Para desligar: *desligar report diario*"
    )


def disable(user_id: int) -> str:
    db.set_daily_report_enabled(user_id, False)
    return "✅ Report diário desligado. Para ligar de novo: *ligar report diario*"
