import os
import re
from datetime import date, datetime, timedelta
from dateutil.relativedelta import relativedelta
import calendar
import discord
from discord.ext import commands
from db import init_db
from dotenv import load_dotenv
load_dotenv() #carrega o .env
from db import init_db, ensure_user, add_launch_and_update_balance, get_balance, list_launches, list_pockets, pocket_withdraw_to_account, create_pocket, pocket_deposit_from_account, delete_pocket, investment_withdraw_to_account, accrue_all_investments, create_investment, investment_deposit_from_account, delete_launch_and_rollback
from db import create_investment_db, delete_investment, get_pending_action, clear_pending_action, set_pending_action
from ai_router import handle_ai_message





# Lançamento (padrão único)
# {
#   "id": int,
#   "tipo": str,           # receita | despesa | deposito_caixinha | saque_caixinha | aporte_investimento | resgate_investimento | criar_caixinha | criar_investimento
#   "valor": float,        # sempre número (use 0.0 quando não tiver)
#   "alvo": str,           # categoria/caixinha/investimento
#   "nota": str | None,
#   "criado_em": str,      # ISO
#   "delta_conta": float   # impacto na conta corrente (+/-)
# }


# --------- helpers ---------


# foca a IA para responder questoes so do bot e nao geral
def should_use_ai(text: str) -> bool:
    t = text.lower().strip()

    # 1) Não chama IA pra coisas muito curtas / aleatórias
    if len(t) < 4:
        return False

    # 2) Palavras-chave financeiras
    keywords = [
        "saldo", "lanc", "lanç", "recebi", "receita", "gastei", "despesa",
        "caixinha", "caixinhas", "invest", "investimento", "aporte", "resgate",
        "fatura", "cartao", "cartão", "parcel", "metas", "limite", "gastos",
        "extrato", "conta", "rendeu", "rendendo", "cdb", "tesouro", "cdi"
    ]

    if any(k in t for k in keywords):
        return True

    # 3) Se começa com comandos conhecidos
    commands = [
        "saldo", "listar lancamentos", "listar lançamentos", "desfazer",
        "criar caixinha", "listar caixinhas", "saldo caixinhas",
        "criar investimento", "saldo investimentos"
    ]

    if any(t.startswith(c) for c in commands):
        return True

    return False


def is_business_day(d: date) -> bool:
    return d.weekday() < 5  # 0=seg ... 4=sex

def business_days_in_month(year: int, month: int) -> int:
    _, last_day = calendar.monthrange(year, month)
    count = 0
    for day in range(1, last_day + 1):
        if is_business_day(date(year, month, day)):
            count += 1
    return count

def business_days_in_year(year: int) -> int:
    start = date(year, 1, 1)
    end = date(year, 12, 31)
    d = start
    count = 0
    while d <= end:
        if is_business_day(d):
            count += 1
        d += timedelta(days=1)
    return count

def daily_business_rate(inv: dict, on_day: date) -> float:
    """
    Converte a taxa do investimento (ao dia/mês/ano) para taxa POR DIA ÚTIL,
    correta para o dia 'on_day' (muda conforme mês/ano).
    """
    r = float(inv.get("rate", 0.0))
    period = inv.get("period")

    if r == 0.0:
        return 0.0

    if period == "daily":
        return r  # 1% ao dia => 1% por dia útil (você pediu assim)

    if period == "monthly":
        n = business_days_in_month(on_day.year, on_day.month)
        if n <= 0:
            return 0.0
        return (1.0 + r) ** (1.0 / n) - 1.0

    if period == "yearly":
        n = business_days_in_year(on_day.year)
        if n <= 0:
            return 0.0
        return (1.0 + r) ** (1.0 / n) - 1.0

    return 0.0

def accrue_investment(inv: dict, today: date | None = None) -> None:
    """
    Aplica rendimento do último last_date até 'today' (default: hoje),
    SOMENTE em dias úteis, com taxa convertida corretamente por período.
    """
    if today is None:
        today = date.today()

    last = inv.get("last_date")
    if not isinstance(last, date):
        inv["last_date"] = today
        return

    if last >= today:
        return

    bal = float(inv.get("balance", 0.0))
    d = last + timedelta(days=1)

    while d <= today:
        if is_business_day(d) and bal > 0:
            dr = daily_business_rate(inv, d)
            bal *= (1.0 + dr)
        d += timedelta(days=1)

    inv["balance"] = bal
    inv["last_date"] = today



def fmt_brl(v: float) -> str:
    return f"R$ {v:.2f}"


DEPOSIT_VERBS = [
    "transferi", "coloquei", "adicionei", "depositei", "pus", "botei",
    "mandei", "joguei", "colocar", "adicionar", "depositar", "por", "botar"
]

def parse_money(text: str):
    m = re.search(r'(\d+[.,]?\d*)', text)
    if not m:
        return None
    return float(m.group(1).replace(",", "."))

