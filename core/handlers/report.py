# core/handlers/report.py
from __future__ import annotations
import db
from core.reports.reports_daily import build_daily_report_text


def daily(user_id: int) -> str:
    return build_daily_report_text(user_id)


def enable(user_id: int) -> str:
    db.set_daily_report_enabled(user_id, True)
    return "✅ Report diário ligado. Você vai receber todo dia no horário configurado."


def disable(user_id: int) -> str:
    db.set_daily_report_enabled(user_id, False)
    return "✅ Report diário desligado. Para ligar de novo: *ligar report diario*"
