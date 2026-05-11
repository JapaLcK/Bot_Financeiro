"""
core/services/ai_chat/tools/reports.py — tools de relatório diário.

Read:
  - get_daily_report_prefs: estado atual (ativado? que horas?)

Write (precisam de confirmação humana):
  - enable_daily_report:  liga o envio diário (opcionalmente seta a hora)
  - disable_daily_report: desliga
  - set_daily_report_hour: muda o horário (também garante que esteja ligado)
"""
from __future__ import annotations

from typing import Any

import db

from ._base import Tool


# ─── Read ───────────────────────────────────────────────────────────────────

def _get_daily_report_prefs(user_id: int, args: dict[str, Any]) -> dict[str, Any]:
    prefs = db.get_daily_report_prefs(user_id) or {}
    return {
        "enabled": bool(prefs.get("enabled")),
        "hour": prefs.get("hour"),
        "minute": prefs.get("minute"),
    }


# ─── Write helpers ──────────────────────────────────────────────────────────

def _validate_hour(args: dict[str, Any]) -> tuple[int | None, int | None, str | None]:
    """Retorna (hour, minute, error_msg)."""
    raw_hour = args.get("hour")
    raw_minute = args.get("minute")
    if raw_hour is None:
        return None, None, None
    try:
        hour = int(raw_hour)
        minute = int(raw_minute) if raw_minute is not None else 0
    except (TypeError, ValueError):
        return None, None, "hora/minuto inválidos"
    if not (0 <= hour <= 23) or not (0 <= minute <= 59):
        return None, None, "hora deve estar entre 00:00 e 23:59"
    return hour, minute, None


# ─── Write: enable_daily_report ─────────────────────────────────────────────

def _enable_daily_report_summary(args: dict[str, Any]) -> str:
    h = args.get("hour")
    m = args.get("minute") or 0
    if isinstance(h, int):
        return f"ligar o relatório diário às {h:02d}:{int(m):02d}"
    return "ligar o relatório diário"


def _enable_daily_report_execute(user_id: int, args: dict[str, Any]) -> str:
    hour, minute, err = _validate_hour(args)
    if err:
        return f"🐷 {err}."
    try:
        db.set_daily_report_enabled(user_id, True)
        if hour is not None:
            db.set_daily_report_hour(user_id, hour, minute or 0)
            return f"✅ Relatório diário ligado às {hour:02d}:{(minute or 0):02d}."
        return "✅ Relatório diário ligado."
    except Exception as e:
        return f"🐷 Não consegui ligar: {e}"


# ─── Write: disable_daily_report ────────────────────────────────────────────

def _disable_daily_report_summary(args: dict[str, Any]) -> str:
    return "desligar o relatório diário"


def _disable_daily_report_execute(user_id: int, args: dict[str, Any]) -> str:
    try:
        db.set_daily_report_enabled(user_id, False)
        return "✅ Relatório diário desligado."
    except Exception as e:
        return f"🐷 Não consegui desligar: {e}"


# ─── Write: set_daily_report_hour ───────────────────────────────────────────

def _set_daily_report_hour_summary(args: dict[str, Any]) -> str:
    h = int(args.get("hour") or 0)
    m = int(args.get("minute") or 0)
    return f"agendar o relatório diário pras {h:02d}:{m:02d}"


def _set_daily_report_hour_execute(user_id: int, args: dict[str, Any]) -> str:
    hour, minute, err = _validate_hour(args)
    if err or hour is None:
        return "🐷 Informa um horário válido (0–23h)."
    try:
        db.set_daily_report_hour(user_id, hour, minute or 0)
        return f"✅ Relatório diário agendado pras {hour:02d}:{(minute or 0):02d}."
    except Exception as e:
        return f"🐷 Não consegui agendar: {e}"


# ─── Tools registry ─────────────────────────────────────────────────────────

TOOLS: list[Tool] = [
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "get_daily_report_prefs",
                "description": "Mostra se o relatório diário está ligado e em que horário. Use pra 'tô recebendo relatório diário?', 'que hora chega meu relatório?'.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        is_write=False,
        execute=_get_daily_report_prefs,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "enable_daily_report",
                "description": "Liga o envio diário do resumo financeiro. Opcionalmente recebe hour/minute pra setar o horário. ESCRITA — pede confirmação.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "hour": {"type": "integer", "minimum": 0, "maximum": 23, "description": "Hora (0–23)."},
                        "minute": {"type": "integer", "minimum": 0, "maximum": 59, "default": 0},
                    },
                },
            },
        },
        is_write=True,
        summary=_enable_daily_report_summary,
        execute=_enable_daily_report_execute,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "disable_daily_report",
                "description": "Desliga o envio diário do resumo financeiro. ESCRITA — pede confirmação.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        is_write=True,
        summary=_disable_daily_report_summary,
        execute=_disable_daily_report_execute,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "set_daily_report_hour",
                "description": "Define o horário do relatório diário (também garante que esteja ligado). ESCRITA — pede confirmação.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "hour": {"type": "integer", "minimum": 0, "maximum": 23},
                        "minute": {"type": "integer", "minimum": 0, "maximum": 59, "default": 0},
                    },
                    "required": ["hour"],
                },
            },
        },
        is_write=True,
        summary=_set_daily_report_hour_summary,
        execute=_set_daily_report_hour_execute,
    ),
]
