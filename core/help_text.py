# core/help_text.py
from __future__ import annotations
import re
from typing import Literal, Tuple

Platform = Literal["discord", "whatsapp"]

HELP_TEXT_SHORT = (
    "❓ **Não entendi esse comando.**\n"
    "Digite `ajuda` para ver os comandos.\n"
    "Exemplos:\n"
    "• `gastei 50 mercado`\n"
    "• `recebi 1000 salario`\n"
    "• `saldo`\n"
)

# Fonte única de verdade: todas as seções de ajuda (conteúdo)
HELP_SECTIONS: dict[str, str] = {
    "start": (
        "👋 **Comece aqui**\n"
        "• `tutorial` → guia rápido\n"
        "• `saldo`\n"
        "• `gastei 50 mercado`\n"
        "• `recebi 1000 salario`\n"
        "• `importar ofx` + anexo\n"
        "\n"
        "Dica: digite `ajuda` para abrir o menu completo."
    ),
    "tutorial": (
        "🚀 **Tutorial rápido (1–2 min)**\n\n"
        "1) **Registre o básico**\n"
        "• `recebi 1000 salario`\n"
        "• `gastei 50 mercado`\n"
        "• `saldo`\n"
        "• `gastos` ou `meus gastos` → últimos lançamentos\n\n"
        "2) **Importe seu extrato (OFX)**\n"
        "• Envie `importar ofx` + anexo `.ofx`\n"
        "• Duplicadas são ignoradas\n\n"
        "3) **Cartão de crédito**\n"
        "• `criar cartao Nubank fecha 10 vence 17`\n"
        "• `definir limite Nubank 5000` → define o limite\n"
        "• `credito 150 mercado` → compra no crédito\n"
        "• `fatura Nubank` → saldo + uso do limite\n"
        "• `pagar fatura Nubank com saldo` → paga usando seu saldo\n\n"
        "4) **Caixinhas**\n"
        "• `criar caixinha viagem`\n"
        "• `coloquei 300 na caixinha viagem`\n\n"
        "5) **Investimentos**\n"
        "• `criar investimento CDB 110% CDI`\n"
        "• `apliquei 200 no investimento CDB`\n\n"
        "6) **Dashboard**\n"
        "• `dashboard`\n"
    ),
    "ofx": (
    "🧾 **Importar extrato (OFX)**\n"
    "• Envie: `importar ofx` + anexo `.ofx`\n"
    "• Pode importar de novo — duplicadas são ignoradas\n"
    "• O saldo final vem do `LEDGERBAL` do OFX\n"
    "\n"
    "📌 **Categorias no OFX**\n"
    "• Se o OFX vier como \"Outros\", o bot tenta aplicar suas **regras** (comando `linkar ...`)\n"
    "• Se não houver correspondência, fica em \"Outros\".\n"
    ),
    "cc": (
        "🏦 **Conta corrente**\n"
        "• `saldo`\n"
        "• `listar lançamentos`\n"
        "• `apagar 3`\n"
        "• `desfazer`\n"
    ),
    "credit": (
        "💳 **Cartões, Crédito e Parcelas**\n\n"
        "📌 *Cadastrar cartão:*\n"
        "• `criar cartao Nubank fecha 10 vence 17`\n"
        "• `cartoes` → lista seus cartões\n"
        "• `padrao Nubank` → define o cartão principal\n\n"
        "💰 *Limite de crédito:*\n"
        "• `definir limite Nubank 5000` → define o limite\n"
        "• `limite Nubank` ou `ver limite` → uso e disponível\n"
        "• `pagar fatura Nubank com saldo` → paga a fatura usando seu saldo da conta\n\n"
        "💸 *Compras no crédito:*\n"
        "• `credito 150 mercado` → compra no cartão padrão\n"
        "• `credito Nubank 150 mercado` → em cartão específico\n\n"
        "📆 *Parcelamento:*\n"
        "• `parcelar 600 em 3x no cartao Nubank`\n"
        "• `parcelei 300 em 6x no cartao Nubank`\n\n"
        "📊 *Faturas:*\n"
        "• `fatura Nubank` → saldo + uso do limite\n"
        "• `pagar fatura Nubank 1200` → registra pagamento parcial\n"
        "• `pagar fatura Nubank com saldo` → paga tudo usando o saldo\n\n"
        "🗓️ *Consultas:*\n"
        "• `meu Nubank fecha quando`\n"
        "• `meu Nubank vence quando`\n"
        "• `qual meu cartao principal`\n"
    ),
    "categories": (
    "🏷️ **Categorias & Regras (auto-categorização)**\n"
    "• `categorias` → lista suas categorias\n"
    "• `criar categoria mercado`\n"
    "• `linkar ifood alimentacao` → sempre que aparecer \"ifood\", vira \"alimentacao\"\n"
    "• `listar regras` → mostra palavras-chave cadastradas\n"
    "• `apagar regra 3` → remove a regra (use o ID retornado)\n"
    "\n"
    "Dica: As regras ajudam MUITO no OFX quando vem tudo como \"Outros\"."
    ),
    "pockets": (
        "📦 **Caixinhas**\n"
        "• `criar caixinha viagem`\n"
        "• `coloquei 300 na caixinha viagem`\n"
        "• `retirei 100 da caixinha viagem`\n"
        "• `saldo caixinhas`\n"
        "• `listar caixinhas`\n"
        "• `excluir caixinha viagem`\n"
    ),
    "invest": (
        "📈 **Investimentos**\n"
        "• `criar investimento CDB Nubank 1% ao mês`\n"
        "• `criar investimento Tesouro 0,03% ao dia`\n"
        "• `criar investimento CDB 110% CDI`\n"
        "• `apliquei 200 no investimento CDB Nubank`\n"
        "• `retirei 100 do investimento CDB Nubank`\n"
        "• `saldo investimentos`\n"
        "• `listar investimentos`\n"
        "• `excluir investimento CDB Nubank`\n"
    ),
    "cdi": (
        "📊 **CDI**\n"
        "• `ver cdi`\n"
    ),
    "dashboard": (
        "📊 **Dashboard financeiro**\n"
        "• `dashboard` → envia o link do painel em tempo real\n"
        "• Acesse pelo navegador para ver saldo, gastos, gráficos e mais\n"
        "• O painel atualiza automaticamente a cada 30 segundos\n"
    ),
    "launches": (
        "🧾 **Lançamentos (histórico)**\n"
        "• `gastos` ou `meus gastos` → últimos 10 lançamentos\n"
        "• `despesas` ou `extrato` → mesmo que acima\n"
        "• `gastos hoje` / `gastos ontem` → por dia específico\n"
        "• `listar lançamentos` → histórico com ID\n"
        "• `apagar 228` → apaga pelo ID\n"
        "• `desfazer` → desfaz o último lançamento\n"
    ),
    "confirm": (
        "⚠️ **Confirmações**\n"
        "• `sim` → confirma ações pendentes\n"
        "• `nao` → cancela\n"
    ),
}

