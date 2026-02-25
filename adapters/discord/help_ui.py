# adapters/discord/help_ui.py
import discord
from core.help_text import HELP_ORDER, TITLE_MAP, render_section

def help_embed(section_key: str = "start") -> discord.Embed:
    txt = render_section(section_key)
    emb = discord.Embed(
        title=TITLE_MAP.get(section_key, "Ajuda"),
        description=txt,
    )
    emb.set_footer(text="Selecione um tópico no menu abaixo.")
    return emb

class HelpSelect(discord.ui.Select):
    def __init__(self, author_id: int):
        self.author_id = author_id
        options = [
            discord.SelectOption(label=label, value=key, emoji=emoji)
            for (key, label, emoji) in HELP_ORDER
        ]
        super().__init__(
            placeholder="Escolha um tópico…",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Só quem pediu `ajuda` pode usar esse menu 🙂",
                ephemeral=True,
            )
            return

        section = self.values[0]
        await interaction.response.edit_message(
            embed=help_embed(section),
            view=self.view,
        )

class HelpView(discord.ui.View):
    def __init__(self, author_id: int, timeout: float = 180):
        super().__init__(timeout=timeout)
        self.add_item(HelpSelect(author_id))

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True