"""
cogs/general_cog.py — Comandos gerais: ajuda, menu, ações pendentes, dashboard e CDI.

Também intercepta o pipeline core (handle_incoming) antes dos demais Cogs.
"""
import discord
from discord.ext import commands

from db import (
    get_pending_action, clear_pending_action, set_pending_action,
    delete_launch_and_rollback, delete_pocket, delete_investment,
    get_conn, get_latest_cdi_aa,
)
from core.help_text import resolve_section
from core.dashboard_links import build_dashboard_link
from adapters.discord.help_ui import help_embed, HelpView
from core.observability import get_logger

logger = get_logger(__name__)


class GeneralCog(commands.Cog):
    """
    Responsabilidades:
    - Menu / ajuda interativa
    - Ações pendentes (sim / não)
    - Dashboard financeiro
    - CDI atual
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def handle(self, message: discord.Message, t: str, uid: int) -> bool:
        """
        Tenta processar a mensagem. Retorna True se tratou, False se deve continuar.
        """
        # ── Menu ──────────────────────────────────────────────────────────────
        if t in {"comandos", "listar comandos", "menu"}:
            await message.reply(embed=help_embed("start"), view=HelpView(message.author.id))
            return True

        # ── Ajuda ─────────────────────────────────────────────────────────────
        if t.startswith("ajuda") or t.startswith("help"):
            section = resolve_section(message.content)
            await message.reply(embed=help_embed(section), view=HelpView(message.author.id))
            return True

        # ── Ações pendentes (sim / não) ───────────────────────────────────────
        pending = get_pending_action(uid)
        if pending:
            ans = t.strip()

            if ans in ("sim", "s", "yes", "y"):
                await self._confirmar_pending(message, uid, pending)
                return True

            if ans in ("nao", "não", "n", "no"):
                try:
                    clear_pending_action(uid)
                except Exception as e:
                    logger.error("Erro ao limpar pending_action: %s", e, exc_info=True)
                await message.reply("❌ Ação cancelada.")
                return True

            # mensagem fora de contexto enquanto há ação pendente
            preview = pending.get("payload", {}).get("preview_text")
            if preview:
                await message.reply(preview + "\n\nResponda **sim** para confirmar ou **não** para cancelar.")
            else:
                await message.reply(
                    "⚠️ Existe uma ação pendente.\n"
                    "Responda **sim** para confirmar ou **não** para cancelar."
                )
            return True

        # ── Dashboard ─────────────────────────────────────────────────────────
        if t in ("dashboard", "ver dashboard", "abrir dashboard", "painel", "ver painel"):
            link = build_dashboard_link(uid, hours=5 / 60)
            if not link:
                await message.reply(
                    "⚠️ Não consegui gerar seu link do dashboard agora.\n"
                    "Tente novamente em instantes."
                )
                return True
            await message.reply(
                f"📊 **Dashboard financeiro**\n"
                f"🔗 {link}\n\n"
                f"Acesse pelo navegador para ver seus dados em tempo real.\n"
                f"⏱️ O link expira em 5 minutos e funciona uma única vez."
            )
            return True

        # ── CDI ───────────────────────────────────────────────────────────────
        if t in ("ver cdi", "cdi"):
            try:
                with get_conn() as conn:
                    with conn.cursor() as cur:
                        res = get_latest_cdi_aa(cur)

                if not res:
                    await message.reply("⚠️ Não consegui obter a CDI agora. Tente novamente mais tarde.")
                    return True

                ref_date, cdi_aa = res
                await message.reply(
                    f"📊 **CDI (a.a.)**\n"
                    f"Data: **{ref_date.strftime('%d/%m/%Y')}**\n"
                    f"Valor: **{cdi_aa:.2f}% ao ano**"
                )
            except Exception as e:
                logger.error("Erro ao buscar CDI: %s", e, exc_info=True)
                await message.reply("❌ Erro ao buscar a CDI. Veja os logs.")
            return True

        return False

    # ── Helpers privados ─────────────────────────────────────────────────────

    async def _confirmar_pending(self, message: discord.Message, uid: int, pending: dict):
        action = pending["action_type"]
        payload = pending["payload"]

        try:
            if action == "delete_launch":
                delete_launch_and_rollback(uid, int(payload["launch_id"]))
                await message.reply(f"🗑️ Apagado e revertido: lançamento **#{payload['launch_id']}**.")

            elif action == "delete_launch_bulk":
                ids = payload["launch_ids"]
                failed = []
                for lid in ids:
                    try:
                        delete_launch_and_rollback(uid, int(lid))
                    except Exception:
                        failed.append(lid)
                ok_ids = [i for i in ids if i not in failed]
                parts = []
                if ok_ids:
                    parts.append("🗑️ Apagados: " + ", ".join(f"**#{i}**" for i in ok_ids))
                if failed:
                    parts.append("⚠️ Falha ao apagar: " + ", ".join(f"#{i}" for i in failed))
                await message.reply("\n".join(parts))

            elif action == "delete_pocket":
                delete_pocket(uid, payload["pocket_name"])
                await message.reply(f"🗑️ Caixinha deletada: **{payload['pocket_name']}**.")

            elif action == "delete_investment":
                delete_investment(uid, payload["investment_name"])
                await message.reply(f"🗑️ Investimento deletado: **{payload['investment_name']}**.")

            else:
                await message.reply("Ação pendente desconhecida. Cancelando.")

        except Exception as e:
            logger.error("Erro ao executar ação pendente '%s': %s", action, e, exc_info=True)
            await message.reply("❌ Deu erro ao executar a ação pendente. Veja os logs.")
        finally:
            try:
                clear_pending_action(uid)
            except Exception as e:
                logger.error("Erro ao limpar pending_action: %s", e, exc_info=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(GeneralCog(bot))
