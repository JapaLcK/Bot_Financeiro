"""
adapters/discord/discord_bot.py — Entrypoint do bot Discord.

Responsabilidades:
  - Configurar o bot e os intents
  - Carregar os Cogs (um por domínio)
  - Orquestrar o pipeline on_message:
      1. core_handle_incoming  (OFX, help compartilhado)
      2. GeneralCog            (menu, ajuda, pending, dashboard, CDI)
      3. PocketsCog            (caixinhas)
      4. InvestmentsCog        (investimentos)
      5. AccountsCog           (saldo, lançamentos, excel)
      6. handle_credit_commands (cartão de crédito)
      7. handle_quick_entry    (receita/despesa natural)
      8. fallback              (mensagem de erro)
"""
import asyncio
import os
import sys
import time as pytime

import discord
from discord.ext import commands

from config.env import load_app_env

load_app_env()

from core.observability import get_logger, log_system_event_sync
logger = get_logger(__name__)

from db import init_db, get_or_create_canonical_user
from core.types import IncomingMessage, Attachment
from core.handle_incoming import handle_incoming as core_handle_incoming
from core.services.quick_entry import handle_quick_entry
from core.reports.reports_daily import setup_daily_report
from handlers.credit import handle_credit_commands

from adapters.discord.cogs.general_cog import GeneralCog
from adapters.discord.cogs.pockets_cog import PocketsCog
from adapters.discord.cogs.investments_cog import InvestmentsCog
from adapters.discord.cogs.accounts_cog import AccountsCog

# ── Instâncias dos Cogs (acesso direto para chamar .handle()) ─────────────────
_general_cog: GeneralCog | None = None
_pockets_cog: PocketsCog | None = None
_investments_cog: InvestmentsCog | None = None
_accounts_cog: AccountsCog | None = None


class FinanceBot(commands.Bot):
    async def setup_hook(self) -> None:
        global _general_cog, _pockets_cog, _investments_cog, _accounts_cog

        if self.get_cog("GeneralCog"):
            logger.info("Cogs ja estavam carregados; pulando setup.")
            setup_daily_report(self)
            return

        _general_cog = GeneralCog(self)
        _pockets_cog = PocketsCog(self)
        _investments_cog = InvestmentsCog(self)
        _accounts_cog = AccountsCog(self)

        await self.add_cog(_general_cog)
        await self.add_cog(_pockets_cog)
        await self.add_cog(_investments_cog)
        await self.add_cog(_accounts_cog)

        setup_daily_report(self)
        logger.info("Cogs carregados: General, Pockets, Investments, Accounts")


# ── Bot setup ─────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True

bot = FinanceBot(command_prefix="!", intents=intents)

HELP_TEXT_SHORT = (
    "❓ **Não entendi esse comando.**\n"
    "Digite `ajuda` para ver todos os comandos.\n"
    "Exemplos:\n"
    "• `gastei 50 mercado`\n"
    "• `recebi 1000 salario`\n"
    "• `saldo`\n"
)


@bot.event
async def on_ready():
    logger.info("Logado como %s", bot.user)


@bot.event
async def on_error(event: str, *args, **kwargs) -> None:
    """Captura exceções não tratadas em qualquer evento Discord."""
    import traceback as _tb
    exc = sys.exc_info()
    tb_str = "".join(_tb.format_exception(*exc)) if exc[0] else ""
    logger.error("Exceção não tratada no evento '%s': %s", event, exc[1], exc_info=exc)
    log_system_event_sync(
        "error",
        "discord_unhandled_event_error",
        f"Exceção não tratada no evento '{event}': {exc[1]}",
        source="discord_bot.on_error",
        details={"event": event, "traceback": tb_str},
    )


