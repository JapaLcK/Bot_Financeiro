# adapters/whatsapp/wa_help_menu.py
"""
Menu de ajuda interativo via WhatsApp Cloud API.

Envia uma mensagem de lista (interactive list) com todos os tópicos
do bot. Quando o usuário seleciona um tópico, responde com o conteúdo
daquele tópico em uma mensagem de botões, com atalho para voltar ao menu
ou iniciar o tutorial.

IDs de itens de lista gerenciados aqui:
  help_cc, help_pockets, help_invest, help_ofx,
  help_categories, help_dashboard, help_launches,
  help_tutorial, help_menu
"""
from __future__ import annotations

import logging

from adapters.whatsapp.wa_client import (
    send_interactive_buttons,
    send_interactive_list,
)
from core.help_text import HELP_SECTIONS, _to_whatsapp_md

logger = logging.getLogger(__name__)

# ── IDs do menu de ajuda ─────────────────────────────────────────────────────

HELP_MENU_IDS: set[str] = {
    "help_cc", "help_pockets", "help_invest",
    "help_ofx", "help_categories", "help_dashboard",
    "help_launches", "help_tutorial", "help_menu",
}


def get_help_menu_id(raw: dict) -> str | None:
    """
    Retorna o ID do item de lista selecionado se for do menu de ajuda,
    senão None. Funciona tanto para list_reply quanto para button_reply.
    """
    if raw.get("type") != "interactive":
        return None
    inter = raw.get("interactive") or {}

    # list_reply
    lr = inter.get("list_reply") or {}
    lid = lr.get("id") or ""
    if lid in HELP_MENU_IDS:
        return lid

    # button_reply (ex: botão "📋 Menu" dentro de seções)
    br = inter.get("button_reply") or {}
    bid = br.get("id") or ""
    if bid in HELP_MENU_IDS:
        return bid

    return None


# ── Envio do menu principal ──────────────────────────────────────────────────

def send_help_menu(wa_id: str) -> None:
    """
    Envia o menu de ajuda interativo com todos os tópicos do bot.
    """
    send_interactive_list(
        to=wa_id,
        header="📋 Menu de Ajuda — PigBank AI",
        body=(
            "Selecione um tópico para ver os comandos disponíveis.\n\n"
            "Você também pode digitar diretamente, ex:\n"
            "*gastei 50 mercado* ou *saldo* 💡"
        ),
        button_label="📋 Ver tópicos",
        footer="Digite 'tutorial' para o guia completo",
        sections=[
            {
                "title": "💰 Financeiro básico",
                "rows": [
                    {
                        "id": "help_cc",
                        "title": "Saldo e lançamentos",
                        "description": "Registrar, listar e apagar transações",
                    },
                    {
                        "id": "help_pockets",
                        "title": "Caixinhas",
                        "description": "Poupar para objetivos específicos",
                    },
                    {
                        "id": "help_invest",
                        "title": "Investimentos",
                        "description": "Aplicações com rendimento automático",
                    },
                ],
            },
            {
                "title": "🔧 Recursos avançados",
                "rows": [
                    {
                        "id": "help_ofx",
                        "title": "Extrato OFX",
                        "description": "Importar extratos bancários (.ofx)",
                    },
                    {
                        "id": "help_categories",
                        "title": "Categorias e regras",
                        "description": "Auto-categorizar seus lançamentos",
                    },
                    {
                        "id": "help_launches",
                        "title": "Histórico detalhado",
                        "description": "Filtrar, desfazer e apagar registros",
                    },
                    {
                        "id": "help_dashboard",
                        "title": "Dashboard visual",
                        "description": "Painel interativo em tempo real",
                    },
                ],
            },
            {
                "title": "🚀 Começar",
                "rows": [
                    {
                        "id": "help_tutorial",
                        "title": "Tour interativo",
                        "description": "Tour guiado de 2 min pelo bot",
                    },
                ],
            },
        ],
    )


# ── Mapa de tópicos → conteúdo ───────────────────────────────────────────────

# Mapeamento de ID do menu → (key do HELP_SECTIONS, emoji, título amigável)
_TOPIC_MAP: dict[str, tuple[str, str, str]] = {
    "help_cc":         ("cc",         "🏦", "Conta Corrente"),
    "help_pockets":    ("pockets",    "📦", "Caixinhas"),
    "help_invest":     ("invest",     "📈", "Investimentos"),
    "help_ofx":        ("ofx",        "🧾", "Extrato OFX"),
    "help_categories": ("categories", "🏷️", "Categorias e Regras"),
    "help_dashboard":  ("dashboard",  "🖥️", "Dashboard"),
    "help_launches":   ("launches",   "📋", "Histórico de Lançamentos"),
}


def send_help_section(wa_id: str, menu_id: str) -> None:
    """
    Envia o conteúdo de um tópico de ajuda com botões para navegar.
    """
    # Caso especial: re-abrir o menu
    if menu_id == "help_menu":
        send_help_menu(wa_id)
        return

    # Caso especial: iniciar tutorial
    if menu_id == "help_tutorial":
        from adapters.whatsapp.wa_tutorial import send_welcome
        send_welcome(wa_id)
        return

    topic = _TOPIC_MAP.get(menu_id)
    if not topic:
        logger.warning("Help menu: ID desconhecido=%s", menu_id)
        send_help_menu(wa_id)
        return

    section_key, emoji, title = topic
    raw_text = HELP_SECTIONS.get(section_key, "")
    # Converte markdown Discord → WhatsApp
    body_text = _to_whatsapp_md(raw_text)

    send_interactive_buttons(
        to=wa_id,
        header=f"{emoji} {title}",
        body=body_text,
        footer="Selecione uma opção abaixo",
        buttons=[
            {"id": "help_menu",   "title": "📋 Voltar ao menu"},
            {"id": "tut_start",   "title": "🚀 Ver tutorial"},
        ],
    )