def parse_interest(text: str):
    raw = text.lower()

    # bloqueia explicitamente "1," ou "1." (com espaços depois também)
    if re.search(r'\d+\s*[.,]\s*(?:%|\b)', raw):
        # exemplos que caem aqui: "1,", "1.", "1, %", "1. %"
        # mas "1,0" NÃO cai porque tem dígito após a vírgula
        if not re.search(r'\d+\s*[.,]\s*\d+', raw):
            return None

    # pega número: 1 / 1.1 / 1,1 / 0,03 etc
    m = re.search(r'(\d+(?:[.,]\d+)?)\s*%?', raw)
    if not m:
        return None

    taxa_pct = float(m.group(1).replace(",", "."))
    taxa = taxa_pct / 100.0

    # período
    if re.search(r'\b(ao|a|por)\s*dia\b|/dia', raw):
        period = "daily"
    elif re.search(r'\b(ao|a|por)\s*m[eê]s\b|/mes|/mês', raw):
        period = "monthly"
    elif re.search(r'\b(ao|a|por)\s*ano\b|/ano', raw):
        period = "yearly"
    else:
        return None

    return taxa, period


def months_between(d1: date, d2: date):
    if d2 <= d1:
        return 0
    rd = relativedelta(d2, d1)
    return rd.years * 12 + rd.months

def days_between(d1: date, d2: date):
    return max(0, (d2 - d1).days)



# --- PARSER DE RECEITA / DESPESA (COLE AQUI) ---
def parse_receita_despesa_natural(text: str):
    raw = re.sub(r"\s+", " ", text.lower()).strip()
    valor = parse_money(raw)
    if valor is None:
        return None

    verbos_despesa = ["gastei", "paguei", "comprei", "cartão", "cartao", "debitei"]
    verbos_receita = ["recebi", "ganhei", "salário", "salario", "pix recebido", "reembolso"]

    eh_despesa = any(v in raw for v in verbos_despesa)
    eh_receita = any(v in raw for v in verbos_receita)

    if not (eh_despesa or eh_receita):
        return None

    tipo = "despesa" if eh_despesa and not eh_receita else "receita" if eh_receita and not eh_despesa else None
    if tipo is None:
        return None

    # categoria simples
    categoria = "outros"
    if "ifood" in raw or "uber eats" in raw:
        categoria = "alimentação"
    elif "uber" in raw or "99" in raw:
        categoria = "transporte"
    elif "luz" in raw or "energia" in raw:
        categoria = "moradia"

    nota = raw  # você pode melhorar isso depois

    return {
        "tipo": tipo,
        "valor": valor,
        "categoria": categoria,
        "nota": nota
    }

def normalize_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()

def parse_pocket_deposit_natural(text: str):
    """
    Retorna (amount: float, pocket_name: str) ou (None, None)
    Entende frases como:
      - coloquei 300 na emergência
      - adicionei 50 na caixinha viagem
      - depositei 1200 em emergencia
      - transferi 200 pra caixinha emergencia
    """
    raw = normalize_spaces(text.lower())

    # precisa ter algum verbo de depósito
    if not any(v in raw for v in DEPOSIT_VERBS):
        return None, None

    amount = parse_money(raw)
    if amount is None:
        return None, None

    # tenta extrair o nome depois de "caixinha ..."
    if "caixinha" in raw:
        pocket = raw.split("caixinha", 1)[1].strip()
        pocket = re.sub(r"^(da|do|na|no|pra|para|em)\s+", "", pocket).strip()
        pocket = re.sub(r"\b(hoje|ontem)\b.*$", "", pocket).strip()
        if pocket:
            return amount, pocket

    # se não tem a palavra caixinha, tenta padrões "na/em/pra <nome>"
    m = re.search(r"\b(na|no|em|pra|para)\s+([a-z0-9_\-áàâãéèêíìîóòôõúùûç ]+)", raw)
    if m:
        pocket = m.group(2).strip()
        pocket = re.sub(r"\b(hoje|ontem)\b.*$", "", pocket).strip()
        # corta se tiver outras palavras típicas depois
        pocket = re.split(r"\b(saldo|investimento|apliquei|aplicar)\b", pocket)[0].strip()
        if pocket:
            return amount, pocket

    return None, None

# Categorias por palavras-chave (bem simples e eficaz)
CATEGORY_KEYWORDS = {
    "alimentação": ["ifood", "uber eats", "rappi", "restaurante", "lanche", "pizza", "hamburguer", "café", "padaria"],
    "mercado": ["mercado", "supermercado", "carrefour", "whole foods", "walmart", "target", "costco"],
    "transporte": ["uber", "lyft", "99", "metro", "trem", "ônibus", "gasolina", "combustível", "posto", "estacionamento", "parking"],
    "moradia": ["aluguel", "rent", "condomínio", "luz", "energia", "água", "internet", "wifi", "gás"],
    "saúde": ["farmácia", "remédio", "medicina", "consulta", "dentista", "hospital"],
    "assinaturas": ["netflix", "spotify", "prime", "amazon prime", "hbo", "disney", "icloud", "google one"],
    "compras": ["amazon", "shopee", "aliexpress", "loja", "compra", "roupa", "tenis", "sapato"],
    "lazer": ["cinema", "show", "bar", "balada", "viagem", "hotel", "airbnb"],
    "educação": ["curso", "udemy", "coursera", "livro", "faculdade", "mensalidade"],
    "outros": []
}

