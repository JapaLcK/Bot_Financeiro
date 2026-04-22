"""
cogs/pockets_cog.py — Comandos de caixinhas (pockets).

Comandos tratados:
  - listar caixinhas / saldo caixinhas
  - criar caixinha <nome>
  - transferi/coloquei/aportei X na caixinha <nome>   (Conta → Caixinha)
  - retirei/saquei/resgatei X da caixinha <nome>      (Caixinha → Conta)
  - excluir/apagar caixinha <nome>                    (com confirmação)

Toda a lógica de negócio e acesso ao banco fica em core/handlers/pockets.py.
Este Cog só faz o parsing específico do Discord e delega.
"""
import re

import discord
from discord.ext import commands

from core.handlers import pockets as h_pockets
from utils_text import parse_money, parse_pocket_deposit_natural


class PocketsCog(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def handle(self, message: discord.Message, t: str, uid: int) -> bool:
        """Retorna True se este Cog tratou a mensagem."""

        # ── Listar / saldo caixinhas ──────────────────────────────────────────
        if t in ("listar caixinhas", "lista caixinhas", "saldo caixinhas", "caixinhas"):
            await message.reply(h_pockets.list_pockets(uid))
            return True

        # ── Criar caixinha ────────────────────────────────────────────────────
        if t.startswith("criar caixinha"):
            name = message.content.split("criar caixinha", 1)[1].strip()
            if not name:
                await message.reply("Qual o nome da caixinha? Ex: `criar caixinha viagem`")
                return True
            await message.reply(h_pockets.create(uid, name, nota=message.content))
            return True

        # ── Depositar na caixinha (palavras-chave explícitas) ─────────────────
        if "caixinha" in t and any(
            w in t for w in ("transferi", "transferir", "adicionar", "colocar",
                             "coloquei", "por", "depositar", "aporte", "aportei")
        ):
            amount = parse_money(message.content)
            if amount is None:
                await message.reply("Qual valor? Ex: `transferi 200 para caixinha viagem`")
                return True
            parts = t.split("caixinha", 1)
            name = re.sub(r'^(a|para|pra|na|no|da|do)\s+', '', parts[1].strip()).strip()
            if not name:
                await message.reply("Pra qual caixinha? Ex: `transferi 200 para caixinha viagem`")
                return True
            entities = {"pocket_name": name, "amount": amount}
            await message.reply(h_pockets.deposit(uid, message.content, entities))
            return True

        # ── Sacar da caixinha ─────────────────────────────────────────────────
        if any(w in t for w in ("retirei", "retirar", "sacar", "saquei", "resgatei", "resgatar")) \
                and "caixinha" in t:
            amount = parse_money(message.content)
            if amount is None:
                await message.reply("Qual valor? Ex: `retirei 200 da caixinha viagem`")
                return True
            parts = t.split("caixinha", 1)
            name = re.sub(r'^(da|do|de|na|no|para|pra)\s+', '', parts[1].strip()).strip()
            if not name:
                await message.reply("De qual caixinha? Ex: `retirei 200 da caixinha viagem`")
                return True
            entities = {"pocket_name": name, "amount": amount}
            await message.reply(h_pockets.withdraw(uid, message.content, entities))
            return True

        # ── Excluir caixinha ──────────────────────────────────────────────────
        if t.startswith(("excluir caixinha", "apagar caixinha", "remover caixinha")):
            parts = message.content.split("caixinha", 1)
            name = parts[1].strip() if len(parts) > 1 else ""
            if not name:
                await message.reply("Qual caixinha quer excluir? Ex: `excluir caixinha viagem`")
                return True
            await message.reply(h_pockets.propose_delete(uid, name))
            return True

        # ── Depósito natural ("coloquei 300 na emergencia") ───────────────────
        amount, pocket_name = parse_pocket_deposit_natural(message.content)
        if amount is not None and pocket_name:
            entities = {"pocket_name": pocket_name, "amount": amount}
            await message.reply(h_pockets.deposit(uid, message.content, entities))
            return True

        return False


async def setup(bot: commands.Bot):
    await bot.add_cog(PocketsCog(bot))