TITLE_MAP: dict[str, str] = {
    "start": "Ajuda — Bot Financeiro",
    "tutorial": "Tutorial",
    "ofx": "OFX",
    "cc": "Conta corrente",
    "credit": "Cartões & Crédito",
    "categories": "Categorias & Regras",
    "pockets": "Caixinhas",
    "invest": "Investimentos",
    "cdi": "CDI",
    "dashboard": "Dashboard",
    "launches": "Lançamentos",
    "confirm": "Confirmações",
}

# Ordem e metadados do menu (Discord dropdown e também serve como "índice")
HELP_ORDER: list[tuple[str, str, str]] = [
    ("start", "Começar (visão geral)", "👋"),
    ("tutorial", "Tutorial", "🚀"),
    ("ofx", "OFX (importar extrato)", "🧾"),
    ("cc", "Conta corrente", "🏦"),
    ("credit", "Cartões & Crédito", "💳"),
    ("categories", "Categorias & Regras", "🏷️"),
    ("pockets", "Caixinhas", "📦"),
    ("invest", "Investimentos", "📈"),
    ("cdi", "CDI", "📊"),
    ("dashboard", "Dashboard financeiro", "📊"),
    ("launches", "Lançamentos", "🧾"),
    ("confirm", "Confirmações", "⚠️"),
]

HELP_TITLES: dict[str, str] = {
    "start": "Ajuda — Bot Financeiro",
    "tutorial": "Tutorial",
    "ofx": "OFX",
    "categories": "Categorias & Regras",
    "cc": "Conta corrente",
    "credit": "Cartões & Crédito",
    "pockets": "Caixinhas",
    "invest": "Investimentos",
    "cdi": "CDI",
    "dashboard": "Dashboard",
    "launches": "Lançamentos",
    "confirm": "Confirmações",
}