def guess_category(text: str) -> str:
    t = text.lower()
    for cat, words in CATEGORY_KEYWORDS.items():
        for w in words:
            if w in t:
                return cat
    return "outros"

def parse_note_after_amount(text: str, amount: float) -> str:
    """
    Pega uma "descrição" simples depois do valor.
    Ex: 'gastei 35 no ifood' -> 'ifood'
    """
    t = re.sub(r"\s+", " ", text.strip())
    # remove o valor (primeira ocorrência de número)
    t2 = re.sub(r"\d+[.,]?\d*", "", t, count=1).strip(" -–—:;")
    # remove palavras comuns
    t2 = re.sub(r"\b(gastei|gasto|paguei|pagar|recebi|ganhei|pix|no|na|em|pra|para|de|do|da)\b", "", t2, flags=re.I).strip()
    return t2.strip()[:60]  # limita

def parse_expense_income_natural(text: str):
    """
    Retorna dict com:
      kind: 'expense'|'income'
      amount: float
      note: str
      category: str
    ou None se não reconhecer.
    """
    raw = re.sub(r"\s+", " ", text.lower()).strip()
    amount = parse_money(raw)
    if amount is None:
        return None

    expense_verbs = ["gastei", "paguei", "comprei", "debitei", "cartão", "cartao"]
    income_verbs  = ["recebi", "ganhei", "caiu", "salário", "salario", "pix recebido", "reembolso"]

    is_expense = any(v in raw for v in expense_verbs)
    is_income  = any(v in raw for v in income_verbs)

    # se não tiver verbo claro, não assume
    if not (is_expense or is_income):
        return None

    kind = "expense" if is_expense and not is_income else "income" if is_income and not is_expense else None
    if kind is None:
        return None

    note = parse_note_after_amount(text, amount)
    category = guess_category(text)

    return {"kind": kind, "amount": amount, "note": note, "category": category}


# --------- bot setup ---------
intents = discord.Intents.default()
intents.message_content = True  # precisa habilitar no Developer Portal também

bot = commands.Bot(command_prefix="!", intents=intents)

HELP_TEXT = (

    "💰 **Receitas e Despesas (conta corrente)**\n"
    "• `recebi 1000 salario`\n"
    "• `gastei 120 mercado`\n\n"

    "🏦 **Conta Corrente**\n"
    "• `saldo`\n\n"

    "📦 **Caixinhas**\n"
    "• `criar caixinha viagem`\n"
    "• `coloquei 300 na caixinha viagem`\n"
    "• `retirei 100 da caixinha viagem`\n"
    "• `saldo caixinhas`\n"
    "• `listar caixinhas`\n\n"

    "📈 **Investimentos**\n"
    "• `criar investimento CDB Nubank 1% ao mês`\n"
    "• `criar investimento Tesouro 0,03% ao dia`\n"
    "• `apliquei 200 no investimento CDB Nubank`\n"
    "• `retirei 100 do investimento CDB Nubank`\n"
    "• `saldo investimentos`\n\n"

    "🧾 **Lançamentos**\n"
    "• `listar lançamentos`\n"
    "• `desfazer`\n"
    "• `apagar lançamento 3`\n"
)

@bot.event
async def on_ready():
    print(f"✅ Logado como {bot.user}")

