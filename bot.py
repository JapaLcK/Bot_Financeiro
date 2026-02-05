import os
import re
import itertools
from datetime import date, datetime, timedelta
from dateutil.relativedelta import relativedelta
import calendar
import discord
from discord.ext import commands
from db import init_db
from dotenv import load_dotenv
load_dotenv() #carrega o .env






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

def registrar_lancamento(launches, launch_id, tipo, valor, alvo, nota, delta_conta):
    launches.append({
        "id": next(launch_id),
        "tipo": tipo,
        "valor": float(valor),
        "alvo": alvo,
        "nota": nota,
        "criado_em": datetime.now().isoformat(timespec="seconds"),
        "delta_conta": float(delta_conta),
    })
    return launches[-1]

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



# --------- in-memory storage (MVP) ---------
# Se quiser multi-usuário: use dict por user_id (eu já deixei assim)
DATA = {}  # user_id -> {"pockets": {}, "investments": {}}
LAUNCH_ID = itertools.count(1) # contador de IDs por usuário
# user_id -> lista de lançamentos
LAUNCHES = {}  # ex: { user_id: [ {id, type, amount, target, date}, ... ] }

def get_user_store(user_id: int):
    if user_id not in DATA:
        DATA[user_id] = {
            "conta": 0.0,        # saldo da conta corrente (cash)
            "pockets": {},      # caixinhas
            "investments": {}   # investimentos
        }
    DATA[user_id].setdefault("conta", 0.0)
    DATA[user_id].setdefault("pockets", {})
    DATA[user_id].setdefault("investments", {})
    return DATA[user_id]


