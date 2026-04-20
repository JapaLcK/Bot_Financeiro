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
import os
import time as pytime
import traceback

import discord
from discord.ext import commands

from config.env import load_app_env

load_app_env()

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

# ── Bot setup ─────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

HELP_TEXT_SHORT = (
    "❓ **Não entendi esse comando.**\n"
    "Digite `ajuda` para ver todos os comandos.\n"
    "Exemplos:\n"
    "• `gastei 50 mercado`\n"
    "• `recebi 1000 salario`\n"
    "• `saldo`\n"
)

# ── Instâncias dos Cogs (acesso direto para chamar .handle()) ─────────────────
_general_cog: GeneralCog | None = None
_pockets_cog: PocketsCog | None = None
_investments_cog: InvestmentsCog | None = None
_accounts_cog: AccountsCog | None = None


@bot.event
async def on_ready():
    global _general_cog, _pockets_cog, _investments_cog, _accounts_cog

    print(f"✅ Logado como {bot.user}")

    # Registra Cogs
    _general_cog = GeneralCog(bot)
    _pockets_cog = PocketsCog(bot)
    _investments_cog = InvestmentsCog(bot)
    _accounts_cog = AccountsCog(bot)

    await bot.add_cog(_general_cog)
    await bot.add_cog(_pockets_cog)
    await bot.add_cog(_investments_cog)
    await bot.add_cog(_accounts_cog)

    setup_daily_report(bot)
    print("✅ Cogs carregados: General, Pockets, Investments, Accounts")


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

    # ── 1. Pipeline core (OFX, help compartilhado WhatsApp/Discord) ──────────
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

    # ── 2. GeneralCog (menu, ajuda, pending, dashboard, CDI) ─────────────────
    if _general_cog and await _general_cog.handle(message, t, uid):
        return

    # ── 3. PocketsCog (caixinhas) ─────────────────────────────────────────────
    if _pockets_cog and await _pockets_cog.handle(message, t, uid):
        return

    # ── 4. InvestmentsCog (investimentos) ─────────────────────────────────────
    if _investments_cog and await _investments_cog.handle(message, t, uid):
        return

    # ── 5. AccountsCog (saldo, lançamentos, excel) ────────────────────────────
    if _accounts_cog and await _accounts_cog.handle(message, t, uid):
        return

    # ── 6. Crédito (cartão, fatura, parcelamento) ─────────────────────────────
    if await handle_credit_commands(message, uid):
        return

    # ── 7. Entrada rápida (receita/despesa natural) ───────────────────────────
    msg_out = handle_quick_entry(uid, text)
    if msg_out:
        await message.reply(msg_out.text)
        return

    # ── 8. Fallback ───────────────────────────────────────────────────────────
    await message.reply("❓ **Não entendi seu comando. Tente um destes exemplos:**\n\n" + HELP_TEXT_SHORT)


# ── run() ─────────────────────────────────────────────────────────────────────

def run():
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN não definido.")

    try:
        print("🗄️ Inicializando banco de dados...")
        init_db()
        print("✅ Banco inicializado com sucesso!")
    except Exception as e:
        print("❌ Falha no init_db:", e)
        traceback.print_exc()
        raise

    wait = 15
    while True:
        try:
            print("🤖 Conectando no Discord...")
            bot.run(token)
            wait = 15
        except Exception as e:
            msg = str(e)
            print("❌ Bot caiu:", msg)
            traceback.print_exc()

            if "429" in msg or "Too Many Requests" in msg:
                wait = max(wait, 60)

            print(f"⏳ Aguardando {wait}s para tentar de novo...")
            pytime.sleep(wait)
            wait = min(wait * 2, 600)
