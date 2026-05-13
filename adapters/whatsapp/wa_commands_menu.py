# adapters/whatsapp/wa_commands_menu.py
"""
Menu interativo "O que pedir" via WhatsApp Cloud API.

Diferente do `wa_help_menu` (que é tutor pra quem está aprendendo o bot),
este menu é catálogo completo de tools pra quem já sabe o básico e quer
explorar. Source-of-truth em `core.commands_catalog.CATALOG`.

Fluxo:
1. User manda "comandos" → `send_commands_menu(wa_id)` envia interactive
   list com 9 categorias.
2. User seleciona uma categoria → `send_commands_section(wa_id, cat_id)`
   envia o texto formatado da categoria + botão "Ver outras" pra reabrir
   o menu.

IDs gerenciados aqui: prefixo `cmds_` (ver `commands_catalog.CATEGORY_IDS`).
"""
from __future__ import annotations

import logging

from adapters.whatsapp.wa_client import (
    send_interactive_buttons,
    send_interactive_list,
    send_text,
)
from core.commands_catalog import (
    CATALOG,
    CATEGORY_IDS,
    get_category,
    render_category_body,
    render_category_full,
)

logger = logging.getLogger(__name__)


COMMANDS_MENU_IDS: set[str] = CATEGORY_IDS | {"cmds_menu"}


def get_commands_menu_id(raw: dict) -> str | None:
    """Retorna o ID se for clique nesse menu, senão None.

    Funciona tanto pra list_reply (categoria selecionada) quanto pra
    button_reply (botão "Ver outras" que reabre o menu).
    """
    if raw.get("type") != "interactive":
        return None
    inter = raw.get("interactive") or {}

    lr = inter.get("list_reply") or {}
    lid = lr.get("id") or ""
    if lid in COMMANDS_MENU_IDS:
        return lid

    br = inter.get("button_reply") or {}
    bid = br.get("id") or ""
    if bid in COMMANDS_MENU_IDS:
        return bid

    return None


def send_commands_menu(wa_id: str) -> None:
    """Envia o menu de "O que pedir" com todas as categorias.

    Limite do WhatsApp: máx 10 rows totais distribuídos em sections.
    Hoje são 9 categorias, divididas em 2 sections pra visual.
    """
    # Primeiras 5 categorias = "Dinheiro do dia a dia"
    # Resto = "Patrimônio e análise"
    daily_ids = {"cmds_plano", "cmds_saldo", "cmds_cartao", "cmds_parcel", "cmds_orca"}
    daily_rows = []
    other_rows = []
    for cat in CATALOG:
        row = {
            "id": cat["id"],
            "title": f"{cat['emoji']} {cat['title']}"[:24],
            "description": cat["description"][:72],
        }
        if cat["id"] in daily_ids:
            daily_rows.append(row)
        else:
            other_rows.append(row)

    send_interactive_list(
        to=wa_id,
        header="💡 O que pedir ao Piggy",
        body=(
            "Aqui tá tudo que dá pra pedir, dividido por tema.\n"
            "Toca numa opção pra ver os exemplos."
        ),
        button_label="💡 Ver temas",
        footer="Pergunta do jeito que sair na cabeça",
        sections=[
            {"title": "Dinheiro do dia a dia", "rows": daily_rows},
            {"title": "Patrimônio e análise", "rows": other_rows},
        ],
    )


def send_commands_section(wa_id: str, cat_id: str) -> None:
    """Envia o conteúdo de uma categoria + botão pra reabrir o menu."""
    if cat_id == "cmds_menu":
        send_commands_menu(wa_id)
        return

    cat = get_category(cat_id)
    if not cat:
        logger.warning("Commands menu: ID desconhecido=%s", cat_id)
        send_commands_menu(wa_id)
        return

    # `body` SEM título — o header do interactive_buttons já mostra
    # "{emoji} {title}". Senão fica duplicado visualmente.
    body_text = render_category_body(cat)

    # WhatsApp limita o body de interactive_buttons a ~1024 chars. Se passar,
    # manda como texto puro primeiro (com cabeçalho próprio) e o botão
    # "Ver outras" em mensagem separada.
    if len(body_text) > 900:
        send_text(to=wa_id, body=render_category_full(cat))
        send_interactive_buttons(
            to=wa_id,
            body="Quer explorar outro tema?",
            buttons=[{"id": "cmds_menu", "title": "💡 Ver outros temas"}],
        )
        return

    send_interactive_buttons(
        to=wa_id,
        header=f"{cat['emoji']} {cat['title']}",
        body=body_text,
        footer="Toca pra copiar um exemplo no chat",
        buttons=[{"id": "cmds_menu", "title": "💡 Ver outros temas"}],
    )