def get_user_launches(user_id: int):
    if user_id not in LAUNCHES:
        LAUNCHES[user_id] = []
    return LAUNCHES[user_id]

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

    store = get_user_store(message.author.id)
    pockets = store["pockets"]
    investments = store["investments"]

    if t in ["listar caixinhas", "saldo caixinhas", "caixinhas"]:
        if not pockets:
            await message.reply("Você ainda não tem caixinhas.")
            return

        total = sum(float(v) for v in pockets.values())
        linhas = [f"• **{k}**: {fmt_brl(float(v))}" for k, v in sorted(pockets.items(), key=lambda x: x[0].lower())]
        await message.reply("📦 **Caixinhas:**\n" + "\n".join(linhas) + f"\n\nTotal nas caixinhas: **{fmt_brl(total)}**")
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
        
        # se vier "caixinha a emergencia", remove preposições iniciais comuns
        name = re.sub(r'^(a|a\s+|para\s+|pra\s+|na\s+|no\s+|da\s+|do\s+)\s+', '', name).strip()

        key = next((k for k in pockets.keys() if k.lower() == name.lower()), None)
        if not key:
            await message.reply(f"Não achei essa caixinha: **{name}**. Use: `criar caixinha {name}`")
            return

        # saldo suficiente na conta
        store.setdefault("conta", 0.0)
        if store["conta"] < amount:
            await message.reply(f"Saldo insuficiente na conta. Conta: {fmt_brl(store['conta'])}")
            return

        # move dinheiro: conta -> caixinha
        store["conta"] -= amount
        pockets[key] = float(pockets.get(key, 0.0)) + amount

        launches = get_user_launches(message.author.id)
        l = registrar_lancamento(
            launches=launches,
            launch_id=LAUNCH_ID,
            tipo="deposito_caixinha",
            valor=amount,
            alvo=key,
            nota=text,
            delta_conta=-amount,
        )

        await message.reply(
            f"✅ Depósito na caixinha **{key}**: +{fmt_brl(amount)}\n"
            f"🏦 Conta: {fmt_brl(store['conta'])} • 📦 Caixinha: {fmt_brl(pockets[key])}\n"
            f"ID: **#{l['id']}**"
        )
        return
    
    # sacar/retirar/resgatar X da caixinha Y (CAIXINHA -> CONTA)
    if any(w in t for w in ["retirei", "retirar", "sacar", "saquei", "resgatei", "resgatar"]) and "caixinha" in t:
        amount = parse_money(text)
        if amount is None:
            await message.reply("Qual valor? Ex: `retirei 200 da caixinha viagem`")
            return

        # tenta achar o nome depois de "caixinha"
        parts = t.split("caixinha", 1)
        name = parts[1].strip() if len(parts) > 1 else ""
        # remove preposições comuns se vierem grudadas
        name = re.sub(r'^(da|do|de|na|no|para|pra)\s+', '', name).strip()

        if not name:
            await message.reply("De qual caixinha? Ex: `retirei 200 da caixinha viagem`")
            return

        key = next((k for k in pockets.keys() if k.lower() == name.lower()), None)
        if not key:
            await message.reply(f"Não achei essa caixinha: **{name}**. Use: `criar caixinha {name}`")
            return

        if pockets[key] < amount:
            await message.reply(f"Saldo insuficiente na caixinha **{key}**. Caixinha: R$ {pockets[key]:.2f}")
            return

        # ✅ move dinheiro
        pockets[key] -= amount
        store["conta"] += amount

        launches = get_user_launches(message.author.id)
        launches.append({
            "id": next(LAUNCH_ID),
            "tipo": "saque_caixinha",
            "valor": float(amount),
            "alvo": key,
            "nota": None,
            "criado_em": datetime.now().isoformat(timespec="seconds"),
            "efeitos": {
                "delta_conta": +float(amount),
                "delta_pocket": {"nome": key, "delta": -float(amount)},
                "delta_invest": None,
                "create_pocket": None,
                "create_investment": None
            }
        })

        await message.reply(
            f"📤 Caixinha **{key}**: -R$ {amount:.2f}\n"
            f"🏦 Conta: R$ {store['conta']:.2f} • 📦 Caixinha: R$ {pockets[key]:.2f}\n"
            f"ID: #{launches[-1]['id']}"
        )
        return

    
    # =========================
    # Listar caixinhas
    # =========================
    if t in ["listar caixinhas", "lista caixinhas", "caixinhas"]:
        if not pockets:
            await message.reply("Você ainda não tem caixinhas. Use: `criar caixinha <nome>`")
            return

        lines = []
        total = 0.0
        for nome, saldo in pockets.items():
            lines.append(f"📦 **{nome}**: R$ {saldo:.2f}")
            total += float(saldo)

        await message.reply("📦 **Suas caixinhas:**\n" + "\n".join(lines) + f"\n\nTotal em caixinhas: R$ {total:.2f}")
        return
    
    # excluir caixinha
    if t.startswith("excluir caixinha") or t.startswith("apagar caixinha") or t.startswith("remover caixinha"):
        parts = text.split("caixinha", 1)
        name = parts[1].strip() if len(parts) > 1 else ""

        if not name:
            await message.reply("Qual caixinha você quer excluir? Ex: `excluir caixinha viagem`")
            return

        key = next((k for k in pockets.keys() if k.lower() == name.lower()), None)
        if not key:
            await message.reply(f"Não achei essa caixinha: **{name}**")
            return

        if pockets[key] != 0:
            await message.reply(f"⚠️ Não posso excluir a caixinha **{key}** porque o saldo não é zero (R$ {pockets[key]:.2f}).")
            return

        # remove
        del pockets[key]

        # registra lançamento
        launches = get_user_launches(message.author.id)
        launches.append({
            "id": next(LAUNCH_ID),
            "tipo": "delete_pocket",
            "valor": None,
            "alvo": key,
            "nota": None,
            "criado_em": datetime.now().isoformat(timespec="seconds")
        })

        await message.reply(f"🗑️ Caixinha **{key}** excluída com sucesso. (ID: #{launches[-1]['id']})")
        return



   # Gasto/Receita natural (ex: "gastei 35 no ifood", "recebi 2500 salario")
    parsed = parse_receita_despesa_natural(text)
    if parsed:
        launches = get_user_launches(message.author.id)

        if parsed["tipo"] == "despesa":
            store["conta"] -= parsed["valor"]
            delta_conta = -float(parsed["valor"])
            emoji = "💸"
        else:
            store["conta"] += parsed["valor"]
            delta_conta = +float(parsed["valor"])
            emoji = "💰"

        new_id = next(LAUNCH_ID)

        launches.append({
            "id": new_id,
            "tipo": parsed["tipo"],  # <-- despesa OU receita
            "valor": float(parsed["valor"]),
            "alvo": parsed["categoria"],
            "nota": parsed["nota"],
            "criado_em": datetime.now().isoformat(timespec="seconds"),
            "efeitos": {
                "delta_conta": delta_conta  # <-- usa o delta correto
            }
        })

        await message.reply(
            f"{emoji} **{parsed['tipo'].capitalize()} registrada**: R$ {parsed['valor']:.2f}\n"
            f"🏷️ Categoria: {parsed['categoria']}\n"
            f"🏦 Conta: R$ {store['conta']:.2f}\n"
            f"ID: #{new_id}"
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

        if name in pockets:
            await message.reply(f"ℹ️ A caixinha **{name}** já existe.")
            return

        pockets[name] = 0.0

        launches = get_user_launches(message.author.id)
        l = registrar_lancamento(
            launches=launches,
            launch_id=LAUNCH_ID,
            tipo="criar_caixinha",
            valor=0.0,
            alvo=name,
            nota=text,
            delta_conta=0.0,
        )

        await message.reply(f"✅ Caixinha criada: **{name}** (ID: **#{l['id']}**)")
        return



  # criar investimento (aceita taxa ao dia / ao mês / ao ano)
    if t.startswith("criar investimento"):
        parts = text.split("criar investimento", 1)
        rest = parts[1].strip() if len(parts) > 1 else ""
        if not rest:
            await message.reply("Use: `criar investimento <nome> <taxa>% ao dia|ao mês|ao ano`")
            return

        # extrai taxa: aceita 1% / 1,1% / 1.1% e "ao dia"/"ao mes"/"ao mês"/"ao ano"
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
        elif "ano" in period_raw:
            period = "yearly"
        else:
            period = "monthly"

        # nome = texto sem a parte da taxa (remove só o trecho encontrado)
        name = (rest[:m.start()] + rest[m.end():]).strip(" -–—")
        if not name:
            await message.reply("Me diga o nome do investimento também. Ex: `criar investimento CDB 1% ao mês`")
            return

        if name in investments:
            await message.reply(f"ℹ️ O investimento **{name}** já existe.")
            return

        investments[name] = {
            "balance": 0.0,
            "rate": rate,          # taxa do período (dia/mês/ano)
            "period": period,      # 'daily'|'monthly'|'yearly'
            "last_date": date.today()
        }

        launches = get_user_launches(message.author.id)
        launches.append({
            "id": next(LAUNCH_ID),
            "tipo": "create_investment",
            "valor": None,
            "alvo": name,
            "nota": f"taxa={rate} periodo={period}",
            "criado_em": datetime.now().isoformat(timespec="seconds")
        })

        taxa_pct = rate * 100
        if period == "daily":
            periodo_str = "ao dia"
        elif period == "monthly":
            periodo_str = "ao mês"
        else:
            periodo_str = "ao ano"

        await message.reply(
            f"✅ Investimento criado: **{name}** ({taxa_pct:.4g}% {periodo_str}) (ID: #{launches[-1]['id']})"
        )
        return


    
    # adicionar/colocar/por X na caixinha Y
    # if any(w in t for w in ["adicionar", "colocar", "por", "depositar"]) and "caixinha" in t:
    #     amount = parse_money(text)
    #     if amount is None:
    #         await message.reply("Qual valor? Ex: `adicionar 200 na caixinha viagem`")
    #         return

    #     # tenta achar o nome depois de "caixinha"
    #     parts = t.split("caixinha", 1)
    #     name = parts[1].strip() if len(parts) > 1 else ""
    #     if not name:
    #         await message.reply("Pra qual caixinha? Ex: `adicionar 200 na caixinha viagem`")
    #         return

    #     # acha a caixinha por case-insensitive
    #     key = next((k for k in pockets.keys() if k.lower() == name.lower()), None)
    #     if not key:
    #         await message.reply(f"Não achei essa caixinha: **{name}**. Use: `criar caixinha {name}`")
    #         return

    #     # ✅ garante saldo suficiente
    #     if store["conta"] < amount:
    #         await message.reply(f"Saldo insuficiente na conta. Conta: R$ {store['conta']:.2f}")
    #         return

    #     # ✅ move dinheiro
    #     store["conta"] -= amount
    #     pockets[key] += amount

    #     launches = get_user_launches(message.author.id)
    #     launches.append({
    #         "id": next(LAUNCH_ID),
    #         "type": "pocket_deposit",
    #         "amount": amount,
    #         "target": key,
    #         "created_at": datetime.now().isoformat(timespec="seconds")
    #         })
        
    #     await message.reply(
    #     f"✅ Caixinha **{key}**: +R$ {amount:.2f}\n"
    #     f"🏦 Conta: R$ {store['conta']:.2f} • 📦 Caixinha: R$ {pockets[key]:.2f}")

    #     return

    # depósito natural em caixinha (ex: "coloquei 300 na emergencia")
    amount, pocket_name = parse_pocket_deposit_natural(text)
    if amount is not None and pocket_name:
        key = next((k for k in pockets.keys() if k.lower() == pocket_name.lower()), None)
        if not key:
            await message.reply(f"Não achei essa caixinha: **{pocket_name}**. Use: `criar caixinha {pocket_name}`")
            return

        pockets[key] += amount

        launches = get_user_launches(message.author.id)
        launches.append({
            "id": next(LAUNCH_ID),
            "type": "pocket_deposit",
            "amount": amount,
            "target": key,
            "created_at": datetime.now().isoformat(timespec="seconds")
        })

        await message.reply(f"✅ Caixinha **{key}**: +R$ {amount:.2f}. Saldo: **R$ {pockets[key]:.2f}**")
        return

    # transferir para caixinha
    # if "transferi" in t and "caixinha" in t:
    #     amount = parse_money(text)
    #     if amount is None:
    #         await message.reply("Qual valor você transferiu?")
    #         return
    #     m = re.search(r'caixinha (.+)$', t)
    #     if not m:
    #         await message.reply("Pra qual caixinha? Ex: 'transferi 200 para caixinha viagem'")
    #         return
    #     name = text.lower().split("caixinha", 1)[1].strip()

    #     key = next((k for k in pockets.keys() if k.lower() == name.lower()), None)
    #     if not key:
    #         await message.reply(f"Não achei essa caixinha: **{name}**. Use: 'criar caixinha {name}'")
    #         return
    #     pockets[key] += amount

    #     launches = get_user_launches(message.author.id)
    #     launches.append({
    #         "id": next(LAUNCH_ID),
    #         "type": "pocket_deposit",
    #         "amount": amount,
    #         "target": key,
    #         "created_at": datetime.now().isoformat(timespec="seconds")
    #     })

    #     await message.reply(f"✅ Caixinha **{key}**: +R$ {amount:.2f}. Saldo: **R$ {pockets[key]:.2f}**")
    #     return

    # aplicar/aporte no investimento (debita conta corrente)
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
            # pega o que vem depois da palavra investimento
            parts = re.split(r'\binvestimento\b', text, flags=re.I, maxsplit=1)
            name = parts[1].strip() if len(parts) > 1 else None

        # fallback: "apliquei 500 cdb nubank" (sem investimento)
        if not name:
            # remove o verbo do começo
            tmp = re.sub(r'^(apliquei|aplicar|aportei|aporte)\b', '', text, flags=re.I).strip()
            # remove o valor (primeiro número que parecer dinheiro)
            tmp = re.sub(r'\b\d[\d\.\,]*\b', '', tmp, count=1).strip()
            name = tmp.strip(" -–—") or None

        if not name:
            await message.reply("Em qual investimento? Ex: `apliquei 200 no investimento cdb_nubank`")
            return

        # acha investimento (case-insensitive)
        key = next((k for k in investments.keys() if k.lower() == name.lower()), None)
        if not key:
            await message.reply(f"Não achei esse investimento: **{name}**. Use: `criar investimento {name} 1% ao mês`")
            return

        # saldo suficiente
        if store["conta"] < amount:
            await message.reply(f"Saldo insuficiente na conta. Conta: R$ {store['conta']:.2f}")
            return

        # atualiza juros antes de mexer
        accrue_investment(investments[key])

        # move dinheiro (CONTA -> INVESTIMENTO)
        store["conta"] -= float(amount)
        investments[key]["balance"] += float(amount)

        # registra lançamento
        launches = get_user_launches(message.author.id)
        launches.append({
            "id": next(LAUNCH_ID),
            "tipo": "aporte_investimento",
            "valor": float(amount),
            "alvo": key,
            "nota": None,
            "criado_em": datetime.now().isoformat(timespec="seconds"),
            "efeitos": {
                "delta_conta": -float(amount),
                "delta_pocket": None,
                "delta_invest": {"nome": key, "delta": +float(amount)},
                "create_pocket": None,
                "create_investment": None
            }
        })

        await message.reply(
            f"✅ Aporte em **{key}**: +R$ {float(amount):.2f}. Saldo: **R$ {investments[key]['balance']:.2f}**\n"
            f"🏦 Conta: R$ {store['conta']:.2f}\n"
            f"ID: #{launches[-1]['id']}"
        )
        return
    
    # resgatar/retirar dinheiro do investimento (credita conta corrente)
    # Aceita:
    # - "resgatei 200 do investimento cdb_nubank"
    # - "retirei 200 do investimento cdb_nubank"
    # - "resgatar 200 investimento cdb_nubank"
    # - "resgatei 200 cdb_nubank" (sem falar investimento)
    if any(w in t for w in ["resgatei", "resgatar", "resgate", "retirei", "retirar", "saquei", "sacar"]) and (
        "investimento" in t or "do investimento" in t or "da investimento" in t or True
    ):
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

        # acha investimento (case-insensitive)
        key = next((k for k in investments.keys() if k.lower() == name.lower()), None)
        if not key:
            await message.reply(f"Não achei esse investimento: **{name}**. Use: `criar investimento {name} 1% ao mês`")
            return

        # atualiza juros antes de mexer
        accrue_investment(investments[key])

        # saldo suficiente no investimento
        if investments[key]["balance"] < float(amount):
            await message.reply(f"Saldo insuficiente no investimento **{key}**. Saldo: R$ {investments[key]['balance']:.2f}")
            return

        # move dinheiro (INVESTIMENTO -> CONTA)
        investments[key]["balance"] -= float(amount)
        store["conta"] += float(amount)

        # registra lançamento
        launches = get_user_launches(message.author.id)
        launches.append({
            "id": next(LAUNCH_ID),
            "tipo": "resgate_investimento",
            "valor": float(amount),
            "alvo": key,
            "nota": None,
            "criado_em": datetime.now().isoformat(timespec="seconds"),
            "efeitos": {
                "delta_conta": +float(amount),
                "delta_pocket": None,
                "delta_invest": {"nome": key, "delta": -float(amount)},
                "create_pocket": None,
                "create_investment": None
            }
        })

        await message.reply(
            f"💸 Resgate de **{key}**: -R$ {float(amount):.2f}. Saldo: **R$ {investments[key]['balance']:.2f}**\n"
            f"🏦 Conta: R$ {store['conta']:.2f}\n"
            f"ID: #{launches[-1]['id']}"
        )
        return




    # saldo caixinhas
    if t == "saldo caixinhas":
        if not pockets:
            await message.reply("Você não tem caixinhas ainda. Use: 'criar caixinha viagem'")
            return
        lines = "\n".join([f"- **{k}**: R$ {v:.2f}" for k, v in pockets.items()])
        await message.reply("💰 **Caixinhas:**\n" + lines)
        return

    # saldo investimentos
    if t == "saldo investimentos":
        if not investments:
            await message.reply("Você não tem investimentos ainda. Use: 'criar investimento CDB 1,1% ao mês'")
            return
        for inv in investments.values():
            accrue_investment(inv)
        lines = "\n".join([f"- **{k}**: R$ {v['balance']:.2f}" for k, v in investments.items()])
        await message.reply("📈 **Investimentos:**\n" + lines)
        return
    

    # listar investimentos
    if t in ["listar investimentos", "lista investimentos", "investimentos", "meus investimentos"]:
        if not investments:
            await message.reply("Você ainda não tem investimentos.")
            return

        lines = ["📈 **Seus investimentos:**"]
        for name, inv in investments.items():
            rate = inv.get("rate", 0.0) * 100
            period = inv.get("period", "monthly")
            period_str = "ao dia" if period == "daily" else ("ao mês" if period == "monthly" else "ao ano")
            bal = inv.get("balance", 0.0)
            lines.append(f"• **{name}** — {rate:.4g}% {period_str} — saldo: R$ {bal:.2f}")

        await message.reply("\n".join(lines))
        return

    
    # listar lancamentos
    if t in ["listar lancamentos", "listar lançamentos", "ultimos lancamentos", "últimos lançamentos"]:
        launches = get_user_launches(message.author.id)
        if not launches:
            await message.reply("Você ainda não tem lançamentos.")
            return

        last = launches[-10:]
        lines = []
        for l in last:
            tipo = l.get("tipo", "-")
            valor = l.get("valor", None)
            alvo = l.get("alvo", "-")
            criado = l.get("criado_em", "-")
            nota = l.get("nota")

            # limpa nota feia do create_investment
            if tipo == "create_investment" and nota and "taxa=" in nota:
                # tenta extrair só taxa/periodo de forma humana
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
            lines.append(f"#{l.get('id','?')} • {tipo} • {valor_str} • {alvo}{nota_part} • {criado}")

        await message.reply("🧾 **Últimos lançamentos:**\n" + "\n".join(lines))
        return

    

    # =========================
    # Apagar lançamento pelo ID (robusto)s
    # =========================
    if t.startswith("apagar") or t.startswith("remover"):
        m = re.search(r'(\d+)', t)
        if not m:
            await message.reply("Me diga o ID do lançamento. Ex: `apagar 3`")
            return

        launch_id = int(m.group(1))
        launches = get_user_launches(message.author.id)

        idx = next((i for i, l in enumerate(launches) if l.get("id") == launch_id), None)
        if idx is None:
            await message.reply(f"Não achei lançamento com ID {launch_id}.")
            return

        removed = launches.pop(idx)

        # ---- normaliza campos (novo e antigo) ----
        tipo = removed.get("tipo") or removed.get("type")
        valor = removed.get("valor") if "valor" in removed else removed.get("amount")
        alvo  = removed.get("alvo")  or removed.get("target")

        if valor is None:
            valor = 0.0
        valor = float(valor)

        # ---- reverter efeitos ----
        if tipo in ["despesa", "expense"]:
            store["conta"] += valor

        elif tipo in ["receita", "income"]:
            store["conta"] -= valor

        elif tipo == "pocket_deposit":
            # depósito em caixinha: desfazer = tirar da caixinha e devolver na conta
            if alvo in pockets:
                pockets[alvo] -= valor
            store["conta"] += valor

        elif tipo == "pocket_withdraw":
            # saque da caixinha: desfazer = devolver na caixinha e tirar da conta
            if alvo in pockets:
                pockets[alvo] += valor
            store["conta"] -= valor

        elif tipo == "investment_apply":
            if alvo in investments:
                investments[alvo]["balance"] -= valor
            store["conta"] += valor

        elif tipo == "investment_withdraw":
            if alvo in investments:
                investments[alvo]["balance"] += valor
            store["conta"] -= valor

        elif tipo == "create_investment":
            # apaga o investimento criado (se existir)
            if alvo in investments:
                del investments[alvo]

        elif tipo == "create_pocket":
            if alvo in pockets:
                del pockets[alvo]

        await message.reply(f"🗑️ Lançamento #{launch_id} removido e saldos ajustados.")
        return

    # comando para desfazer a ultima acao
    if t in ["desfazer", "undo", "voltar", "excluir"]:
        launches = get_user_launches(message.author.id)

        if not launches:
            await message.reply("Você não tem lançamentos para desfazer.")
            return

        last = launches[-1]
        tipo = last.get("tipo")
        efeitos = last.get("efeitos") or {}

        # 1) reverter create_investment (só se saldo == 0)
        if tipo == "create_investment":
            inv_name = last.get("alvo")
            if inv_name in investments and investments[inv_name]["balance"] != 0:
                await message.reply(f"⚠️ Não consigo desfazer: o investimento **{inv_name}** não está zerado (R$ {investments[inv_name]['balance']:.2f}).")
                return
            if inv_name in investments:
                del investments[inv_name]

        # 2) reverter create_pocket (só se saldo == 0)
        elif tipo == "create_pocket":
            pocket = last.get("alvo")
            if pocket in pockets and pockets[pocket] != 0:
                await message.reply(f"⚠️ Não consigo desfazer: a caixinha **{pocket}** não está zerada (R$ {pockets[pocket]:.2f}).")
                return
            if pocket in pockets:
                del pockets[pocket]

        # 3) reverter ações com efeitos (conta/caixinha/invest)
        else:
            # conta
            store["conta"] -= float(efeitos.get("delta_conta", 0.0))

            # pocket
            dp = efeitos.get("delta_pocket")
            if dp:
                nome = dp["nome"]
                if nome in pockets:
                    pockets[nome] -= float(dp["delta"])

            # invest
            di = efeitos.get("delta_invest")
            if di:
                nome = di["nome"]
                if nome in investments:
                    investments[nome]["balance"] -= float(di["delta"])

        # ✅ SÓ AGORA remove o lançamento
        removed = launches.pop()

        await message.reply(
            f"↩️ Desfeito: **#{removed.get('id')}** • {removed.get('tipo')} • "
            f"{'R$ '+format(float(removed.get('valor', 0.0)), '.2f') if removed.get('valor') is not None else '-'} • "
            f"{removed.get('alvo','-')}"
        )
        return

    
        
    # comando para ver saldo da conta
    if t in ["saldo", "saldo conta", "saldo da conta", "conta", "saldo geral"]:
        await message.reply(f"🏦 **Conta Corrente:** R$ {store['conta']:.2f}")
        return



    # fallback
    await message.reply("❓ **Não entendi seu comando. Tente um destes exemplos:**\n\n" + HELP_TEXT)

# --------- run ---------
if __name__ == "__main__":
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN não definido.")

    # Só inicializa DB se tiver DATABASE_URL (no Railway terá)
    if os.getenv("DATABASE_URL"):
        init_db()
    else:
        print("⚠️ DATABASE_URL não definido — rodando sem Postgres (modo local).")

    bot.run(token)


