"""
cogs/investments_cog.py — Comandos de investimentos.

Comandos tratados:
  - saldo investimentos
  - listar investimentos / meus investimentos
  - criar investimento <nome> <taxa>% ao dia|mês|ano
  - criar investimento <nome> <pct>% CDI
  - apliquei/aportei X no investimento <nome>          (Conta → Investimento)
  - resgatei/retirei/saquei X do investimento <nome>   (Investimento → Conta)
  - excluir investimento <nome>                        (com confirmação)
"""
import re

import discord
from discord.ext import commands

from investment_parse import parse_initial_amount, parse_investment_spec
from db import (
    list_investments,
    accrue_all_investments,
    create_investment_db,
    investment_deposit_from_account,
    investment_withdraw_to_account,
    delete_investment,
    list_investments,
    set_pending_action,
    get_balance,
)
from utils_text import fmt_brl, fmt_rate, parse_money


class InvestmentsCog(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def handle(self, message: discord.Message, t: str, uid: int) -> bool:
        """Retorna True se este Cog tratou a mensagem."""

        # ── Saldo investimentos ───────────────────────────────────────────────
        if t == "saldo investimentos":
            rows = accrue_all_investments(uid)
            if not rows:
                await message.reply(
                    "Você não tem investimentos ainda. Use: `criar investimento CDB 1,1% ao mês`"
                )
                return True
            lines = "\n".join(f"- **{r['name']}**: {fmt_brl(float(r['balance']))}" for r in rows)
            await message.reply("📈 **Investimentos:**\n" + lines)
            return True

        # ── Listar investimentos ──────────────────────────────────────────────
        if t in ("listar investimentos", "lista investimentos", "investimentos", "meus investimentos"):
            rows = accrue_all_investments(uid)
            if not rows:
                await message.reply("Você ainda não tem investimentos.")
                return True

            lines = ["📈 **Seus investimentos:**"]
            for r in rows:
                period_str = fmt_rate(r.get("rate"), r.get("period"))
                asset = r.get("asset_type") or "CDB"
                lines.append(
                    f"• **{r['name']}** [{asset}] — {period_str} — saldo: {fmt_brl(float(r['balance']))}"
                )
            await message.reply("\n".join(lines))
            return True

        # ── Criar investimento ────────────────────────────────────────────────
        if t.startswith("criar investimento"):
            return await self._criar_investimento(message, uid)

        # ── Excluir investimento ──────────────────────────────────────────────
        if t.startswith(("excluir investimento", "apagar investimento", "remover investimento")):
            return await self._excluir_investimento(message, uid, t)

        # ── Aporte (Conta → Investimento) ─────────────────────────────────────
        if any(w in t for w in ("apliquei", "aplicar", "aportei", "aporte")):
            return await self._aportar(message, uid, t)

        # ── Resgate (Investimento → Conta) ────────────────────────────────────
        if any(w in t for w in ("resgatei", "resgatar", "resgate", "retirei", "retirar", "saquei", "sacar")) \
                and "investimento" in t:
            return await self._resgatar(message, uid, t)

        return False

    # ── Criar ────────────────────────────────────────────────────────────────

    async def _criar_investimento(self, message: discord.Message, uid: int) -> bool:
        rest = message.content[len("criar investimento"):].strip()
        if not rest:
            await message.reply(
                "Use: `criar investimento <nome> <taxa>% ao dia|ao mês|ao ano` "
                "ou `criar investimento <nome> <pct>% cdi`"
            )
            return True

        spec = parse_investment_spec(rest)
        if not spec:
            await message.reply(
                "Não entendi a taxa/período. Exemplos:\n"
                "• `criar investimento CDB Banco 110% CDI`\n"
                "• `criar investimento CDB Banco CDI + 2,5% a.a.`\n"
                "• `criar investimento Tesouro IPCA+ 2029 IPCA + 7,43% a.a.`\n"
                "• `criar investimento Tesouro Prefixado 13,59% a.a.`"
            )
            return True

        rate = spec["rate"]
        period = spec["period"]
        name = spec["name"]
        if not name:
            await message.reply("Me diga o nome do investimento também. Ex: `criar investimento CDB 1% ao mês`")
            return True

        try:
            initial_amount = parse_initial_amount(message.content)
            kwargs = {
                "nota": message.content,
                "asset_type": spec.get("asset_type"),
                "indexer": spec.get("indexer"),
                "tax_profile": spec.get("tax_profile"),
            }
            if initial_amount is not None:
                kwargs["initial_amount"] = initial_amount
            launch_id, inv_id, canon = create_investment_db(
                uid,
                name=name,
                rate=rate,
                period=period,
                **kwargs,
            )
        except Exception as e:
            print("ERRO criar investimento:", repr(e))
            await message.reply("Deu erro ao criar investimento. Veja os logs.")
            return True

        if launch_id is None:
            await message.reply(f"ℹ️ O investimento **{canon}** já existe.")
        else:
            await message.reply(f"✅ Investimento criado: **{canon}** ({fmt_rate(rate, period)}) (ID: #{launch_id})")
        return True

    # ── Excluir ──────────────────────────────────────────────────────────────

    async def _excluir_investimento(self, message: discord.Message, uid: int, t: str) -> bool:
        parts = message.content.split("investimento", 1)
        name = parts[1].strip() if len(parts) > 1 else ""
        if not name:
            await message.reply("Qual investimento quer excluir? Ex: `excluir investimento CDB`")
            return True

        rows = list_investments(uid)
        inv = next((r for r in rows if r["name"].lower() == name.lower()), None)
        if not inv:
            await message.reply(f"Não achei esse investimento: **{name}**")
            return True

        canon = inv["name"]
        saldo = float(inv["balance"])
        if saldo != 0.0:
            await message.reply(
                f"⚠️ Não posso excluir o investimento **{canon}** "
                f"porque o saldo não é zero ({fmt_brl(saldo)}).\n"
                f"Retire o valor antes e tente novamente."
            )
            return True

        taxa = fmt_rate(inv.get("rate"), inv.get("period"))
        preview_text = (
            f"⚠️ Você está prestes a excluir este investimento:\n"
            f"• **{canon}** • saldo: **{fmt_brl(saldo)}**"
            + (f" • taxa: **{taxa}**" if taxa else "")
        )
        set_pending_action(
            uid, "delete_investment",
            {"investment_name": canon, "preview_text": preview_text},
            minutes=10,
        )
        await message.reply(
            preview_text + "\n\nResponda **sim** para confirmar ou **não** para cancelar. (expira em 10 min)"
        )
        return True

    # ── Aporte ───────────────────────────────────────────────────────────────

    async def _aportar(self, message: discord.Message, uid: int, t: str) -> bool:
        amount = parse_money(message.content)
        if amount is None:
            await message.reply("Qual valor? Ex: `apliquei 200 no investimento cdb`")
            return True

        name = self._extrair_nome_investimento(message.content, t,
                                               prefixes=("apliquei", "aplicar", "aportei", "aporte"))
        if not name:
            await message.reply("Em qual investimento? Ex: `apliquei 200 no investimento cdb`")
            return True

        try:
            launch_id, new_acc, new_inv, canon = investment_deposit_from_account(
                uid, investment_name=name, amount=float(amount), nota=message.content
            )
        except LookupError:
            await message.reply(
                f"Não achei esse investimento: **{name}**. Use: `criar investimento {name} 1% ao mês`"
            )
            return True
        except ValueError as e:
            if str(e) == "INSUFFICIENT_ACCOUNT":
                bal = get_balance(uid)
                await message.reply(f"Saldo insuficiente na conta. Conta: {fmt_brl(float(bal))}")
            else:
                await message.reply("Valor inválido.")
            return True
        except Exception:
            await message.reply("Deu erro ao aplicar/aportar no investimento. Veja os logs.")
            return True

        await message.reply(
            f"✅ Aporte em **{canon}**: +{fmt_brl(float(amount))}. Saldo: **{fmt_brl(float(new_inv))}**\n"
            f"🏦 Conta: {fmt_brl(float(new_acc))}\n"
            f"ID: #{launch_id}"
        )
        return True

    # ── Resgate ──────────────────────────────────────────────────────────────

    async def _resgatar(self, message: discord.Message, uid: int, t: str) -> bool:
        amount = parse_money(message.content)
        if amount is None:
            await message.reply("Qual valor? Ex: `resgatei 200 do investimento cdb`")
            return True

        name = self._extrair_nome_investimento(
            message.content, t,
            prefixes=("resgatei", "resgatar", "resgate", "retirei", "retirar", "saquei", "sacar"),
            prep="do investimento",
        )
        if not name:
            await message.reply("De qual investimento? Ex: `resgatei 200 do investimento cdb`")
            return True

        try:
            launch_id, new_acc, new_inv, canon = investment_withdraw_to_account(
                uid, investment_name=name, amount=float(amount), nota=message.content
            )
        except LookupError:
            await message.reply(
                f"Não achei esse investimento: **{name}**. Use: `criar investimento {name} 1% ao mês`"
            )
            return True
        except ValueError as e:
            if str(e) == "INSUFFICIENT_INVEST":
                await message.reply(f"Saldo insuficiente no investimento **{name}**.")
            else:
                await message.reply("Valor inválido.")
            return True
        except Exception:
            await message.reply("Deu erro ao resgatar investimento. Veja os logs.")
            return True

        await message.reply(
            f"💸 Resgate de **{canon}**: -{fmt_brl(float(amount))}. Saldo: **{fmt_brl(float(new_inv))}**\n"
            f"🏦 Conta: {fmt_brl(float(new_acc))}\n"
            f"ID: #{launch_id}"
        )
        return True

    # ── Extração de nome do investimento ─────────────────────────────────────

    @staticmethod
    def _extrair_nome_investimento(
        text: str, t: str, prefixes: tuple, prep: str = "no investimento"
    ) -> str | None:
        raw = text.lower()
        name = None

        if prep in raw:
            name = text.split(prep, 1)[1].strip()
        elif "investimento" in raw:
            parts = re.split(r'\binvestimento\b', text, flags=re.I, maxsplit=1)
            name = parts[1].strip() if len(parts) > 1 else None

        if not name:
            pattern = r'^(' + '|'.join(prefixes) + r')\b'
            tmp = re.sub(pattern, '', text, flags=re.I).strip()
            tmp = re.sub(r'\b\d[\d.,]*\b', '', tmp, count=1).strip()
            name = tmp.strip(" -–—") or None

        return name or None


async def setup(bot: commands.Bot):
    await bot.add_cog(InvestmentsCog(bot))
