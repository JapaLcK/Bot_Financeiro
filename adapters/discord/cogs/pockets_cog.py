"""
cogs/pockets_cog.py — Comandos de caixinhas (pockets).

Comandos tratados:
  - listar caixinhas / saldo caixinhas
  - criar caixinha <nome>
  - transferi/coloquei/aportei X na caixinha <nome>   (Conta → Caixinha)
  - retirei/saquei/resgatei X da caixinha <nome>      (Caixinha → Conta)
  - excluir/apagar caixinha <nome>                    (com confirmação)
"""
import re

import discord
from discord.ext import commands

from db import (
    list_pockets,
    create_pocket,
    pocket_deposit_from_account,
    pocket_withdraw_to_account,
    delete_pocket,
    get_balance,
    set_pending_action,
)
from utils_text import fmt_brl, parse_money, parse_pocket_deposit_natural


class PocketsCog(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def handle(self, message: discord.Message, t: str, uid: int) -> bool:
        """Retorna True se este Cog tratou a mensagem."""

        # ── Listar / saldo caixinhas ──────────────────────────────────────────
        if t in ("listar caixinhas", "lista caixinhas", "saldo caixinhas", "caixinhas"):
            rows = list_pockets(uid)
            if not rows:
                await message.reply("Você ainda não tem caixinhas. Use: `criar caixinha <nome>`")
                return True

            total = sum(float(r["balance"]) for r in rows)
            linhas = [f"• **{r['name']}**: {fmt_brl(float(r['balance']))}" for r in rows]
            await message.reply(
                "📦 **Caixinhas:**\n"
                + "\n".join(linhas)
                + f"\n\nTotal nas caixinhas: **{fmt_brl(total)}**"
            )
            return True

        # ── Criar caixinha ────────────────────────────────────────────────────
        if t.startswith("criar caixinha"):
            name = message.content.split("criar caixinha", 1)[1].strip()
            if not name:
                await message.reply("Qual o nome da caixinha? Ex: `criar caixinha viagem`")
                return True
            try:
                launch_id, pocket_id, pocket_name = create_pocket(uid, name=name, nota=message.content)
            except Exception:
                await message.reply("Deu erro ao criar caixinha. Veja os logs.")
                return True

            if launch_id is None:
                await message.reply(f"ℹ️ A caixinha **{pocket_name}** já existe.")
            else:
                await message.reply(f"✅ Caixinha criada: **{pocket_name}** (ID: **#{launch_id}**)")
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
            return await self._depositar(message, uid, name, float(amount))

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
            return await self._sacar(message, uid, name, float(amount))

        # ── Excluir caixinha ──────────────────────────────────────────────────
        if t.startswith(("excluir caixinha", "apagar caixinha", "remover caixinha")):
            parts = message.content.split("caixinha", 1)
            name = parts[1].strip() if len(parts) > 1 else ""
            if not name:
                await message.reply("Qual caixinha quer excluir? Ex: `excluir caixinha viagem`")
                return True

            rows = list_pockets(uid)
            pocket = next((r for r in rows if r["name"].lower() == name.lower()), None)
            if not pocket:
                await message.reply(f"Não achei essa caixinha: **{name}**")
                return True

            canon_name = pocket["name"]
            saldo = float(pocket["balance"])
            if saldo != 0.0:
                await message.reply(
                    f"⚠️ Não posso excluir a caixinha **{canon_name}** "
                    f"porque o saldo não é zero ({fmt_brl(saldo)}).\n"
                    f"Retire o valor antes e tente novamente."
                )
                return True

            set_pending_action(uid, "delete_pocket", {"pocket_name": canon_name}, minutes=10)
            await message.reply(
                f"⚠️ Você está prestes a excluir esta caixinha:\n"
                f"• **{canon_name}** • saldo: **{fmt_brl(0.0)}**\n\n"
                f"Responda **sim** para confirmar ou **não** para cancelar. (expira em 10 min)"
            )
            return True

        # ── Depósito natural ("coloquei 300 na emergencia") ───────────────────
        amount, pocket_name = parse_pocket_deposit_natural(message.content)
        if amount is not None and pocket_name:
            return await self._depositar(message, uid, pocket_name, float(amount))

        return False

    # ── Helpers ──────────────────────────────────────────────────────────────

    async def _depositar(self, message: discord.Message, uid: int, name: str, amount: float) -> bool:
        try:
            launch_id, new_acc, new_pocket, canon = pocket_deposit_from_account(
                uid, pocket_name=name, amount=amount, nota=message.content
            )
        except LookupError:
            await message.reply(f"Não achei essa caixinha: **{name}**. Use: `criar caixinha {name}`")
            return True
        except ValueError as e:
            if str(e) == "INSUFFICIENT_ACCOUNT":
                bal = get_balance(uid)
                await message.reply(f"Saldo insuficiente na conta. Conta: {fmt_brl(float(bal))}")
            else:
                await message.reply("Valor inválido.")
            return True
        except Exception:
            await message.reply("Deu erro ao depositar na caixinha. Veja os logs.")
            return True

        await message.reply(
            f"✅ Depósito na caixinha **{canon}**: +{fmt_brl(amount)}\n"
            f"🏦 Conta: {fmt_brl(float(new_acc))} • 📦 Caixinha: {fmt_brl(float(new_pocket))}\n"
            f"ID: **#{launch_id}**"
        )
        return True

    async def _sacar(self, message: discord.Message, uid: int, name: str, amount: float) -> bool:
        try:
            launch_id, new_acc, new_pocket, canon = pocket_withdraw_to_account(
                uid, pocket_name=name, amount=amount, nota=None
            )
        except LookupError:
            await message.reply(f"Não achei essa caixinha: **{name}**. Use: `criar caixinha {name}`")
            return True
        except ValueError as e:
            if str(e) == "INSUFFICIENT_POCKET":
                await message.reply(f"Saldo insuficiente na caixinha **{name}**.")
            else:
                await message.reply("Valor inválido.")
            return True
        except Exception:
            await message.reply("Deu erro ao sacar da caixinha. Veja os logs.")
            return True

        await message.reply(
            f"📤 Caixinha **{canon}**: -{fmt_brl(amount)}\n"
            f"🏦 Conta: {fmt_brl(float(new_acc))} • 📦 Caixinha: {fmt_brl(float(new_pocket))}\n"
            f"ID: #{launch_id}"
        )
        return True


async def setup(bot: commands.Bot):
    await bot.add_cog(PocketsCog(bot))