@bot.event
async def on_message(message: discord.Message):
    # ignora mensagens do próprio bot
    if message.author.bot:
        return

    text = (message.content or "").strip()
    if (not text) and (not message.attachments):
        return

    t = text.casefold()
    external_id = str(message.author.id)
    uid = get_or_create_canonical_user("discord", external_id)

    try:
        await _handle_message(message, text, t, external_id, uid)
    except Exception as e:
        logger.error("Exceção não tratada em on_message (uid=%s): %s", uid, e, exc_info=True)
        log_system_event_sync(
            "error",
            "discord_on_message_crash",
            f"Crash em on_message: {e}",
            source="discord_bot.on_message",
            user_id=uid,
            details={"text": text[:200]},
        )
        try:
            await message.reply("❌ Ocorreu um erro inesperado. Nossa equipe foi notificada.")
        except Exception:
            pass


async def _handle_message(message: discord.Message, text: str, t: str, external_id: str, uid: int) -> None:
    async with message.channel.typing():
        # ── 1. Pipeline core (OFX, help compartilhado WhatsApp/Discord) ──────
        atts = []
        if message.attachments:
            for a in message.attachments:
                try:
                    data = await a.read()
                    atts.append(Attachment(
                        filename=a.filename or "arquivo",
                        content_type=a.content_type or "application/octet-stream",
                        data=data,
                    ))
                except Exception:
                    pass

        incoming = IncomingMessage(
            platform="discord",
            user_id=uid,
            external_id=external_id,
            text=text,
            message_id=str(message.id),
            attachments=atts,
        )

        outs = core_handle_incoming(incoming)
        if outs:
            for out in outs:
                await message.reply(out.text)
            return

        # ── 2. GeneralCog (menu, ajuda, pending, dashboard, CDI) ─────────────
        if _general_cog and await _general_cog.handle(message, t, uid):
            return

        # ── 3. PocketsCog (caixinhas) ─────────────────────────────────────────
        if _pockets_cog and await _pockets_cog.handle(message, t, uid):
            return

        # ── 4. InvestmentsCog (investimentos) ─────────────────────────────────
        if _investments_cog and await _investments_cog.handle(message, t, uid):
            return

        # ── 5. AccountsCog (saldo, lançamentos, excel) ───────────────────────
        if _accounts_cog and await _accounts_cog.handle(message, t, uid):
            return

        # ── 6. Crédito (cartão, fatura, parcelamento) ─────────────────────────
        if await handle_credit_commands(message, uid):
            return

        # ── 7. Entrada rápida (receita/despesa natural) ───────────────────────
        msg_out = handle_quick_entry(uid, text)
        if msg_out:
            await message.reply(msg_out.text)
            return

        # ── 8. Fallback ───────────────────────────────────────────────────────
        await message.reply("❓ **Não entendi seu comando. Tente um destes exemplos:**\n\n" + HELP_TEXT_SHORT)


# ── run() ─────────────────────────────────────────────────────────────────────

def run():
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN não definido.")

    def _asyncio_exception_handler(loop, context: dict) -> None:
        """Captura exceções em tasks asyncio sem observer."""
        exc = context.get("exception")
        msg = context.get("message", "sem mensagem")
        if exc:
            logger.error("Task asyncio sem tratamento: %s | %s", msg, exc, exc_info=(type(exc), exc, exc.__traceback__))
            log_system_event_sync(
                "error",
                "asyncio_unhandled_task_error",
                f"Task asyncio sem tratamento: {msg} | {exc}",
                source="discord_bot.asyncio",
                details={"context": {k: str(v) for k, v in context.items()}},
            )
        else:
            logger.warning("Evento asyncio sem exceção: %s", msg)

    try:
        logger.info("Inicializando banco de dados...")
        init_db()
        logger.info("Banco inicializado com sucesso.")
    except Exception as e:
        logger.critical("Falha no init_db: %s", e, exc_info=True)
        raise

    wait = 15
    while True:
        try:
            logger.info("Conectando no Discord...")
            bot.run(token)
            wait = 15
        except Exception as e:
            msg = str(e)
            logger.error("Bot caiu: %s", msg, exc_info=True)

            if "429" in msg or "Too Many Requests" in msg:
                wait = max(wait, 60)

            logger.info("Aguardando %ss para tentar de novo...", wait)
            pytime.sleep(wait)
            wait = min(wait * 2, 600)
