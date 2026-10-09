# core/handlers/report.py
from __future__ import annotations
import re
import db
from core.reports.reports_daily import (
    build_daily_report_text,
    build_weekly_report_text,
    build_monthly_report_text,
)


def daily(user_id: int) -> str:
    return build_daily_report_text(user_id)


def weekly(user_id: int) -> str:
    return build_weekly_report_text(user_id)


def monthly(user_id: int) -> str:
    return build_monthly_report_text(user_id)


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


# --- resumo semanal ---

# Dia pedido que NÃO é segunda: o resumo semanal só sai na segunda-feira, e o usuário
# tem que saber disso na hora (não pode ficar em silêncio).
_DIA_NAO_SEGUNDA = re.compile(r"\b(domingo|s[áa]bado|ter[çc]a|quarta|quinta|sexta)s?\b", re.IGNORECASE)


def enable_weekly(user_id: int, texto: str = "") -> str:
    from core.services.plan_service import plan_gate_ok
    if not plan_gate_ok(user_id, "weekly_report"):
        return "🐷 O resumo semanal automático está disponível nos planos Plus e Pro."
    db.set_weekly_report_enabled(user_id, True)
    aviso = ""
    dia = _DIA_NAO_SEGUNDA.search(texto or "")
    if dia:
        aviso = (f"⚠️ Você pediu {dia.group(1)}, mas o resumo semanal sai só na segunda-feira.\n")
    return (
        aviso
        + "✅ Resumo semanal ligado. Você recebe toda segunda-feira, referente à semana anterior.\n"
        + "Para desligar: *desligar resumo semanal*"
    )


def disable_weekly(user_id: int) -> str:
    db.set_weekly_report_enabled(user_id, False)
    return "✅ Resumo semanal desligado. Para ligar de novo: *ligar resumo semanal*"


# --- resumo mensal ---

def enable_monthly(user_id: int) -> str:
    db.set_monthly_report_enabled(user_id, True)
    return (
        "✅ Resumo mensal ligado. Você recebe todo dia 1º, referente ao mês anterior.\n"
        "Para desligar: *desligar resumo mensal*"
    )


def disable_monthly(user_id: int) -> str:
    db.set_monthly_report_enabled(user_id, False)
    return "✅ Resumo mensal desligado. Para ligar de novo: *ligar resumo mensal*"