# Alias de tópicos para `ajuda <topico>` (útil no WhatsApp)
HELP_ALIASES: dict[str, str] = {
    "inicio": "start",
    "start": "start",
    "tutorial": "tutorial",
    "ofx": "ofx",
    "extrato": "ofx",
    "importar": "ofx",
    "conta": "cc",
    "cc": "cc",
    "caixinha": "pockets",
    "caixinhas": "pockets",
    "invest": "invest",
    "investimento": "invest",
    "investimentos": "invest",
    "cdi": "cdi",
    "sheet": "dashboard",
    "sheets": "dashboard",
    "dashboard": "dashboard",
    "painel": "dashboard",
    "lanc": "launches",
    "lançamentos": "launches",
    "lancamentos": "launches",
    "gastos": "launches",
    "despesas": "launches",
    "extrato": "launches",
    "historico": "launches",
    "histórico": "launches",
    "limite": "credit",
    "limites": "credit",
    "confirm": "confirm",
    "confirmacoes": "confirm",
    "confirmações": "confirm",
    "cartao": "credit",
    "cartoes": "credit",
    "credito": "credit",
    "fatura": "credit",
    "faturas": "credit",
    "parcelas": "credit",
    "parcelamento": "credit",
    "categoria": "categories",
    "categorias": "categories",
    "regras": "categories",
    "regra": "categories",
    "linkar": "categories",
    "palavras": "categories",
    "palavraschave": "categories",
    "palavra-chave": "categories",
}

# Mapeia vários nomes para a mesma seção
_SECTION_ALIASES = {
    "start": {"start", "inicio", "início", "geral", "menu"},
    "tutorial": {"tutorial", "guia"},
    "ofx": {"ofx", "extrato", "importar", "importarofx"},
    "cc": {"cc", "conta", "conta corrente", "corrente", "saldo"},
    "pockets": {"caixinhas", "caixinha", "pockets"},
    "invest": {"invest", "investimentos", "investimento"},
    "cdi": {"cdi"},
    "dashboard": {"dashboard", "painel", "sheets", "planilha", "exportar"},
    "launches": {"lancamentos", "lançamentos", "historico", "histórico", "gastos", "despesas", "extrato"},
    "confirm": {"confirm", "confirmacoes", "confirmações", "sim", "nao", "não"},
    "credit": {"cartao", "cartoes", "cartão", "cartões", "credito", "crédito", "fatura", "faturas", "parcel", "parcelamento", "parcelas", "limite", "limites"},
    "categories": {"categoria", "categorias", "regras", "regra", "linkar", "palavras", "palavra-chave", "palavras-chave"},
}

def resolve_section(text: str) -> str:
    """
    Aceita:
      - "ajuda"
      - "help"
      - "ajuda ofx"
      - "ajuda investimentos"
    Retorna uma key de HELP_SECTIONS.
    """
    t = (text or "").strip()
    if not t:
        return "start"

    tl = t.casefold()

    # caso "ajuda" puro
    if tl in {"ajuda", "help"}:
        return "start"

    # caso "ajuda <algo>"
    m = re.match(r"^(ajuda|help)\s+(.+)$", tl)
    if not m:
        return "start"

    arg = m.group(2).strip()

    # procura correspondência por aliases
    for key, names in _SECTION_ALIASES.items():
        if arg in names:
            return key

    # fallback: se o usuário digitou exatamente a key
    if arg in HELP_SECTIONS:
        return arg

    return "start"

def _to_whatsapp_md(s: str) -> str:
    # Discord usa **bold**; WhatsApp usa *bold*
    return s.replace("**", "*")

def render_help(section_key: str, platform: Platform) -> str:
    key = HELP_ALIASES.get(section_key.casefold().strip(), section_key.casefold().strip())
    txt = HELP_SECTIONS.get(key, HELP_SECTIONS["start"])
    if platform == "whatsapp":
        txt = _to_whatsapp_md(txt)
    return txt

def render_full(platform: Platform) -> str:
    # para WhatsApp / texto puro, gera um "guia completo"
    parts: list[str] = []
    for key, label, emoji in HELP_ORDER:
        parts.append(render_help(key, platform))
        parts.append("")  # linha em branco entre seções
    return "\n".join(parts).strip()

def render_section(section_key: str) -> str:
    return HELP_SECTIONS.get(section_key, HELP_SECTIONS["start"])

# Compatibilidade com imports antigos
HELP_TEXT_FULL = render_full("discord")
TUTORIAL_TEXT = render_help("tutorial", "discord")