@bot.event
async def on_message(message: discord.Message):
    # ignora mensagens do próprio bot
    if message.author.bot:
        return

    text = (message.content or "").strip()
    if not text:
        return
    t = text.lower()

    # Se existir uma ação pendente, processa "sim" / "não"
    pending = get_pending_action(message.author.id)
    if pending:
        ans = t.strip()
        if ans in ["sim", "s", "yes", "y"]:
            action = pending["action_type"]
            payload = pending["payload"]
            try:
                if action == "delete_launch":
                    delete_launch_and_rollback(message.author.id, int(payload["launch_id"]))
                    await message.reply(f"🗑️ Apagado e revertido: lançamento **#{payload['launch_id']}**.")
                elif action == "delete_pocket":
                    delete_pocket(message.author.id, payload["pocket_name"])
                    await message.reply(f"🗑️ Caixinha deletada: **{payload['pocket_name']}**.")
                elif action == "delete_investment":
                    delete_investment(message.author.id, payload["investment_name"])
                    await message.reply(f"🗑️ Investimento deletado: **{payload['investment_name']}**.")
                else:
                    await message.reply("Ação pendente desconhecida. Cancelando.")
                clear_pending_action(message.author.id)
            except Exception as e:
                print("Erro ao executar ação pendente:", e)
            await message.reply("❌ Deu erro ao executar a ação pendente. Veja os logs.")


        if ans in ["não", "nao", "n", "no"]:
            clear_pending_action(message.author.id)
            await message.reply("✅ Cancelado.")
            return


    if t in ["listar caixinhas", "saldo caixinhas", "caixinhas"]:
        rows = list_pockets(message.author.id)

        if not rows:
            await message.reply("Você ainda não tem caixinhas.")
            return

        total = sum(float(r["balance"]) for r in rows)
        linhas = [f"• **{r['name']}**: {fmt_brl(float(r['balance']))}" for r in rows]

        await message.reply(
            "📦 **Caixinhas:**\n"
            + "\n".join(linhas)
            + f"\n\nTotal nas caixinhas: **{fmt_brl(total)}**"
        )
        return
    
    # depositar na caixinha (ex: "transferi 200 para caixinha viagem", "adicionar 200 na caixinha viagem")
    if ("caixinha" in t) and any(w in t for w in ["transferi", "transferir", "adicionar", "colocar", "coloquei", "por", "depositar", "aporte", "aportei"]):
        amount = parse_money(text)
        if amount is None:
            await message.reply("Qual valor? Ex: `transferi 200 para caixinha viagem`")
            return

        # nome depois de "caixinha"
        parts = t.split("caixinha", 1)
        name = parts[1].strip() if len(parts) > 1 else ""
        if not name:
            await message.reply("Pra qual caixinha? Ex: `transferi 200 para caixinha viagem`")
            return

        name = re.sub(r'^(a|para|pra|na|no|da|do)\s+', '', name).strip()

        try:
            launch_id, new_acc, new_pocket, canon_name = pocket_deposit_from_account(
                message.author.id,
                pocket_name=name,
                amount=float(amount),
                nota=text
            )
        except LookupError:
            await message.reply(f"Não achei essa caixinha: **{name}**. Use: `criar caixinha {name}`")
            return
        except ValueError as e:
            if str(e) == "INSUFFICIENT_ACCOUNT":
                # pega saldo atual pra mensagem ficar boa
                bal = get_balance(message.author.id)
                await message.reply(f"Saldo insuficiente na conta. Conta: {fmt_brl(float(bal))}")
            else:
                await message.reply("Valor inválido.")
            return
        except Exception:
            await message.reply("Deu erro ao depositar na caixinha (Postgres). Veja os logs.")
            return

        await message.reply(
            f"✅ Depósito na caixinha **{canon_name}**: +{fmt_brl(float(amount))}\n"
            f"🏦 Conta: {fmt_brl(float(new_acc))} • 📦 Caixinha: {fmt_brl(float(new_pocket))}\n"
            f"ID: **#{launch_id}**"
        )
        return

    
    # sacar/retirar/resgatar X da caixinha Y (CAIXINHA -> CONTA)
    if any(w in t for w in ["retirei", "retirar", "sacar", "saquei", "resgatei", "resgatar"]) and "caixinha" in t:
        amount = parse_money(text)
        if amount is None:
            await message.reply("Qual valor? Ex: `retirei 200 da caixinha viagem`")
            return

        parts = t.split("caixinha", 1)
        name = parts[1].strip() if len(parts) > 1 else ""
        name = re.sub(r'^(da|do|de|na|no|para|pra)\s+', '', name).strip()

        if not name:
            await message.reply("De qual caixinha? Ex: `retirei 200 da caixinha viagem`")
            return

        try:
            launch_id, new_acc, new_pocket, canon_name = pocket_withdraw_to_account(
                message.author.id,
                pocket_name=name,
                amount=float(amount),
                nota=None
            )
        except LookupError:
            await message.reply(f"Não achei essa caixinha: **{name}**. Use: `criar caixinha {name}`")
            return
        except ValueError as e:
            if str(e) == "INSUFFICIENT_POCKET":
                await message.reply(f"Saldo insuficiente na caixinha **{name}**.")
            else:
                await message.reply("Valor inválido.")
            return
        except Exception:
            await message.reply("Deu erro ao sacar da caixinha (Postgres). Veja os logs.")
            return

        await message.reply(
            f"📤 Caixinha **{canon_name}**: -R$ {float(amount):.2f}\n"
            f"🏦 Conta: R$ {float(new_acc):.2f} • 📦 Caixinha: R$ {float(new_pocket):.2f}\n"
            f"ID: #{launch_id}"
        )
        return


   # =========================
    # Listar caixinhas (Postgres)
    # =========================
    if t in ["listar caixinhas", "lista caixinhas", "caixinhas"]:
        rows = list_pockets(message.author.id)

        if not rows:
            await message.reply("Você ainda não tem caixinhas. Use: `criar caixinha <nome>`")
            return

        total = sum(float(r["balance"]) for r in rows)
        lines = [f"📦 **{r['name']}**: {fmt_brl(float(r['balance']))}" for r in rows]

        await message.reply(
            "📦 **Suas caixinhas:**\n"
            + "\n".join(lines)
            + f"\n\nTotal em caixinhas: {fmt_brl(total)}"
        )
        return


    # excluir caixinha (Postgres)
    if t.startswith("excluir caixinha") or t.startswith("apagar caixinha") or t.startswith("remover caixinha"):
        parts = text.split("caixinha", 1)
        name = parts[1].strip() if len(parts) > 1 else ""

        if not name:
            await message.reply("Qual caixinha você quer excluir? Ex: `excluir caixinha viagem`")
            return

        try:
            launch_id, canon_name = delete_pocket(message.author.id, pocket_name=name)
        except LookupError:
            await message.reply(f"Não achei essa caixinha: **{name}**")
            return
        except ValueError as e:
            if str(e) == "POCKET_NOT_ZERO":
                # pega saldo atual pra mostrar na msg
                rows = list_pockets(message.author.id)
                saldo = None
                for r in rows:
                    if r["name"].lower() == name.lower():
                        saldo = float(r["balance"])
                        break
                if saldo is None:
                    await message.reply("⚠️ Não consegui ler o saldo da caixinha agora.")
                else:
                    await message.reply(
                        f"⚠️ Não posso excluir a caixinha **{name}** porque o saldo não é zero ({fmt_brl(saldo)})."
                    )
            else:
                await message.reply("Nome/valor inválido.")
            return
        except Exception:
            await message.reply("Deu erro ao excluir caixinha (Postgres). Veja os logs.")
            return

        await message.reply(f"🗑️ Caixinha **{canon_name}** excluída com sucesso. (ID: #{launch_id})")
        return

    # excluir investimento
    if t.startswith("excluir investimento") or t.startswith("apagar investimento") or t.startswith("remover investimento"):
        parts = text.split("investimento", 1)
        name = parts[1].strip() if len(parts) > 1 else ""
        if not name:
            await message.reply("Qual investimento você quer excluir? Ex: `excluir investimento CDB`")
            return

        try:
            launch_id, canon = delete_investment(message.author.id, name, nota=text)
        except LookupError:
            await message.reply(f"Não achei esse investimento: **{name}**")
            return
        except ValueError as e:
            if str(e) == "INV_NOT_ZERO":
                await message.reply("⚠️ Não posso excluir: o saldo do investimento não é zero.")
            else:
                await message.reply("Não consegui excluir esse investimento.")
            return
        except Exception:
            await message.reply("Deu erro ao excluir investimento (Postgres). Veja os logs.")
            return

        await message.reply(f"🗑️ Investimento **{canon}** excluído com sucesso. (ID: #{launch_id})")
        return

   # Gasto/Receita natural (ex: "gastei 35 no ifood", "recebi 2500 salario")
    parsed = parse_receita_despesa_natural(text)
    if parsed:
        user_id = message.author.id
        ensure_user(user_id)

        tipo = parsed["tipo"]                 # "despesa" ou "receita"
        valor = float(parsed["valor"])
        categoria = parsed["categoria"]
        nota = parsed.get("nota")

        launch_id, new_balance = add_launch_and_update_balance(
            user_id=user_id,
            tipo=tipo,
            valor=valor,
            alvo=categoria,
            nota=nota
        )

        emoji = "💸" if tipo == "despesa" else "💰"

        await message.reply(
            f"{emoji} **{tipo.capitalize()} registrada**: R$ {valor:.2f}\n"
            f"🏷️ Categoria: {categoria}\n"
            f"🏦 Conta: R$ {float(new_balance):.2f}\n"
            f"ID: #{launch_id}"
        )
        return


    

    # ajuda / comandos
    if t in ["ajuda", "help", "comandos", "listar comandos", "menu"]:
        texto = (
            "**📌 Comandos do Meu Assistente Financeiro**\n\n"

            "**🏦 Conta Corrente**\n"
            "• `saldo`\n"
            "• `recebi <valor> <categoria/opcional>`  (ex: `recebi 1000 salario`)\n"
            "• `gastei <valor> <categoria/opcional>`  (ex: `gastei 35 ifood`)\n\n"

            "**📦 Caixinhas**\n"
            "• `criar caixinha <nome>`  (ex: `criar caixinha viagem`)\n"
            "• `saldo caixinhas` / `listar caixinhas` / `caixinhas`\n\n"

            "**✅ Depósito (Conta ➜ Caixinha):**\n"
            "• `transferi <valor> para caixinha <nome>`\n"
            "• `coloquei <valor> na caixinha <nome>`\n"
            "• `aportei <valor> na caixinha <nome>`\n"
            "• `depositei <valor> na caixinha <nome>`\n\n"

            "**📤 Saque (Caixinha ➜ Conta):**\n"
            "• `retirei <valor> da caixinha <nome>`\n"
            "• `saquei <valor> da caixinha <nome>`\n"
            "• `resgatei <valor> da caixinha <nome>`\n\n"

            "**📈 Investimentos**\n"
            "• `criar investimento <nome> <taxa>% ao mês|ao dia`\n"
            "• ex: `criar investimento cdb_nubank 1% ao mês`\n"
            "• ex: `criar investimento tesouro 0,03% ao dia`\n"
            "• `saldo investimentos`\n\n"

            "**💰 Aporte (Conta ➜ Investimento):**\n"
            "• `apliquei <valor> no investimento <nome>`\n"
            "• `aportei <valor> no investimento <nome>`\n\n"

            "**🧾 Lançamentos**\n"
            "• `listar lançamentos` / `ultimos lançamentos`\n"
            "• `apagar <id>` / `remover <id>`\n"
            "• `desfazer`  (desfaz o último lançamento quando possível)\n\n"
        )

        await message.reply(texto)
        return


    # (Opcional) se você quiser responder só em DM, descomente:
    # if not isinstance(message.channel, discord.DMChannel):
    #     return

    # criar caixinha
    if t.startswith("criar caixinha"):
        parts = text.split("criar caixinha", 1)
        name = parts[1].strip() if len(parts) > 1 else ""
        if not name:
            await message.reply("Qual o nome da caixinha? Ex: `criar caixinha viagem`")
            return

        try:
            launch_id, pocket_id, pocket_name = create_pocket(
                message.author.id,
                name=name,
                nota=text
            )
        except Exception:
            await message.reply("Deu erro ao criar caixinha (Postgres). Veja os logs.")
            return

        if launch_id is None:
            await message.reply(f"ℹ️ A caixinha **{pocket_name}** já existe.")
            return

        await message.reply(f"✅ Caixinha criada: **{pocket_name}** (ID: **#{launch_id}**)")
        return




  # criar investimento (Postgres) — aceita taxa ao dia / ao mês / ao ano
    if t.startswith("criar investimento"):
        parts = text.split("criar investimento", 1)
        rest = parts[1].strip() if len(parts) > 1 else ""
        if not rest:
            await message.reply("Use: `criar investimento <nome> <taxa>% ao dia|ao mês|ao ano`")
            return

        m = re.search(r'(\d+(?:[.,]\d+)?)\s*%\s*(?:ao|a)\s*(dia|m[eê]s|ano)\b', rest, flags=re.I)
        if not m:
            await message.reply(
                "Não entendi a taxa/período. Exemplos:\n"
                "• `criar investimento CDB 1% ao mês`\n"
                "• `criar investimento Tesouro 0,03% ao dia`\n"
                "• `criar investimento IPCA 12% ao ano`"
            )
            return

        num_str = m.group(1).replace(",", ".")
        try:
            rate = float(num_str) / 100.0
        except ValueError:
            await message.reply("Taxa inválida. Ex: **1% ao mês**, **0,03% ao dia**, **12% ao ano**")
            return

        period_raw = m.group(2).lower()
        if "dia" in period_raw:
            period = "daily"
            periodo_str = "ao dia"
        elif "ano" in period_raw:
            period = "yearly"
            periodo_str = "ao ano"
        else:
            period = "monthly"
            periodo_str = "ao mês"

        name = (rest[:m.start()] + rest[m.end():]).strip(" -–—")
        if not name:
            await message.reply("Me diga o nome do investimento também. Ex: `criar investimento CDB 1% ao mês`")
            return

        try:
            launch_id, inv_id, canon = create_investment_db(
                message.author.id,
                name=name,
                rate=rate,
                period=period,
                nota=text
            )
        except Exception:
            await message.reply("Deu erro ao criar investimento (Postgres). Veja os logs.")
            return

        # já existia
        if launch_id is None:
            await message.reply(f"ℹ️ O investimento **{canon}** já existe.")
            return

        await message.reply(
            f"✅ Investimento criado: **{canon}** ({rate*100:.4g}% {periodo_str}) (ID: #{launch_id})"
        )
        return

    # depósito natural em caixinha (ex: "coloquei 300 na emergencia")
    amount, pocket_name = parse_pocket_deposit_natural(text)
    if amount is not None and pocket_name:
        try:
            launch_id, new_acc, new_pocket, canon_name = pocket_deposit_from_account(
                message.author.id,
                pocket_name=pocket_name,
                amount=float(amount),
                nota=text
            )
        except LookupError:
            await message.reply(f"Não achei essa caixinha: **{pocket_name}**. Use: `criar caixinha {pocket_name}`")
            return
        except ValueError as e:
            if str(e) == "INSUFFICIENT_ACCOUNT":
                bal = get_balance(message.author.id)
                await message.reply(f"Saldo insuficiente na conta. Conta: {fmt_brl(float(bal))}")
            else:
                await message.reply("Valor inválido.")
            return
        except Exception:
            await message.reply("Deu erro ao depositar na caixinha (Postgres). Veja os logs.")
            return

        await message.reply(
            f"✅ Depósito na caixinha **{canon_name}**: +{fmt_brl(float(amount))}\n"
            f"🏦 Conta: {fmt_brl(float(new_acc))} • 📦 Caixinha: {fmt_brl(float(new_pocket))}\n"
            f"ID: **#{launch_id}**"
        )
        return

   # aplicar/aporte no investimento (Postgres) — debita conta corrente
    if any(w in t for w in ["apliquei", "aplicar", "aportei", "aporte"]):
        amount = parse_money(text)
        if amount is None:
            await message.reply("Qual valor? Ex: `apliquei 200 no investimento cdb_nubank`")
            return

        raw = text.lower()

        # tenta extrair nome depois de "no investimento"
        name = None
        if "no investimento" in raw:
            name = text.split("no investimento", 1)[1].strip()

        # tenta extrair nome depois de "investimento"
        if not name and "investimento" in raw:
            parts = re.split(r'\binvestimento\b', text, flags=re.I, maxsplit=1)
            name = parts[1].strip() if len(parts) > 1 else None

        # fallback: "apliquei 500 cdb nubank"
        if not name:
            tmp = re.sub(r'^(apliquei|aplicar|aportei|aporte)\b', '', text, flags=re.I).strip()
            tmp = re.sub(r'\b\d[\d\.\,]*\b', '', tmp, count=1).strip()
            name = tmp.strip(" -–—") or None

        if not name:
            await message.reply("Em qual investimento? Ex: `apliquei 200 no investimento cdb_nubank`")
            return

        try:
            launch_id, new_acc, new_inv, canon_name = investment_deposit_from_account(
                message.author.id,
                investment_name=name,
                amount=float(amount),
                nota=text
            )
        except LookupError:
            await message.reply(f"Não achei esse investimento: **{name}**. Use: `criar investimento {name} 1% ao mês`")
            return
        except ValueError as e:
            if str(e) == "INSUFFICIENT_ACCOUNT":
                bal = get_balance(message.author.id)
                await message.reply(f"Saldo insuficiente na conta. Conta: {fmt_brl(float(bal))}")
            else:
                await message.reply("Valor inválido.")
            return
        except Exception:
            await message.reply("Deu erro ao aplicar/aportar no investimento (Postgres). Veja os logs.")
            return

        await message.reply(
            f"✅ Aporte em **{canon_name}**: +{fmt_brl(float(amount))}. Saldo: **{fmt_brl(float(new_inv))}**\n"
            f"🏦 Conta: {fmt_brl(float(new_acc))}\n"
            f"ID: #{launch_id}"
        )
        return

    
    # resgatar/retirar dinheiro do investimento (Postgres) — credita conta corrente
    if any(w in t for w in ["resgatei", "resgatar", "resgate", "retirei", "retirar", "saquei", "sacar"]):
        amount = parse_money(text)
        if amount is None:
            await message.reply("Qual valor? Ex: `resgatei 200 do investimento cdb_nubank`")
            return

        raw = text.lower()

        # tenta extrair nome depois de "do investimento"
        name = None
        if "do investimento" in raw:
            name = text.split("do investimento", 1)[1].strip()

        # tenta extrair nome depois de "investimento"
        if not name and "investimento" in raw:
            parts = re.split(r'\binvestimento\b', text, flags=re.I, maxsplit=1)
            name = parts[1].strip() if len(parts) > 1 else None

        # fallback: "resgatei 200 cdb nubank"
        if not name:
            tmp = re.sub(r'^(resgatei|resgatar|resgate|retirei|retirar|saquei|sacar)\b', '', text, flags=re.I).strip()
            tmp = re.sub(r'\b\d[\d\.\,]*\b', '', tmp, count=1).strip()
            name = tmp.strip(" -–—") or None

        if not name:
            await message.reply("De qual investimento? Ex: `resgatei 200 do investimento cdb_nubank`")
            return

        try:
            launch_id, new_acc, new_inv, canon_name = investment_withdraw_to_account(
                message.author.id,
                investment_name=name,
                amount=float(amount),
                nota=text
            )
        except LookupError:
            await message.reply(f"Não achei esse investimento: **{name}**. Use: `criar investimento {name} 1% ao mês`")
            return
        except ValueError as e:
            if str(e) == "INSUFFICIENT_INVEST":
                await message.reply(f"Saldo insuficiente no investimento **{name}**.")
            else:
                await message.reply("Valor inválido.")
            return
        except Exception:
            await message.reply("Deu erro ao resgatar investimento (Postgres). Veja os logs.")
            return

        await message.reply(
            f"💸 Resgate de **{canon_name}**: -{fmt_brl(float(amount))}. Saldo: **{fmt_brl(float(new_inv))}**\n"
            f"🏦 Conta: {fmt_brl(float(new_acc))}\n"
            f"ID: #{launch_id}"
        )
        return





   # saldo caixinhas (Postgres)
    if t == "saldo caixinhas":
        rows = list_pockets(message.author.id)
        if not rows:
            await message.reply("Você não tem caixinhas ainda. Use: `criar caixinha viagem`")
            return

        lines = "\n".join([f"- **{r['name']}**: {fmt_brl(float(r['balance']))}" for r in rows])
        await message.reply("💰 **Caixinhas:**\n" + lines)
        return


   # saldo investimentos (Postgres + aplica juros antes)
    if t == "saldo investimentos":
        rows = accrue_all_investments(message.author.id)
        if not rows:
            await message.reply("Você não tem investimentos ainda. Use: `criar investimento CDB 1,1% ao mês`")
            return

        lines = "\n".join([f"- **{r['name']}**: {fmt_brl(float(r['balance']))}" for r in rows])
        await message.reply("📈 **Investimentos:**\n" + lines)
        return

    

   # listar investimentos (Postgres + aplica juros antes)
    if t in ["listar investimentos", "lista investimentos", "investimentos", "meus investimentos"]:
        rows = accrue_all_investments(message.author.id)
        if not rows:
            await message.reply("Você ainda não tem investimentos.")
            return

        lines = ["📈 **Seus investimentos:**"]
        for r in rows:
            rate_pct = float(r["rate"]) * 100
            period = (r["period"] or "monthly").lower()
            period_str = "ao dia" if period == "daily" else ("ao mês" if period == "monthly" else "ao ano")
            bal = float(r["balance"])
            lines.append(f"• **{r['name']}** — {rate_pct:.4g}% {period_str} — saldo: {fmt_brl(bal)}")

        await message.reply("\n".join(lines))
        return


    
    # listar lancamentos (Postgres)
    if t in ["listar lancamentos", "listar lançamentos", "ultimos lancamentos", "últimos lançamentos"]:
        rows = list_launches(message.author.id, limit=10)

        if not rows:
            await message.reply("Você ainda não tem lançamentos.")
            return

        lines = []
        for r in rows:
            tipo = r["tipo"]
            valor = r["valor"]
            alvo = r["alvo"] or "-"
            criado = r["criado_em"]
            nota = r["nota"]

            # mesma limpeza que você já tinha (mantive)
            if tipo == "create_investment" and nota and "taxa=" in nota:
                try:
                    m_taxa = re.search(r"taxa=([0-9.]+)", nota)
                    m_per = re.search(r"periodo=(\w+)", nota)
                    taxa = float(m_taxa.group(1)) * 100 if m_taxa else None
                    per = m_per.group(1) if m_per else ""
                    per = "ao mês" if per.startswith("month") else "ao dia" if per.startswith("day") else per
                    nota = f"{taxa:.4g}% {per}" if taxa is not None else None
                except:
                    pass

            valor_str = f"R$ {float(valor):.2f}" if valor is not None else "-"
            nota_part = f" • {nota}" if nota else ""
            lines.append(f"#{r['id']} • {tipo} • {valor_str} • {alvo}{nota_part} • {criado}")

        await message.reply("🧾 **Últimos lançamentos:**\n" + "\n".join(lines))
        return


    

    # =========================
    # Apagar lançamento pelo ID (Postgres) - com confirmação
    # =========================
    if t.startswith("apagar") or t.startswith("remover"):
        m = re.search(r'(\d+)', t)
        if not m:
            await message.reply("Me diga o ID do lançamento. Ex: `apagar 3`")
            return

        launch_id = int(m.group(1))

        # (opcional) valida se existe antes de pedir confirmação
        rows = list_launches(message.author.id, limit=1000)
        if not any(int(r["id"]) == launch_id for r in rows):
            await message.reply(f"Não achei lançamento com ID {launch_id}.")
            return

        # cria a ação pendente (expira em 10 min)
        set_pending_action(message.author.id, "delete_launch", {"launch_id": launch_id}, minutes=10)

        await message.reply(
            f"⚠️ Tem certeza que deseja apagar o lançamento **#{launch_id}**?\n"
            f"Responda **sim** para confirmar ou **não** para cancelar. (expira em 10 min)"
        )
        return


    # comando para desfazer a última ação (100% Postgres)
    if t in ["desfazer", "undo", "voltar", "excluir"]:
        user_id = message.author.id

        rows = list_launches(user_id, limit=1)
        if not rows:
            await message.reply("Você não tem lançamentos para desfazer.")
            return

        last_id = int(rows[0]["id"])

        try:
            delete_launch_and_rollback(user_id, last_id)
        except LookupError:
            await message.reply("Não achei o último lançamento para desfazer (isso não deveria acontecer).")
            return
        except ValueError as e:
            await message.reply(f"Não consegui desfazer o último lançamento: {e}")
            return
        except Exception:
            await message.reply("Deu erro ao desfazer o último lançamento (Postgres). Veja os logs.")
            return

        await message.reply(f"↩️ Desfeito: lançamento **#{last_id}** (saldos ajustados no banco).")
        return
        
    # comando para ver saldo da conta
    if t in ["saldo", "saldo conta", "saldo da conta", "conta", "saldo geral"]:
        user_id = message.author.id
        bal = get_balance(user_id)
        
        await message.reply(f"🏦 **Conta Corrente:** R$ {float(bal):.2f}")
        return

    # fallback com IA (apenas se fizer sentido financeiro)
    if should_use_ai(message.content):
        ai_reply = await handle_ai_message(message.author.id, message.content)
        if ai_reply:
            await message.reply(ai_reply)
            return
        
    # fallback
    await message.reply("❓ **Não entendi seu comando. Tente um destes exemplos:**\n\n" + HELP_TEXT)




# --------- run ---------
if __name__ == "__main__":
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN não definido.")

    print("🗄️ Inicializando banco de dados (init_db)...")
    init_db()
    print("✅ Banco inicializado com sucesso!")

    bot.run(token)


