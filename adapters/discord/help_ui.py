import discord

HELP_SECTIONS = {
    "start": (
        "👋 **Comece aqui**\n"
        "• `tutorial` → guia rápido\n"
        "• `saldo`\n"
        "• `gastei 50 mercado`\n"
        "• `recebi 1000 salario`\n"
        "• `importar ofx` + anexo\n"
    ),
    "tutorial": (
        "🚀 **Tutorial rápido (1–2 min)**\n\n"
        "1) **Registre o básico**\n"
        "• `recebi 1000 salario`\n"
        "• `gastei 50 mercado`\n"
        "• `saldo`\n\n"
        "2) **Importe seu extrato (OFX)**\n"
        "• Envie `importar ofx` + anexo `.ofx`\n"
        "• Duplicadas são ignoradas\n\n"
        "3) **Caixinhas**\n"
        "• `criar caixinha viagem`\n"
        "• `coloquei 300 na caixinha viagem`\n\n"
        "4) **Investimentos**\n"
        "• `criar investimento CDB 110% CDI`\n"
        "• `apliquei 200 no investimento CDB`\n\n"
        "5) **Sheets**\n"
        "• `exportar sheets`\n"
    ),
    "ofx": (
        "🧾 **Importar extrato (OFX)**\n"
        "• Envie: `importar ofx` + anexo `.ofx`\n"
        "• Pode importar de novo — duplicadas são ignoradas\n"
        "• O saldo final vem do `LEDGERBAL` do OFX\n"
    ),
    "cc": (
        "🏦 **Conta corrente**\n"
        "• `saldo`\n"
        "• `listar lançamentos`\n"
        "• `apagar 3`\n"
        "• `desfazer`\n"
    ),
    "pockets": (
        "📦 **Caixinhas**\n"
        "• `criar caixinha viagem`\n"
        "• `coloquei 300 na caixinha viagem`\n"
        "• `retirei 100 da caixinha viagem`\n"
        "• `saldo caixinhas`\n"
        "• `listar caixinhas`\n"
        "• `excluir caixinha viagem`\n"
    ),
    "invest": (
        "📈 **Investimentos**\n"
        "• `criar investimento CDB Nubank 1% ao mês`\n"
        "• `criar investimento Tesouro 0,03% ao dia`\n"
        "• `criar investimento CDB 110% CDI`\n"
        "• `apliquei 200 no investimento CDB Nubank`\n"
        "• `retirei 100 do investimento CDB Nubank`\n"
        "• `saldo investimentos`\n"
        "• `listar investimentos`\n"
        "• `excluir investimento CDB Nubank`\n"
    ),
    "cdi": (
        "📊 **CDI**\n"
        "• `ver cdi`\n"
    ),
    "sheets": (
        "📤 **Exportar para Google Sheets**\n"
        "• `exportar sheets`\n"
        "• `exportar sheets 2026-02-01 2026-02-28`\n"
        "• Datas: use `YYYY-MM-DD`\n"
    ),
    "launches": (
        "🧾 **Lançamentos (histórico)**\n"
        "• `listar lançamentos`\n"
        "• `apagar 3`\n"
        "• `desfazer`\n"
    ),
    "confirm": (
        "⚠️ **Confirmações**\n"
        "• `sim` → confirma ações pendentes\n"
        "• `nao` → cancela\n"
    ),
}

def help_embed(section_key: str = "start") -> discord.Embed:
    txt = HELP_SECTIONS.get(section_key, HELP_SECTIONS["start"])
    title_map = {
        "start": "Ajuda — Bot Financeiro",
        "tutorial": "Tutorial",
        "ofx": "OFX",
        "cc": "Conta corrente",
        "pockets": "Caixinhas",
        "invest": "Investimentos",
        "cdi": "CDI",
        "sheets": "Google Sheets",
        "launches": "Lançamentos",
        "confirm": "Confirmações",
    }
    emb = discord.Embed(
        title=title_map.get(section_key, "Ajuda"),
        description=txt,
    )
    emb.set_footer(text="Selecione um tópico no menu abaixo.")
    return emb

class HelpSelect(discord.ui.Select):
    def __init__(self, author_id: int):
        self.author_id = author_id
        options = [
            discord.SelectOption(label="Começar (visão geral)", value="start", emoji="👋"),
            discord.SelectOption(label="Tutorial", value="tutorial", emoji="🚀"),
            discord.SelectOption(label="OFX (importar extrato)", value="ofx", emoji="🧾"),
            discord.SelectOption(label="Conta corrente", value="cc", emoji="🏦"),
            discord.SelectOption(label="Caixinhas", value="pockets", emoji="📦"),
            discord.SelectOption(label="Investimentos", value="invest", emoji="📈"),
            discord.SelectOption(label="CDI", value="cdi", emoji="📊"),
            discord.SelectOption(label="Exportar Sheets", value="sheets", emoji="📤"),
            discord.SelectOption(label="Lançamentos", value="launches", emoji="🧾"),
            discord.SelectOption(label="Confirmações", value="confirm", emoji="⚠️"),
        ]
        super().__init__(
            placeholder="Escolha um tópico…",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        # trava para só o autor poder clicar
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Só quem pediu `ajuda` pode usar esse menu 🙂",
                ephemeral=True,
            )
            return

        section = self.values[0]
        await interaction.response.edit_message(embed=help_embed(section), view=self.view)

class HelpView(discord.ui.View):
    def __init__(self, author_id: int, timeout: float = 180):
        super().__init__(timeout=timeout)
        self.add_item(HelpSelect(author_id))

    async def on_timeout(self):
        # quando expira, remove interações (evita cliques velhos)
        for item in self.children:
            item.disabled = True