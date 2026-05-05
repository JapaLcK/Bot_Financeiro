"""
cogs/investments_cog.py — Comandos de investimentos.

Comandos tratados:
  - saldo investimentos
  - listar investimentos / meus investimentos
  - abrir dashboard para criar/editar investimentos
  - apliquei/aportei X no investimento <nome>          (Conta → Investimento)
  - resgatei/retirei/saquei X do investimento <nome>   (Investimento → Conta)
  - excluir investimento <nome>                        (com confirmação)
"""
import re

import discord
from discord.ext import commands

from core.dashboard_links import build_dashboard_link
from db import (
    list_investments,
    accrue_all_investments,
    investment_deposit_from_account,
    investment_withdraw_to_account,
    set_pending_action,
    get_balance,
)
from utils_text import fmt_brl, fmt_rate, parse_money


class InvestmentsCog(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _dashboard_link_text(self, uid: int) -> str:
        link = build_dashboard_link(uid, view="investments")
        if not link:
            return "⚠️ Não consegui gerar o link do dashboard agora. Tente novamente em instantes."
        return (
            "O bot cria investimentos pelo dashboard para evitar cadastro incompleto por mensagem.\n"
            "Abra a aba de investimentos para criar, editar, aportar ou resgatar com todos os detalhes:\n"
            f"{link}\n"
            "⏱️ Link mágico de uso único, expira em 5 minutos."
        )

    def _investments_text(self, uid: int, intro: str | None = None) -> str:
        rows = accrue_all_investments(uid)
        lines = [intro or "📈 **Seus investimentos:**"]
        if rows:
            for r in rows:
                period_str = fmt_rate(r.get("rate"), r.get("period"))
                asset = r.get("asset_type") or "CDB"
                projected_balance = r.get("projected_balance")
                projected_days = r.get("projected_days") or 0
                if projected_days > 0 and projected_balance:
                    saldo_txt = f"{fmt_brl(float(projected_balance))} *"
                else:
                    saldo_txt = fmt_brl(float(r["balance"]))
                lines.append(
                    f"• **{r['name']}** [{asset}] — {period_str} — saldo: {saldo_txt}"
                )
            if any((r.get("projected_days") or 0) > 0 for r in rows):
                lines.append(
                    "_* saldo estimado com a última taxa CDI conhecida — "
                    "será corrigido quando o BCB publicar os dados oficiais._"
                )
        else:
            lines.append("Você ainda não tem investimentos cadastrados.")
        lines.append("")
        lines.append(self._dashboard_link_text(uid))
        return "\n".join(lines)

    async def handle(self, message: discord.Message, t: str, uid: int) -> bool:
        """Retorna True se este Cog tratou a mensagem."""

        # ── Saldo investimentos ───────────────────────────────────────────────
        if t == "saldo investimentos":
            await message.reply(self._investments_text(uid, "📈 **Investimentos:**"))
            return True

        # ── Listar investimentos ──────────────────────────────────────────────
        if t in ("listar investimentos", "lista investimentos", "investimentos", "meus investimentos"):
            await message.reply(self._investments_text(uid))
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
        await message.reply(
            self._investments_text(uid, "📈 Eu consigo te ajudar a criar investimentos, mas agora isso é feito pelo dashboard.")
        )
        return True

    # ── Excluir ──────────────────────────────────────────────────────────────

    async def _excluir_investimento(self, message: discord.Message, uid: int, t: str) -> bool:
        parts = message.content.split("investimento", 1)
        name = parts[1].strip() if len(parts) > 1 else ""
        if not name:
            await message.reply(self._investments_text(uid, "Qual investimento quer excluir?"))
            return True

        rows = list_investments(uid)
        inv = next((r for r in rows if r["name"].lower() == name.lower()), None)
        if not inv:
            await message.reply(self._investments_text(uid, f"Não achei esse investimento: **{name}**"))
            return True

        canon = inv["name"]
        saldo = float(inv["balance"])
        if saldo != 0.0:
            await message.reply(
                f"⚠️ Não posso excluir o investimento **{canon}** "
                f"porque o saldo não é zero ({fmt_brl(saldo)}).\n"
                f"Retire o valor antes e tente novamente.\n\n{self._dashboard_link_text(uid)}"
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
            await message.reply(self._investments_text(uid, "Qual valor você quer aportar?"))
            return True

        name = self._extrair_nome_investimento(message.content, t,
                                               prefixes=("apliquei", "aplicar", "aportei", "aporte"))
        if not name:
            await message.reply(self._investments_text(uid, "Em qual investimento você quer aportar?"))
            return True

        try:
            launch_id, new_acc, new_inv, canon = investment_deposit_from_account(
                uid, investment_name=name, amount=float(amount), nota=message.content
            )
        except LookupError:
            await message.reply(self._investments_text(uid, f"Não achei esse investimento: **{name}**."))
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
            f"ID: #{launch_id}\n\n"
            f"{self._investments_text(uid)}"
        )
        return True

    # ── Resgate ──────────────────────────────────────────────────────────────

    async def _resgatar(self, message: discord.Message, uid: int, t: str) -> bool:
        amount = parse_money(message.content)
        if amount is None:
            await message.reply(self._investments_text(uid, "Qual valor você quer resgatar?"))
            return True

        name = self._extrair_nome_investimento(
            message.content, t,
            prefixes=("resgatei", "resgatar", "resgate", "retirei", "retirar", "saquei", "sacar"),
            prep="do investimento",
        )
        if not name:
            await message.reply(self._investments_text(uid, "De qual investimento você quer resgatar?"))
            return True

        try:
            launch_id, new_acc, new_inv, canon, taxes = investment_withdraw_to_account(
                uid, investment_name=name, amount=float(amount), nota=message.content
            )
        except LookupError:
            await message.reply(self._investments_text(uid, f"Não achei esse investimento: **{name}**."))
            return True
        except ValueError as e:
            if str(e) == "INSUFFICIENT_INVEST":
                await message.reply(f"Saldo insuficiente no investimento **{name}**.\n\n{self._investments_text(uid)}")
            else:
                await message.reply("Valor inválido.")
            return True
        except Exception:
            await message.reply("Deu erro ao resgatar investimento. Veja os logs.")
            return True

        tax_note = ""
        if taxes and float(taxes.get("iof", 0) or 0) + float(taxes.get("ir", 0) or 0) > 0:
            tax_note = f"\nLíquido creditado: **{fmt_brl(float(taxes.get('net', 0)))}**"
        await message.reply(
            f"💸 Resgate de **{canon}**: -{fmt_brl(float(amount))}. Saldo: **{fmt_brl(float(new_inv))}**"
            f"{tax_note}\n🏦 Conta: {fmt_brl(float(new_acc))}\n"
            f"ID: #{launch_id}\n\n"
            f"{self._investments_text(uid)}"
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
