# core/handlers/dashboard.py
from __future__ import annotations
from core.dashboard_links import build_dashboard_link


def open_dashboard(user_id: int) -> str:
    link = build_dashboard_link(user_id, hours=5 / 60)
    if not link:
        return "⚠️ Não consegui gerar seu link do dashboard agora. Tente novamente em instantes."
    return f"📊 Dashboard financeiro:\n{link}\n\n⏱️ Este link expira em 5 minutos e funciona uma única vez."
