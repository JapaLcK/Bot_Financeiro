from __future__ import annotations

import re
import unicodedata

from core.dashboard_links import build_dashboard_link


def _normalize(text: str) -> str:
    value = (text or "").strip().lower()
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _open_finance_dashboard_link(user_id: int) -> str:
    link = build_dashboard_link(user_id, view="open-finance")
    if not link:
        return "⚠️ Não consegui gerar o link do Open Finance agora. Tente novamente em instantes."
    return (
        "A conexão com Open Finance é feita nas configurações seguras do PigBank, não pelo WhatsApp.\n\n"
        "Abra o link abaixo para conectar ou gerenciar seus bancos:\n"
        f"{link}"
    )


def handle_open_finance_whatsapp_command(user_id: int, text: str) -> str | None:
    """
    Processa comandos de Open Finance no WhatsApp.
    O WhatsApp nunca cria/remove conexão: ele só entrega o link para a tela web.
    """
    text_norm = _normalize(text)
    if not text_norm:
        return None

    commands = (
        "conectar openfinance",
        "conectar open finance",
        "conectar banco",
        "conectar pluggy",
        "sincronizar openfinance",
        "sincronizar open finance",
        "sincronizar banco",
        "atualizar openfinance",
        "atualizar open finance",
        "desconectar openfinance",
        "desconectar open finance",
        "remover openfinance",
        "remover open finance",
        "desconectar banco",
    )
    exact_commands = {
        "openfinance",
        "open finance",
        "pluggy",
        "bancos conectados",
        "minhas conexoes",
        "minhas conexoes openfinance",
        "minhas conexoes open finance",
    }

    if text_norm in exact_commands or any(text_norm.startswith(command) for command in commands):
        return _open_finance_dashboard_link(user_id)

    return None
