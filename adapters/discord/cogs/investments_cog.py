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
                rate_pct = float(r["rate"]) * 100
                period = (r["period"] or "monthly").lower()
                period_str = "ao dia" if period == "daily" else ("ao mês" if period == "monthly" else "ao ano")
                lines.append(
                    f"• **{r['name']}** — {rate_pct:.4g}% {period_str} — saldo: {fmt_brl(float(r['balance']))}"
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

        m_cdi = re.search(r'(\d+(?:[.,]\d+)?)\s*%\s*(?:do\s*)?cdi\b', rest, flags=re.I)
        m = re.search(r'(\d+(?:[.,]\d+)?)\s*%\s*(?:ao|a)\s*(dia|m[eê]s|ano)\b', rest, flags=re.I)

        if not m_cdi and not m:
            await message.reply(
                "Não entendi a taxa/período. Exemplos:\n"
                "• `criar investimento CDB 1% ao mês`\n"
                "• `criar investimento Tesouro 0,03% ao dia`\n"
                "• `criar investimento IPCA 12% ao ano`\n"
                "• `criar investimento CDB 100% CDI`"
            )
            return True

        if m_cdi:
            try:
                pct_cdi = float(m_cdi.group(1).replace(",", "."))
            except ValueError:
                await message.reply("Percentual do CDI inválido. Ex: `criar investimento CDB 110% cdi`")
                return True
            rate = pct_cdi / 100.0
            period = "cdi"
            periodo_str = f"{pct_cdi:.4g}% do CDI"
            name = (rest[:m_cdi.start()] + rest[m_cdi.end():]).strip(" -–—")
        else:
            try:
                rate = float(m.group(1).replace(",", ".")) / 100.0
            except ValueError:
                await message.reply("Taxa inválida. Ex: **1% ao mês**, **0,03% ao dia**, **12% ao ano**")
                return True
            period_raw = m.group(2).lower()
            if "dia" in period_raw:
                period, periodo_str = "daily", "ao dia"
            elif "ano" in period_raw:
                period, periodo_str = "yearly", "ao ano"
            else:
                period, periodo_str = "monthly", "ao mês"
            name = (rest[:m.start()] + rest[m.end():]).strip(" -–—")

        if not name:
            await message.reply("Me diga o nome do investimento também. Ex: `criar investimento CDB 1% ao mês`")
            return True

        try:
            launch_id, inv_id, canon = create_investment_db(
                uid, name=name, rate=rate, period=period, nota=message.content
            )
        except Exception as e:
            print("ERRO criar investimento:", repr(e))
            await message.reply("Deu erro ao criar investimento. Veja os logs.")
            return True

        if launch_id is None:
            await message.reply(f"ℹ️ O investimento **{canon}** já existe.")
        elif period == "cdi":
            await message.reply(f"✅ Investimento criado: **{canon}** ({periodo_str}) (ID: #{launch_id})")
        else:
            await message.reply(
                f"✅ Investimento criado: **{canon}** ({rate * 100:.4g}% {periodo_str}) (ID: #{launch_id})"
            )
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
