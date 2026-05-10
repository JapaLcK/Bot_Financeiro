"""
cogs/billing_cog.py — Comandos de assinatura PigBank+.

Comandos tratados:
  - assinar / fazer upgrade / quero pro / etc.
  - cancelar / cancelar assinatura / encerrar
  - plano / meu plano / minha assinatura
"""

import discord
from discord.ext import commands

from core.services.billing_commands import handle_billing_command


class BillingCog(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def handle(self, message: discord.Message, t: str, uid: int) -> bool:
        """Retorna True se este Cog tratou a mensagem."""
        reply = handle_billing_command(uid, t, platform="discord")
        if reply is None:
            return False
        await message.reply(reply)
        return True


async def setup(bot: commands.Bot):
    await bot.add_cog(BillingCog(bot))
