"""
adapters/discord/commands_embed.py — embed "O que pedir" pro Discord.

Diferente de `help_ui.help_embed` (que é tutor com tour interativo), este
embed lista o catálogo COMPLETO de tools do bot pra quem já sabe o básico
e quer explorar. Fonte única: `core.commands_catalog.CATALOG`.

Renderizado como um único embed com 9 fields (1 por categoria), formato
inline=False pra cada categoria ocupar a linha toda.
"""
from __future__ import annotations

import discord

from core.commands_catalog import CATALOG


def _format_category_value(cmds: list[dict]) -> str:
    """Formata os comandos de uma categoria pra Discord (markdown
    com bullets e itálico pras notas)."""
    lines = []
    for cmd in cmds:
        lines.append(f"• `{cmd['text']}`")
        if cmd.get("note"):
            lines.append(f"  _{cmd['note']}_")
    return "\n".join(lines)


def commands_embed() -> discord.Embed:
    embed = discord.Embed(
        title="💡 O que pedir ao Piggy",
        description=(
            "Aqui tá tudo que dá pra pedir, separado por tema.\n"
            "**Pergunta do jeito que sair na cabeça** — o bot entende "
            "português corrido e pede confirmação antes de mexer em "
            "qualquer coisa."
        ),
        color=discord.Color.from_rgb(167, 139, 250),  # purple
    )

    for cat in CATALOG:
        embed.add_field(
            name=f"{cat['emoji']} {cat['title']}",
            value=_format_category_value(cat["commands"]),
            inline=False,
        )

    embed.set_footer(text="Não viu o que queria? Pergunta mesmo assim.")
    return embed
